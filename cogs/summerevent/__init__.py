"""
Summer Olympics seasonal event backbone.

This cog intentionally keeps copy and art light. Event flavor, art URLs, and shop
prices can be adjusted later without changing the storage or reward plumbing.
"""
from __future__ import annotations

import datetime as dt
import logging
import random
from typing import Optional

import discord
from discord.ext import commands, tasks

from classes.converters import IntGreaterThan, UserWithCharacter
from utils import items as loot_items
from utils.checks import has_char, is_gm
from utils.divine_familiars import (
    DIVINE_FAMILIARS,
    award_divine_shards,
    choose_familiar_key,
    get_familiar_display_name,
    resolve_familiar_key,
)
from utils.i18n import _, locale_doc


log = logging.getLogger(__name__)

EVENT_NAME = "Summer Olympic Games"
SHOP_BANNER_URL = "https://i.imgur.com/L39heMB.png"
SHOP_ICON_URL = "https://i.imgur.com/6pkjTt3.png"
MARATHON_DISTANCE_METERS = 42195
MAX_LEADERBOARD_ROWS = 10
MARATHON_METERS_PER_HOUR = 74
MINIGAME_MEDAL_GUIDE = (
    "Suggested minigame podium: 🥇 1st place, 🥈 2nd place, 🥉 3rd place.\n"
    "Use GM medal commands for exact awards per hosted game."
)

MEDAL_ALIASES = {
    "gold": "gold",
    "g": "gold",
    "silver": "silver",
    "s": "silver",
    "bronze": "bronze",
    "b": "bronze",
}

MEDAL_COLUMNS = {
    "gold": "gold_medals",
    "silver": "silver_medals",
    "bronze": "bronze_medals",
}

MEDAL_EMOJIS = {
    "gold": "🥇",
    "silver": "🥈",
    "bronze": "🥉",
}

CRATE_EXCHANGES = {
    "common": {
        "label": "Common Crate",
        "crate_column": "crates_common",
        "crate_amount": 1,
        "medal_type": "bronze",
        "medal_amount": 1,
    },
    "uncommon": {
        "label": "Uncommon Crate",
        "crate_column": "crates_uncommon",
        "crate_amount": 1,
        "medal_type": "bronze",
        "medal_amount": 5,
    },
    "rare": {
        "label": "Rare Crate",
        "crate_column": "crates_rare",
        "crate_amount": 1,
        "medal_type": "bronze",
        "medal_amount": 10,
    },
    "magic": {
        "label": "Magic Crate",
        "crate_column": "crates_magic",
        "crate_amount": 1,
        "medal_type": "silver",
        "medal_amount": 3,
    },
    "mystery": {
        "label": "Mystery Crate",
        "crate_column": "crates_mystery",
        "crate_amount": 1,
        "medal_type": "silver",
        "medal_amount": 1,
    },
    "legendary": {
        "label": "Legendary Crate",
        "crate_column": "crates_legendary",
        "crate_amount": 1,
        "medal_type": "gold",
        "medal_amount": 1,
    },
    "fortune": {
        "label": "Fortune Crate",
        "crate_column": "crates_fortune",
        "crate_amount": 1,
        "medal_type": "gold",
        "medal_amount": 3,
    },
    "divine": {
        "label": "Divine Crate",
        "crate_column": "crates_divine",
        "crate_amount": 1,
        "medal_type": "gold",
        "medal_amount": 4,
    },
    "material": {
        "label": "Material Crate",
        "crate_column": "crates_materials",
        "crate_amount": 1,
        "medal_type": "gold",
        "medal_amount": 2,
    },
}

MEDAL_EXCHANGES = {
    "bronze_to_silver": {
        "label": "20 Bronze → 1 Silver",
        "cost": {"bronze": 20},
        "reward": {"silver": 1},
    },
    "silver_to_gold": {
        "label": "10 Silver → 1 Gold",
        "cost": {"silver": 10},
        "reward": {"gold": 1},
    },
}

MEDAL_SHOP_ITEMS = {
    "mortal_exchange": {
        "label": "Mortal Exchange",
        "description": "Grants 100 gold.",
        "cost": {"bronze": 1},
        "action": "profile_update",
        "profile_updates": {"money": 100},
        "section": "Olympian Exchange",
    },
    "demigod_exchange": {
        "label": "Demi-God Exchange",
        "description": "Grants 500 XP.",
        "cost": {"silver": 1},
        "action": "profile_update",
        "profile_updates": {"xp": 500},
        "section": "Olympian Exchange",
    },
    "hades_boon": {
        "label": "Hades Boon",
        "description": "50/50 chance for 15,000 XP or 30,000 gold.",
        "cost": {"gold": 1},
        "action": "hades_boon",
        "section": "Olympian Boons",
    },
    "poseidon_boon": {
        "label": "Poseidon Boon",
        "description": "Grants 3 random loot items.",
        "cost": {"gold": 1},
        "action": "loot_items",
        "loot_amount": 3,
        "section": "Olympian Boons",
    },
    "zeus_boon": {
        "label": "Zeus' Boon",
        "description": "Creates a manual order for the event role or badge.",
        "cost": {"gold": 15},
        "action": "manual_order",
        "section": "Olympian Boons",
    },
    "random_divine_shard": {
        "label": "Random Divine Pet Blessing",
        "description": "Grants 1 random divine familiar shard.",
        "cost": {"gold": 15},
        "action": "random_divine_shard",
        "limit": 3,
        "section": "Divine Blessings",
    },
    "specific_divine_shard": {
        "label": "Specific Divine Pet Blessing",
        "description": "Grants 1 selected divine familiar shard. Use a familiar name after the amount.",
        "cost": {"gold": 25},
        "action": "specific_divine_shard",
        "limit": 1,
        "section": "Divine Blessings",
    },
    "eris_1h": {
        "label": "Eris Weapon Gift 1H",
        "description": "Random one-handed weapon, 1-100 stat.",
        "cost": {"gold": 2},
        "action": "weapon",
        "hand": "one",
        "min_stat": 1,
        "max_stat": 100,
        "section": "Weapon Gifts",
    },
    "eris_2h": {
        "label": "Eris Weapon Gift 2H",
        "description": "Random two-handed weapon, 2-200 stat.",
        "cost": {"gold": 2},
        "action": "weapon",
        "hand": "both",
        "min_stat": 2,
        "max_stat": 200,
        "section": "Weapon Gifts",
    },
    "hephaestus_1h": {
        "label": "Hephaestus Gift 1H",
        "description": "Random one-handed weapon, 70-100 stat.",
        "cost": {"gold": 10},
        "action": "weapon",
        "hand": "one",
        "min_stat": 70,
        "max_stat": 100,
        "section": "Weapon Gifts",
    },
    "hephaestus_2h": {
        "label": "Hephaestus Gift 2H",
        "description": "Random two-handed weapon, 140-200 stat.",
        "cost": {"gold": 10},
        "action": "weapon",
        "hand": "both",
        "min_stat": 140,
        "max_stat": 200,
        "section": "Weapon Gifts",
    },
    "pet_age": {
        "label": "Pet Age",
        "description": "Grants 1 Pet Age Potion.",
        "cost": {"gold": 4},
        "action": "consumable",
        "consumable_type": "pet_age_potion",
        "limit": 9,
        "section": "Possible Extras",
    },
    "pet_speed": {
        "label": "Pet Speed Growth",
        "description": "Grants 1 Pet Speed Growth Potion.",
        "cost": {"gold": 6},
        "action": "consumable",
        "consumable_type": "pet_speed_growth_potion",
        "limit": 3,
        "section": "Possible Extras",
    },
    "pet_xp": {
        "label": "Pet XP",
        "description": "Grants 1 Pet XP Potion.",
        "cost": {"gold": 12},
        "action": "consumable",
        "consumable_type": "pet_xp_potion",
        "limit": 2,
        "section": "Possible Extras",
    },
    "reset_potion": {
        "label": "Reset Potion",
        "description": "Grants 1 reset potion.",
        "cost": {"gold": 15},
        "action": "profile_update",
        "profile_updates": {"resetpotion": 1},
        "limit": 1,
        "section": "Possible Extras",
    },
    "pantheon_blessing": {
        "label": "Blessing from the Pantheon",
        "description": "Grants 1 level candy.",
        "cost": {"gold": 50},
        "action": "level_candy",
        "profile_updates": {"levelcandy": 1},
        "limit": 1,
        "section": "Possible Extras",
    },
}


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def normalize_medal_type(medal_type: str) -> Optional[str]:
    return MEDAL_ALIASES.get(str(medal_type).strip().lower())


def format_medals(row) -> str:
    if not row:
        return "🥇 0 | 🥈 0 | 🥉 0"
    return (
        f"🥇 {int(row['gold_medals'] or 0)} | "
        f"🥈 {int(row['silver_medals'] or 0)} | "
        f"🥉 {int(row['bronze_medals'] or 0)}"
    )


def format_minigame_medals(row) -> str:
    if not row:
        return "🥇 0 | 🥈 0 | 🥉 0"
    return (
        f"🥇 {int(row['minigame_gold_medals'] or 0)} | "
        f"🥈 {int(row['minigame_silver_medals'] or 0)} | "
        f"🥉 {int(row['minigame_bronze_medals'] or 0)}"
    )


def format_cost(cost: dict[str, int]) -> str:
    parts = []
    for medal_type in ("gold", "silver", "bronze"):
        amount = int(cost.get(medal_type, 0) or 0)
        if amount:
            parts.append(f"{amount} {MEDAL_EMOJIS[medal_type]}")
    return ", ".join(parts) if parts else "Free"


def format_shop_item_line(item_id: str, item: dict) -> str:
    limit = item.get("limit")
    limit_text = f" | limit {limit}" if limit else ""
    return (
        f"`{item_id}` - **{item['label']}** ({format_cost(item['cost'])}{limit_text})\n"
        f"{item['description']}"
    )


def marathon_place_label(rank: int) -> str:
    labels = {
        1: "1st",
        2: "2nd",
        3: "3rd",
        4: "4th",
        5: "5th",
    }
    return labels.get(rank, f"#{rank}")


class SummerSunShopView(discord.ui.View):
    def __init__(
        self,
        cog: "SummerOlympics",
        ctx,
        side: str = "crates",
        owner_id: Optional[int] = None,
        opener_id: Optional[int] = None,
    ):
        super().__init__(timeout=180)
        self.cog = cog
        self.ctx = ctx
        self.side = side
        self.owner_id = int(owner_id or ctx.author.id)
        self.opener_id = int(opener_id or ctx.author.id)
        self._rebuild_items()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                _("This Sun Shop menu belongs to another player."), ephemeral=True
            )
            return False
        return True

    def _rebuild_items(self) -> None:
        self.clear_items()

        crates_button = discord.ui.Button(
            label="Crates Sun Shop",
            style=discord.ButtonStyle.success
            if self.side == "crates"
            else discord.ButtonStyle.secondary,
            row=0,
        )
        medals_button = discord.ui.Button(
            label="Medals Sun Shop",
            style=discord.ButtonStyle.success
            if self.side == "medals"
            else discord.ButtonStyle.secondary,
            row=0,
        )
        crates_button.callback = self._show_crates
        medals_button.callback = self._show_medals
        self.add_item(crates_button)
        self.add_item(medals_button)

        if self.side == "crates":
            for key in ("common", "uncommon", "rare", "magic", "mystery"):
                exchange = CRATE_EXCHANGES[key]
                button = discord.ui.Button(
                    label=exchange["label"],
                    style=discord.ButtonStyle.primary,
                    row=1,
                )
                button.callback = self._crate_exchange_callback(key)
                self.add_item(button)

            for key in ("legendary", "fortune", "divine", "material"):
                exchange = CRATE_EXCHANGES[key]
                button = discord.ui.Button(
                    label=exchange["label"],
                    style=discord.ButtonStyle.primary,
                    row=2,
                )
                button.callback = self._crate_exchange_callback(key)
                self.add_item(button)

            for key in ("bronze_to_silver", "silver_to_gold"):
                button = discord.ui.Button(
                    label=MEDAL_EXCHANGES[key]["label"],
                    style=discord.ButtonStyle.primary,
                    row=3,
                )
                button.callback = self._medal_exchange_callback(key)
                self.add_item(button)
        else:
            for item_id in ("mortal_exchange", "demigod_exchange", "hades_boon", "poseidon_boon"):
                item = MEDAL_SHOP_ITEMS[item_id]
                button = discord.ui.Button(
                    label=item["label"],
                    style=discord.ButtonStyle.primary,
                    row=1,
                )
                button.callback = self._shop_buy_callback(item_id)
                self.add_item(button)

            for item_id in ("eris_1h", "eris_2h", "pet_age", "pet_speed", "pet_xp"):
                item = MEDAL_SHOP_ITEMS[item_id]
                button = discord.ui.Button(
                    label=item["label"],
                    style=discord.ButtonStyle.primary,
                    row=2,
                )
                button.callback = self._shop_buy_callback(item_id)
                self.add_item(button)

        refresh_button = discord.ui.Button(
            label="Refresh",
            style=discord.ButtonStyle.secondary,
            row=4,
        )
        refresh_button.callback = self._refresh
        self.add_item(refresh_button)

    async def _show_crates(self, interaction: discord.Interaction) -> None:
        self.side = "crates"
        self._rebuild_items()
        await interaction.response.edit_message(
            embed=await self.cog.build_shop_embed(self.owner_id, "crates"),
            view=self,
        )

    async def _show_medals(self, interaction: discord.Interaction) -> None:
        self.side = "medals"
        self._rebuild_items()
        await interaction.response.edit_message(
            embed=await self.cog.build_shop_embed(self.owner_id, "medals"),
            view=self,
        )

    async def _refresh(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            embed=await self.cog.build_shop_embed(self.owner_id, self.side),
            view=self,
        )

    def _crate_exchange_callback(self, exchange_id: str):
        async def callback(interaction: discord.Interaction) -> None:
            ok, message = await self.cog.exchange_crates_for_medals(
                self.owner_id, exchange_id, amount=1
            )
            await interaction.response.send_message(message, ephemeral=True)
            if ok and interaction.message:
                await interaction.message.edit(
                    embed=await self.cog.build_shop_embed(self.owner_id, self.side),
                    view=self,
                )

        return callback

    def _medal_exchange_callback(self, exchange_id: str):
        async def callback(interaction: discord.Interaction) -> None:
            ok, message = await self.cog.exchange_medals(
                self.owner_id, exchange_id, amount=1
            )
            await interaction.response.send_message(message, ephemeral=True)
            if ok and interaction.message:
                await interaction.message.edit(
                    embed=await self.cog.build_shop_embed(self.owner_id, self.side),
                    view=self,
                )

        return callback

    def _shop_buy_callback(self, item_id: str):
        async def callback(interaction: discord.Interaction) -> None:
            ok, message = await self.cog.buy_medal_shop_item(
                self.owner_id, item_id, amount=1
            )
            await interaction.response.send_message(message, ephemeral=True)
            if ok and interaction.message:
                await interaction.message.edit(
                    embed=await self.cog.build_shop_embed(self.owner_id, self.side),
                    view=self,
                )

        return callback


class SummerOlympics(commands.Cog):
    MARATHON_DISTANCE_METERS = MARATHON_DISTANCE_METERS
    MEDAL_EMOJIS = MEDAL_EMOJIS

    def __init__(self, bot):
        self.bot = bot
        self._init_db.start()

    def cog_unload(self):
        self._init_db.cancel()

    async def ensure_core_tables(self, conn=None) -> None:
        owns_conn = conn is None
        if owns_conn:
            conn = await self.bot.pool.acquire()
        try:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS summer_olympics_profiles (
                    "user" BIGINT PRIMARY KEY REFERENCES profile("user") ON DELETE CASCADE,
                    meters BIGINT NOT NULL DEFAULT 0,
                    marathon_finished_at TIMESTAMPTZ,
                    gold_medals BIGINT NOT NULL DEFAULT 0,
                    silver_medals BIGINT NOT NULL DEFAULT 0,
                    bronze_medals BIGINT NOT NULL DEFAULT 0,
                    minigame_gold_medals BIGINT NOT NULL DEFAULT 0,
                    minigame_silver_medals BIGINT NOT NULL DEFAULT 0,
                    minigame_bronze_medals BIGINT NOT NULL DEFAULT 0,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )
            await conn.execute(
                """
                ALTER TABLE summer_olympics_profiles
                    ADD COLUMN IF NOT EXISTS minigame_gold_medals BIGINT NOT NULL DEFAULT 0,
                    ADD COLUMN IF NOT EXISTS minigame_silver_medals BIGINT NOT NULL DEFAULT 0,
                    ADD COLUMN IF NOT EXISTS minigame_bronze_medals BIGINT NOT NULL DEFAULT 0;
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS summer_olympics_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )
            await conn.execute(
                """
                INSERT INTO summer_olympics_state (key, value)
                VALUES ('enabled', 'true')
                ON CONFLICT (key) DO NOTHING;
                """
            )
            await conn.execute(
                """
                INSERT INTO summer_olympics_state (key, value)
                VALUES ('shop_open', 'false')
                ON CONFLICT (key) DO NOTHING;
                """
            )
        finally:
            if owns_conn:
                await self.bot.pool.release(conn)

    @tasks.loop(count=1)
    async def _init_db(self):
        await self.bot.wait_until_ready()
        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                """
                ALTER TABLE profile
                    ADD COLUMN IF NOT EXISTS levelcandy BIGINT NOT NULL DEFAULT 0;
                """
            )
            await self.ensure_core_tables(conn=conn)
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS summer_olympics_profiles (
                    "user" BIGINT PRIMARY KEY REFERENCES profile("user") ON DELETE CASCADE,
                    meters BIGINT NOT NULL DEFAULT 0,
                    marathon_finished_at TIMESTAMPTZ,
                    gold_medals BIGINT NOT NULL DEFAULT 0,
                    silver_medals BIGINT NOT NULL DEFAULT 0,
                    bronze_medals BIGINT NOT NULL DEFAULT 0,
                    minigame_gold_medals BIGINT NOT NULL DEFAULT 0,
                    minigame_silver_medals BIGINT NOT NULL DEFAULT 0,
                    minigame_bronze_medals BIGINT NOT NULL DEFAULT 0,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )
            await conn.execute(
                """
                ALTER TABLE summer_olympics_profiles
                    ADD COLUMN IF NOT EXISTS minigame_gold_medals BIGINT NOT NULL DEFAULT 0,
                    ADD COLUMN IF NOT EXISTS minigame_silver_medals BIGINT NOT NULL DEFAULT 0,
                    ADD COLUMN IF NOT EXISTS minigame_bronze_medals BIGINT NOT NULL DEFAULT 0;
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS summer_olympics_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS summer_olympics_purchases (
                    "user" BIGINT NOT NULL REFERENCES profile("user") ON DELETE CASCADE,
                    item_id TEXT NOT NULL,
                    quantity BIGINT NOT NULL DEFAULT 0,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY ("user", item_id)
                );
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS summer_olympics_orders (
                    id BIGSERIAL PRIMARY KEY,
                    "user" BIGINT NOT NULL REFERENCES profile("user") ON DELETE CASCADE,
                    item_id TEXT NOT NULL,
                    quantity BIGINT NOT NULL DEFAULT 1,
                    option TEXT,
                    fulfilled BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    fulfilled_at TIMESTAMPTZ
                );
                """
            )
            await conn.execute(
                """
                INSERT INTO summer_olympics_state (key, value)
                VALUES ('enabled', 'true')
                ON CONFLICT (key) DO NOTHING;
                """
            )
            await conn.execute(
                """
                INSERT INTO summer_olympics_state (key, value)
                VALUES ('shop_open', 'false')
                ON CONFLICT (key) DO NOTHING;
                """
            )

    async def _ensure_profile(self, user_id: int, conn=None):
        query = """
            INSERT INTO summer_olympics_profiles ("user")
            SELECT $1
            WHERE EXISTS (SELECT 1 FROM profile WHERE "user" = $1)
            ON CONFLICT ("user") DO NOTHING;
        """
        if conn is not None:
            await conn.execute(query, user_id)
            return
        await self.bot.pool.execute(query, user_id)

    async def event_enabled(self, conn=None) -> bool:
        query = "SELECT value FROM summer_olympics_state WHERE key = 'enabled';"
        if conn is not None:
            value = await conn.fetchval(query)
        else:
            value = await self.bot.pool.fetchval(query)
        return str(value or "true").strip().lower() == "true"

    async def shop_open(self, conn=None) -> bool:
        query = "SELECT value FROM summer_olympics_state WHERE key = 'shop_open';"
        if conn is not None:
            value = await conn.fetchval(query)
        else:
            value = await self.bot.pool.fetchval(query)
        return str(value or "false").strip().lower() == "true"

    async def set_event_enabled(self, enabled: bool) -> None:
        await self.bot.pool.execute(
            """
            INSERT INTO summer_olympics_state (key, value)
            VALUES ('enabled', $1)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW();
            """,
            "true" if enabled else "false",
        )

    async def set_shop_open(self, open_: bool) -> None:
        await self.bot.pool.execute(
            """
            INSERT INTO summer_olympics_state (key, value)
            VALUES ('shop_open', $1)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW();
            """,
            "true" if open_ else "false",
        )

    def meters_for_adventure(self, adventure_level: int) -> int:
        return max(
            MARATHON_METERS_PER_HOUR,
            int(adventure_level) * MARATHON_METERS_PER_HOUR,
        )

    def normalize_medal_type(self, medal_type: str) -> Optional[str]:
        return normalize_medal_type(medal_type)

    async def award_adventure_meters(
        self, user_id: int, adventure_level: int, *, conn=None
    ) -> Optional[dict]:
        await self.ensure_core_tables(conn=conn)
        if not await self.event_enabled(conn=conn):
            return None

        meters_awarded = self.meters_for_adventure(adventure_level)
        owns_conn = conn is None
        if owns_conn:
            conn = await self.bot.pool.acquire()

        try:
            async with conn.transaction():
                await self._ensure_profile(user_id, conn=conn)
                before = await conn.fetchrow(
                    """
                    SELECT meters, marathon_finished_at
                    FROM summer_olympics_profiles
                    WHERE "user" = $1
                    FOR UPDATE;
                    """,
                    user_id,
                )
                if not before:
                    return None

                previous_meters = int(before["meters"] or 0)
                previous_finished_at = before["marathon_finished_at"]
                new_meters = min(
                    MARATHON_DISTANCE_METERS, previous_meters + meters_awarded
                )
                just_finished = (
                    previous_finished_at is None
                    and new_meters >= MARATHON_DISTANCE_METERS
                )
                finished_at = utcnow() if just_finished else previous_finished_at

                row = await conn.fetchrow(
                    """
                    UPDATE summer_olympics_profiles
                    SET meters = $2,
                        marathon_finished_at = COALESCE(marathon_finished_at, $3),
                        updated_at = NOW()
                    WHERE "user" = $1
                    RETURNING meters, marathon_finished_at, gold_medals, silver_medals, bronze_medals;
                    """,
                    user_id,
                    new_meters,
                    finished_at,
                )

                finish_rank = None
                if just_finished:
                    finish_rank = await conn.fetchval(
                        """
                        SELECT ranked.rank
                        FROM (
                            SELECT "user", ROW_NUMBER() OVER (ORDER BY marathon_finished_at ASC, "user" ASC) AS rank
                            FROM summer_olympics_profiles
                            WHERE marathon_finished_at IS NOT NULL
                        ) AS ranked
                        WHERE ranked."user" = $1;
                        """,
                        user_id,
                    )

            result = {
                "meters_awarded": meters_awarded,
                "meters": int(row["meters"] or 0),
                "finished": row["marathon_finished_at"] is not None,
                "just_finished": just_finished,
                "finish_rank": int(finish_rank) if finish_rank else None,
            }
            if just_finished and 1 <= int(finish_rank or 0) <= 5:
                try:
                    rank_text = marathon_place_label(int(finish_rank))
                    await self.bot.public_log(
                        f"☀️ **Summer Olympic Games:** <@{user_id}> crossed Apollo's marathon finish line in **{rank_text} place**!"
                    )
                except Exception:
                    pass
            return result
        finally:
            if owns_conn:
                await self.bot.pool.release(conn)

    async def add_medals(self, user_id: int, medal_type: str, amount: int, conn=None) -> bool:
        medal_type = normalize_medal_type(medal_type)
        if medal_type is None:
            return False
        column = MEDAL_COLUMNS[medal_type]

        query = f"""
            UPDATE summer_olympics_profiles
            SET {column} = GREATEST(0, {column} + $2), updated_at = NOW()
            WHERE "user" = $1;
        """

        if conn is not None:
            await self._ensure_profile(user_id, conn=conn)
            result = await conn.execute(query, user_id, int(amount))
        else:
            async with self.bot.pool.acquire() as local_conn:
                async with local_conn.transaction():
                    await self._ensure_profile(user_id, conn=local_conn)
                    result = await local_conn.execute(query, user_id, int(amount))
        return not result.endswith(" 0")

    async def add_minigame_medals(
        self, user_id: int, medal_type: str, amount: int, conn=None
    ) -> bool:
        medal_type = normalize_medal_type(medal_type)
        if medal_type is None:
            return False
        total_column = MEDAL_COLUMNS[medal_type]
        minigame_column = f"minigame_{medal_type}_medals"
        query = f"""
            UPDATE summer_olympics_profiles
            SET {total_column} = GREATEST(0, {total_column} + $2),
                {minigame_column} = GREATEST(0, {minigame_column} + $2),
                updated_at = NOW()
            WHERE "user" = $1;
        """

        if conn is not None:
            await self._ensure_profile(user_id, conn=conn)
            result = await conn.execute(query, user_id, int(amount))
        else:
            async with self.bot.pool.acquire() as local_conn:
                async with local_conn.transaction():
                    await self._ensure_profile(user_id, conn=local_conn)
                    result = await local_conn.execute(query, user_id, int(amount))
        return not result.endswith(" 0")

    async def get_profile(self, user_id: int):
        await self._ensure_profile(user_id)
        return await self.bot.pool.fetchrow(
            """
            SELECT sop.*, profile.name
            FROM summer_olympics_profiles sop
            JOIN profile ON profile."user" = sop."user"
            WHERE sop."user" = $1;
            """,
            user_id,
        )

    async def _spend_medals(self, conn, user_id: int, cost: dict[str, int]) -> bool:
        row = await conn.fetchrow(
            """
            SELECT gold_medals, silver_medals, bronze_medals
            FROM summer_olympics_profiles
            WHERE "user" = $1
            FOR UPDATE;
            """,
            user_id,
        )
        if not row:
            return False
        for medal_type, amount in cost.items():
            column = MEDAL_COLUMNS[medal_type]
            if int(row[column] or 0) < int(amount):
                return False

        await conn.execute(
            """
            UPDATE summer_olympics_profiles
            SET gold_medals = gold_medals - $2,
                silver_medals = silver_medals - $3,
                bronze_medals = bronze_medals - $4,
                updated_at = NOW()
            WHERE "user" = $1;
            """,
            user_id,
            int(cost.get("gold", 0) or 0),
            int(cost.get("silver", 0) or 0),
            int(cost.get("bronze", 0) or 0),
        )
        return True

    async def _grant_medal_rewards(self, conn, user_id: int, reward: dict[str, int]) -> None:
        await conn.execute(
            """
            UPDATE summer_olympics_profiles
            SET gold_medals = gold_medals + $2,
                silver_medals = silver_medals + $3,
                bronze_medals = bronze_medals + $4,
                updated_at = NOW()
            WHERE "user" = $1;
            """,
            user_id,
            int(reward.get("gold", 0) or 0),
            int(reward.get("silver", 0) or 0),
            int(reward.get("bronze", 0) or 0),
        )

    async def exchange_crates_for_medals(
        self, user_id: int, exchange_id: str, amount: int = 1
    ) -> tuple[bool, str]:
        if not await self.shop_open():
            return False, _("Apollo's Sun Shop is currently closed.")
        exchange = CRATE_EXCHANGES.get(str(exchange_id).strip().lower())
        if not exchange:
            return False, _("Unknown crate exchange.")
        amount = max(1, int(amount))
        crate_total = int(exchange["crate_amount"]) * amount
        medal_total = int(exchange["medal_amount"]) * amount
        medal_type = exchange["medal_type"]
        crate_column = exchange["crate_column"]

        async with self.bot.pool.acquire() as conn:
            async with conn.transaction():
                await self._ensure_profile(user_id, conn=conn)
                updated = await conn.fetchval(
                    f"""
                    UPDATE profile
                    SET {crate_column} = {crate_column} - $2
                    WHERE "user" = $1 AND {crate_column} >= $2
                    RETURNING {crate_column};
                    """,
                    user_id,
                    crate_total,
                )
                if updated is None:
                    return (
                        False,
                        _(
                            "You do not have enough crates for that exchange."
                        ),
                    )
                await self._grant_medal_rewards(
                    conn, user_id, {medal_type: medal_total}
                )

        return (
            True,
            _(
                "Exchanged **{crates}x {crate_label}** for **{medals} {emoji}**."
            ).format(
                crates=crate_total,
                crate_label=exchange["label"],
                medals=medal_total,
                emoji=MEDAL_EMOJIS[medal_type],
            ),
        )

    async def exchange_medals(
        self, user_id: int, exchange_id: str, amount: int = 1
    ) -> tuple[bool, str]:
        if not await self.shop_open():
            return False, _("Apollo's Sun Shop is currently closed.")
        exchange = MEDAL_EXCHANGES.get(str(exchange_id).strip().lower())
        if not exchange:
            return False, _("Unknown medal exchange.")
        amount = max(1, int(amount))
        cost = {
            medal_type: int(value) * amount
            for medal_type, value in exchange["cost"].items()
        }
        reward = {
            medal_type: int(value) * amount
            for medal_type, value in exchange["reward"].items()
        }

        async with self.bot.pool.acquire() as conn:
            async with conn.transaction():
                await self._ensure_profile(user_id, conn=conn)
                if not await self._spend_medals(conn, user_id, cost):
                    return False, _("You do not have enough medals for that exchange.")
                await self._grant_medal_rewards(conn, user_id, reward)

        return True, _("Exchange complete: **{label}** x{amount}.").format(
            label=exchange["label"], amount=amount
        )

    async def _check_purchase_limit(
        self, conn, user_id: int, item_id: str, item: dict, amount: int
    ) -> tuple[bool, str]:
        limit = item.get("limit")
        if not limit:
            return True, ""
        current = await conn.fetchval(
            """
            SELECT quantity
            FROM summer_olympics_purchases
            WHERE "user" = $1 AND item_id = $2
            FOR UPDATE;
            """,
            user_id,
            item_id,
        )
        current = int(current or 0)
        if current + amount > int(limit):
            return (
                False,
                _("Purchase limit reached for **{label}** ({current}/{limit}).").format(
                    label=item["label"],
                    current=current,
                    limit=limit,
                ),
            )
        return True, ""

    async def _record_purchase(self, conn, user_id: int, item_id: str, amount: int) -> None:
        await conn.execute(
            """
            INSERT INTO summer_olympics_purchases ("user", item_id, quantity)
            VALUES ($1, $2, $3)
            ON CONFLICT ("user", item_id)
            DO UPDATE SET quantity = summer_olympics_purchases.quantity + EXCLUDED.quantity,
                          updated_at = NOW();
            """,
            user_id,
            item_id,
            amount,
        )

    async def _grant_profile_updates(
        self, conn, user_id: int, updates: dict[str, int], amount: int
    ) -> None:
        set_fragments = []
        values = []
        for column, value in updates.items():
            set_fragments.append(
                f"{column} = COALESCE({column}, 0) + ${len(values) + 2}"
            )
            values.append(int(value) * amount)
        await conn.execute(
            f"""
            UPDATE profile
            SET {", ".join(set_fragments)}
            WHERE "user" = $1;
            """,
            user_id,
            *values,
        )

    async def _grant_consumable(
        self, conn, user_id: int, consumable_type: str, amount: int
    ) -> None:
        row = await conn.fetchrow(
            "SELECT id FROM user_consumables WHERE user_id = $1 AND consumable_type = $2;",
            user_id,
            consumable_type,
        )
        if row:
            await conn.execute(
                "UPDATE user_consumables SET quantity = quantity + $1 WHERE id = $2;",
                amount,
                row["id"],
            )
            return
        await conn.execute(
            "INSERT INTO user_consumables (user_id, consumable_type, quantity) VALUES ($1, $2, $3);",
            user_id,
            consumable_type,
            amount,
        )

    async def _grant_loot_items(self, conn, user_id: int, amount: int) -> list[str]:
        names = []
        for _ in range(amount):
            item = loot_items.get_item(adventure_level=random.randint(20, 100))
            await conn.execute(
                'INSERT INTO loot ("name", "value", "user") VALUES ($1, $2, $3);',
                item["name"],
                item["value"],
                user_id,
            )
            names.append(str(item["name"]))
        return names

    async def _grant_random_weapon(
        self,
        conn,
        user_id: int,
        hand: str,
        min_stat: int,
        max_stat: int,
    ):
        pre_min = max(1, int(min_stat))
        pre_max = max(pre_min, int(max_stat))
        if hand == "both":
            pre_min = max(1, (int(min_stat) + 1) // 2)
            pre_max = max(pre_min, int(max_stat) // 2)

        selected = None
        for _ in range(100):
            candidate = await self.bot.create_random_item(
                minstat=pre_min,
                maxstat=pre_max,
                minvalue=pre_min * 10,
                maxvalue=max(pre_min * 10, pre_max * 75),
                owner=user_id,
                insert=False,
                conn=conn,
            )
            stat = int(candidate.get("damage") or candidate.get("armor") or 0)
            is_weapon = candidate.get("type_") != "Shield"
            if hand == "both":
                matches_hand = candidate.get("hand") == "both"
            else:
                matches_hand = candidate.get("hand") in {"any", "right"}
            if is_weapon and matches_hand and min_stat <= stat <= max_stat:
                selected = candidate
                break

        if selected is None:
            selected = await self.bot.create_random_item(
                minstat=pre_min,
                maxstat=pre_max,
                minvalue=pre_min * 10,
                maxvalue=max(pre_min * 10, pre_max * 75),
                owner=user_id,
                insert=False,
                conn=conn,
            )

        return await self.bot.create_item(**selected, conn=conn)

    async def _create_manual_order(
        self, conn, user_id: int, item_id: str, amount: int, option: Optional[str]
    ) -> int:
        order_id = int(
            await conn.fetchval(
                """
                INSERT INTO summer_olympics_orders ("user", item_id, quantity, option)
                VALUES ($1, $2, $3, $4)
                RETURNING id;
                """,
                user_id,
                item_id,
                amount,
                option,
            )
        )
        item = MEDAL_SHOP_ITEMS.get(item_id, {})
        label = item.get("label", item_id)
        try:
            await self.bot.public_log(
                f"☀️ **Summer Olympics order #{order_id}**: <@{user_id}> bought **{label}** x{amount}. "
                f"Use `$summer admin fulfill {order_id}` after granting it."
            )
        except Exception:
            pass
        return order_id

    async def _apply_shop_reward(
        self,
        conn,
        user_id: int,
        item_id: str,
        item: dict,
        amount: int,
        option: Optional[str],
    ) -> str:
        action = item["action"]
        if action == "profile_update":
            await self._grant_profile_updates(
                conn, user_id, item["profile_updates"], amount
            )
            return _("Granted **{label}** x{amount}.").format(
                label=item["label"], amount=amount
            )

        if action == "level_candy":
            updated_user = await conn.fetchval(
                """
                UPDATE profile
                SET levelcandy = COALESCE(levelcandy, 0) + $2
                WHERE "user" = $1
                RETURNING "user";
                """,
                user_id,
                amount,
            )
            if updated_user is None:
                raise ValueError(_("You need a character before Apollo can grant Level Candy."))
            return _("Apollo granted **Level Candy** x{amount}. Use `$consume candy` to eat it.").format(
                amount=amount
            )

        if action == "hades_boon":
            xp_rewards = 0
            gold_rewards = 0
            for _ in range(amount):
                if random.randint(0, 1):
                    xp_rewards += 15000
                else:
                    gold_rewards += 30000
            updates = {}
            if xp_rewards:
                updates["xp"] = xp_rewards
            if gold_rewards:
                updates["money"] = gold_rewards
            await self._grant_profile_updates(conn, user_id, updates, 1)
            parts = []
            if xp_rewards:
                parts.append(f"{xp_rewards:,} XP")
            if gold_rewards:
                parts.append(f"{gold_rewards:,} gold")
            return _("Hades granted: **{reward}**.").format(
                reward=", ".join(parts)
            )

        if action == "loot_items":
            names = await self._grant_loot_items(
                conn, user_id, int(item["loot_amount"]) * amount
            )
            preview = ", ".join(names[:3])
            if len(names) > 3:
                preview += "..."
            return _("Poseidon granted loot: **{items}**.").format(items=preview)

        if action == "manual_order":
            order_id = await self._create_manual_order(
                conn, user_id, item_id, amount, option
            )
            return _(
                "Created manual fulfillment order **#{order_id}** for **{label}**."
            ).format(order_id=order_id, label=item["label"])

        if action == "random_divine_shard":
            granted = []
            for _ in range(amount):
                familiar_key = choose_familiar_key()
                total = await award_divine_shards(conn, user_id, familiar_key, 1)
                granted.append(
                    f"{get_familiar_display_name(familiar_key)} ({total})"
                )
            return _("Granted random divine shard(s): **{shards}**.").format(
                shards=", ".join(granted)
            )

        if action == "specific_divine_shard":
            familiar_key = resolve_familiar_key(option or "")
            if not familiar_key or familiar_key not in DIVINE_FAMILIARS:
                valid = ", ".join(
                    sorted(cfg["name"] for cfg in DIVINE_FAMILIARS.values())
                )
                raise ValueError(
                    _("Choose a valid familiar: {valid}").format(valid=valid)
                )
            total = await award_divine_shards(conn, user_id, familiar_key, amount)
            return _("Granted **{amount}** shard(s) for **{familiar}**. Total: **{total}**.").format(
                amount=amount,
                familiar=get_familiar_display_name(familiar_key),
                total=total,
            )

        if action == "weapon":
            names = []
            for _ in range(amount):
                weapon = await self._grant_random_weapon(
                    conn,
                    user_id,
                    item["hand"],
                    int(item["min_stat"]),
                    int(item["max_stat"]),
                )
                stat = int(weapon["damage"] or weapon["armor"] or 0)
                names.append(f"{weapon['name']} ({stat})")
            preview = ", ".join(names[:3])
            if len(names) > 3:
                preview += "..."
            return _("Granted weapon gift(s): **{items}**.").format(items=preview)

        if action == "consumable":
            await self._grant_consumable(
                conn, user_id, item["consumable_type"], amount
            )
            return _("Granted **{label}** x{amount}.").format(
                label=item["label"], amount=amount
            )

        raise ValueError(_("Unsupported shop item action."))

    async def buy_medal_shop_item(
        self, user_id: int, item_id: str, amount: int = 1, option: Optional[str] = None
    ) -> tuple[bool, str]:
        if not await self.shop_open():
            return False, _("Apollo's Sun Shop is currently closed.")
        item = MEDAL_SHOP_ITEMS.get(str(item_id).strip().lower())
        if not item:
            return False, _("Unknown medal shop item.")
        amount = max(1, int(amount))
        cost = {
            medal_type: int(value) * amount
            for medal_type, value in item["cost"].items()
        }

        try:
            async with self.bot.pool.acquire() as conn:
                async with conn.transaction():
                    await self._ensure_profile(user_id, conn=conn)
                    ok, limit_message = await self._check_purchase_limit(
                        conn, user_id, item_id, item, amount
                    )
                    if not ok:
                        return False, limit_message

                    if not await self._spend_medals(conn, user_id, cost):
                        return False, _("You do not have enough medals for that purchase.")

                    reward_message = await self._apply_shop_reward(
                        conn, user_id, item_id, item, amount, option
                    )
                    await self._record_purchase(conn, user_id, item_id, amount)
        except ValueError as exc:
            return False, str(exc)
        except Exception:
            log.exception(
                "Summer shop purchase failed: user_id=%s item_id=%s amount=%s option=%s",
                user_id,
                item_id,
                amount,
                option,
            )
            return False, _(
                "That purchase failed before medals were taken. Staff can check the bot logs for the exact error."
            )

        return True, reward_message

    async def build_shop_embed(self, user_id: int, side: str = "crates") -> discord.Embed:
        row = await self.get_profile(user_id)
        embed = discord.Embed(
            title="☀️ Apollo's Sun Shop",
            colour=discord.Color.gold(),
        )
        embed.set_thumbnail(url=SHOP_ICON_URL)
        embed.set_image(url=SHOP_BANNER_URL)
        embed.description = _("Your medals: {medals}").format(medals=format_medals(row))

        if side == "medals":
            sections = {}
            for item_id, item in MEDAL_SHOP_ITEMS.items():
                sections.setdefault(item.get("section", "Olympian Exchange"), []).append(
                    format_shop_item_line(item_id, item)
                )
            for section, lines in sections.items():
                embed.add_field(
                    name=section,
                    value="\n\n".join(lines),
                    inline=False,
                )
            valid_familiars = ", ".join(
                sorted(cfg["name"] for cfg in DIVINE_FAMILIARS.values())
            )
            embed.add_field(
                name="Specific Divine Pet Blessing",
                value=f"Use `summer shop buy specific_divine_shard 1 <familiar>`. Valid: {valid_familiars}",
                inline=False,
            )
            embed.set_footer(text="Use the buttons or `summer shop buy <item_id> [amount]`.")
            return embed

        embed.add_field(
            name="Crates for Medals",
            value="\n".join(
                f"`{exchange_id}` - {exchange['crate_amount']}x {exchange['label']} "
                f"→ {exchange['medal_amount']} {MEDAL_EMOJIS[exchange['medal_type']]}"
                for exchange_id, exchange in CRATE_EXCHANGES.items()
            ),
            inline=False,
        )
        embed.add_field(
            name="Medal Exchange",
            value="\n".join(
                f"`{exchange_id}` - {exchange['label']}"
                for exchange_id, exchange in MEDAL_EXCHANGES.items()
            ),
            inline=False,
        )
        embed.set_footer(
            text="Use buttons or `summer shop exchange <exchange_id> [amount]`."
        )
        return embed

    async def build_status_embed(self, user) -> discord.Embed:
        row = await self.get_profile(user.id)
        meters = int(row["meters"] or 0) if row else 0
        percent = min(100, meters / MARATHON_DISTANCE_METERS * 100)
        finished_at = row["marathon_finished_at"] if row else None
        embed = discord.Embed(
            title=f"☀️ {EVENT_NAME}",
            colour=discord.Color.gold(),
        )
        embed.add_field(
            name="Marathon",
            value=(
                f"**{meters:,}m / {MARATHON_DISTANCE_METERS:,}m** ({percent:.1f}%)\n"
                f"Status: **{'Finished' if finished_at else 'Running'}**"
            ),
            inline=False,
        )
        embed.add_field(name="Your Medals", value=format_medals(row), inline=False)
        embed.add_field(
            name="Minigame Medals",
            value=f"{format_minigame_medals(row)}\n{MINIGAME_MEDAL_GUIDE}",
            inline=False,
        )
        return embed

    def build_help_embed(self, prefix: str) -> discord.Embed:
        embed = discord.Embed(
            title=f"☀️ {EVENT_NAME}",
            description=(
                "Apollo has opened the sunlit stadium, mortal. Run beneath his chariot, "
                "gather medals bright enough to shame the dawn, and spend your glory before the Games fade."
            ),
            colour=discord.Color.gold(),
        )
        embed.set_thumbnail(url=SHOP_ICON_URL)
        embed.add_field(
            name="🏃 Marathon",
            value=(
                f"The road is **{MARATHON_DISTANCE_METERS:,}m** long, and every successful adventure "
                f"carries your sandals forward by **{MARATHON_METERS_PER_HOUR}m per adventure hour** "
                "before time reductions. The first flames to cross the line are remembered, but the road remains open."
            ),
            inline=False,
        )
        embed.add_field(
            name="🥇 Medals & Minigames",
            value=(
                "Win contests, answer trials, trade wisely, and Apollo will let bronze, silver, and gold "
                "find your hands. Minigame medals have their own tally, while your full medal hoard rises "
                "on the Olympic leaderboard."
            ),
            inline=False,
        )
        embed.add_field(
            name="☀️ Sun Shop",
            value=(
                "The Sun Shop has two faces. One turns crates into medals; the other turns medals into boons. "
                "When the doors are open, bring what you have earned and choose what glory buys."
            ),
            inline=False,
        )
        embed.add_field(
            name="🧩 Apollo's Enigma Hunt",
            value=(
                f"Use `{prefix}enigma start` and Apollo will whisper the first riddles into your DMs. "
                "Each victory crowns a letter. Keep them close; the final chord listens for all of them."
            ),
            inline=False,
        )
        embed.add_field(
            name="Player Commands",
            value=(
                f"`{prefix}summer status` - Your medals and marathon progress\n"
                f"`{prefix}summer marathon` - Marathon leaderboard\n"
                f"`{prefix}summer leaderboard` - Olympic medal leaderboard\n"
                f"`{prefix}summer shop` - Apollo's Sun Shop\n"
                f"`{prefix}summer shop exchange <id> [amount]` - Exchange crates or medals\n"
                f"`{prefix}summer shop buy <id> [amount]` - Buy Olympian Exchange rewards\n"
                f"`{prefix}enigma progress` - Your enigma gates and solved days"
            ),
            inline=False,
        )
        embed.add_field(
            name="Specific Divine Pet Blessing",
            value=f"`{prefix}summer shop buy specific_divine_shard 1 <familiar>`",
            inline=False,
        )
        return embed

    def build_gm_help_embed(self, prefix: str) -> discord.Embed:
        embed = discord.Embed(
            title="☀️ Summer Olympics GM Commands",
            description="Staff tools for the summer event.",
            colour=discord.Color.gold(),
        )
        embed.set_thumbnail(url=SHOP_ICON_URL)
        embed.add_field(
            name="Event Control",
            value=(
                f"`{prefix}summer admin on` - Enable adventure marathon rewards\n"
                f"`{prefix}summer admin off` - Disable adventure marathon rewards\n"
                f"`{prefix}summer admin restart` - Reset marathon, medals, minigame tally, limits, and orders\n"
                f"`{prefix}summer admin logmarathontop` - Post current marathon top 5"
            ),
            inline=False,
        )
        embed.add_field(
            name="Medals",
            value=(
                f"`{prefix}summer amm @user <amount> <gold|silver|bronze>` - Award medals\n"
                f"`{prefix}summer amms @u1 @u2 <amount> <gold|silver|bronze>` - Award medals to many\n"
                f"`{prefix}summer admin minigamemedal @user <amount> <type>` - Award minigame medals\n"
                f"`{prefix}summer admin minigamemedals @u1 @u2 <amount> <type>` - Award minigame medals to many"
            ),
            inline=False,
        )
        embed.add_field(
            name="Shop & Orders",
            value=(
                f"`{prefix}summer admin shop open|close|status` - Open or close the public Sun Shop\n"
                f"`{prefix}summer as open|close|status` - Short shop control alias\n"
                f"`{prefix}summer admin openshop [user]` - Preview/open a Sun Shop panel for yourself or a player\n"
                f"`{prefix}summer admin orders` - List open Zeus/manual orders\n"
                f"`{prefix}summer admin fulfill <order_id>` - Fulfill an order\n"
                f"`{prefix}summer af <order_id>` - Short fulfill alias"
            ),
            inline=False,
        )
        embed.add_field(
            name="Enigma",
            value=(
                f"`{prefix}load summerenigma` - Load the enigma cog if it is not loaded\n"
                f"`{prefix}enigma admin on` - Open Apollo's Enigma Hunt\n"
                f"`{prefix}enigma admin off` - Close Apollo's Enigma Hunt\n"
                f"`{prefix}enigma admin restart` - Reset all enigma progress\n"
                f"`{prefix}enigma admin reset @user` - Reset one player\n"
                f"`{prefix}enigma admin unlock @user <phase>` - Force-unlock the first day of a phase\n"
                f"`{prefix}enigma admin globalunlock <phase>` - Globally open a phase and ping the event channel\n"
                f"`{prefix}enigma admin solve @user <day>` - Mark a day solved\n"
                f"`{prefix}enigmagm` - Dedicated enigma GM panel"
            ),
            inline=False,
        )
        return embed

    @commands.group(
        name="summerolympics",
        aliases=["summer", "olympics", "so"],
        invoke_without_command=True,
        brief=_("View your Summer Olympic Games progress."),
    )
    @has_char()
    @locale_doc
    async def summerolympics(self, ctx):
        _("""View your Summer Olympic Games progress.""")
        await ctx.send(embed=self.build_help_embed(ctx.clean_prefix))

    @summerolympics.command(name="status", aliases=["profile"])
    @has_char()
    async def summer_status(self, ctx):
        await ctx.send(embed=await self.build_status_embed(ctx.author))

    @summerolympics.command(name="help")
    async def summer_help(self, ctx):
        await ctx.send(embed=self.build_help_embed(ctx.clean_prefix))

    @is_gm()
    @commands.command(name="summergm", aliases=["summeradmin"])
    async def summer_gm_help(self, ctx):
        await ctx.send(embed=self.build_gm_help_embed(ctx.clean_prefix))

    @is_gm()
    @summerolympics.command(name="amm")
    async def summer_award_medal(
        self,
        ctx,
        target: UserWithCharacter,
        amount: int,
        medal_type: str,
    ):
        normalized_medal = self.normalize_medal_type(medal_type)
        if normalized_medal is None:
            return await ctx.send("Invalid medal type. Use gold, silver, or bronze.")
        success = await self.add_medals(target.id, normalized_medal, amount)
        if not success:
            return await ctx.send("Could not update medals for that user.")
        await ctx.send(
            f"Gave **{amount}** {MEDAL_EMOJIS[normalized_medal]} medal(s) to **{target}**."
        )

    @is_gm()
    @summerolympics.command(name="amms")
    async def summer_award_medals(
        self,
        ctx,
        targets: commands.Greedy[UserWithCharacter],
        amount: int,
        medal_type: str,
    ):
        if not targets:
            return await ctx.send("No valid users with characters were provided.")
        normalized_medal = self.normalize_medal_type(medal_type)
        if normalized_medal is None:
            return await ctx.send("Invalid medal type. Use gold, silver, or bronze.")

        success_count = 0
        failed = []
        for target in targets:
            try:
                success = await self.add_medals(target.id, normalized_medal, amount)
            except Exception:
                success = False
            if success:
                success_count += 1
            else:
                failed.append(str(target))
        await ctx.send(
            f"Gave **{amount}** {MEDAL_EMOJIS[normalized_medal]} medal(s) to **{success_count}** user(s)."
        )
        if failed:
            await ctx.send(f"Failed: {', '.join(failed[:10])}")

    @is_gm()
    @summerolympics.command(name="as", aliases=["adminshop"])
    async def summer_admin_shop_alias(self, ctx, action: str = "status"):
        await self._set_or_show_shop_state(ctx, action)

    @summerolympics.command(name="leaderboard", aliases=["lb", "top"])
    async def summer_leaderboard(self, ctx):
        rows = await self.bot.pool.fetch(
            """
            SELECT sop.*, profile.name
            FROM summer_olympics_profiles sop
            JOIN profile ON profile."user" = sop."user"
            ORDER BY gold_medals DESC,
                     silver_medals DESC,
                     bronze_medals DESC,
                     (gold_medals + silver_medals + bronze_medals) DESC,
                     meters DESC
            LIMIT $1;
            """,
            MAX_LEADERBOARD_ROWS,
        )
        embed = discord.Embed(
            title="🏆 Summer Olympic Games Leaderboard",
            description=_("Ranked Olympic-style: gold, then silver, then bronze."),
            colour=discord.Color.gold(),
        )
        if not rows:
            embed.description = _("No Summer Olympic Games medals have been earned yet.")
        for index, row in enumerate(rows, start=1):
            name = row["name"] or str(row["user"])
            total = int(row["gold_medals"] or 0) + int(row["silver_medals"] or 0) + int(row["bronze_medals"] or 0)
            embed.add_field(
                name=f"#{index} {name}",
                value=(
                    f"{format_medals(row)} | Total: **{total}**\n"
                    f"Marathon: **{int(row['meters'] or 0):,}m**"
                ),
                inline=False,
            )
        await ctx.send(embed=embed)

    @summerolympics.command(name="marathon", aliases=["race"])
    async def summer_marathon(self, ctx):
        rows = await self.bot.pool.fetch(
            """
            SELECT sop.*, profile.name
            FROM summer_olympics_profiles sop
            JOIN profile ON profile."user" = sop."user"
            ORDER BY meters DESC, marathon_finished_at ASC NULLS LAST
            LIMIT $1;
            """,
            MAX_LEADERBOARD_ROWS,
        )
        embed = discord.Embed(
            title="🏃 Summer Marathon",
            description=f"Finish line: **{MARATHON_DISTANCE_METERS:,}m**",
            colour=discord.Color.gold(),
        )
        if not rows:
            embed.description = _("No runners have started yet.")
        for index, row in enumerate(rows, start=1):
            name = row["name"] or str(row["user"])
            finished = "Finished" if row["marathon_finished_at"] else "Running"
            embed.add_field(
                name=f"#{index} {name}",
                value=f"**{int(row['meters'] or 0):,}m** - {finished}",
                inline=False,
            )
        await ctx.send(embed=embed)

    @summerolympics.group(name="shop", aliases=["sunshop"], invoke_without_command=True)
    @has_char()
    async def summer_shop(self, ctx):
        if not await self.shop_open():
            return await ctx.send(
                "☀️ Apollo's Sun Shop is currently closed. Staff will open it when rewards are ready."
            )
        view = SummerSunShopView(self, ctx, side="crates")
        await ctx.send(
            embed=await self.build_shop_embed(ctx.author.id, "crates"),
            view=view,
        )

    @summer_shop.command(name="exchange")
    @has_char()
    async def summer_shop_exchange(
        self, ctx, exchange_id: str, amount: IntGreaterThan(0) = 1
    ):
        exchange_id = exchange_id.strip().lower()
        if exchange_id in CRATE_EXCHANGES:
            ok, message = await self.exchange_crates_for_medals(
                ctx.author.id, exchange_id, int(amount)
            )
        else:
            ok, message = await self.exchange_medals(ctx.author.id, exchange_id, int(amount))
        await ctx.send(message)

    @summer_shop.command(name="buy")
    @has_char()
    async def summer_shop_buy(
        self,
        ctx,
        item_id: str,
        amount: IntGreaterThan(0) = 1,
        *,
        option: Optional[str] = None,
    ):
        _ok, message = await self.buy_medal_shop_item(
            ctx.author.id, item_id.strip().lower(), int(amount), option
        )
        await ctx.send(message)

    @is_gm()
    @summerolympics.group(name="admin", aliases=["gm"], invoke_without_command=True)
    async def summer_admin(self, ctx):
        enabled = await self.event_enabled()
        shop_open = await self.shop_open()
        await ctx.send(
            f"Summer Olympics is currently **{'enabled' if enabled else 'disabled'}**.\n"
            f"Sun Shop is **{'open' if shop_open else 'closed'}**."
        )

    @summer_admin.command(name="on")
    async def summer_admin_on(self, ctx):
        await self.set_event_enabled(True)
        await ctx.send("Summer Olympics adventure rewards enabled.")

    @summer_admin.command(name="off")
    async def summer_admin_off(self, ctx):
        await self.set_event_enabled(False)
        await ctx.send("Summer Olympics adventure rewards disabled.")

    @summer_admin.command(name="restart", aliases=["reset"])
    async def summer_admin_restart(self, ctx):
        if not await ctx.confirm(
            "Reset every player's Summer marathon progress, medals, minigame tally, purchase limits, and open orders?"
        ):
            return await ctx.send("Summer Olympics restart cancelled.")

        async with self.bot.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    UPDATE summer_olympics_profiles
                    SET meters = 0,
                        marathon_finished_at = NULL,
                        gold_medals = 0,
                        silver_medals = 0,
                        bronze_medals = 0,
                        minigame_gold_medals = 0,
                        minigame_silver_medals = 0,
                        minigame_bronze_medals = 0,
                        updated_at = NOW();
                    """
                )
                await conn.execute("DELETE FROM summer_olympics_purchases;")
                await conn.execute("DELETE FROM summer_olympics_orders;")
        await ctx.send(
            "Summer Olympics marathon progress, medals, minigame tallies, purchase limits, and orders have been reset."
        )

    async def _set_or_show_shop_state(self, ctx, action: str = "status") -> None:
        normalized = str(action or "status").strip().lower()
        if normalized in {"open", "on", "enable", "enabled"}:
            await self.set_shop_open(True)
            return await ctx.send("☀️ Apollo's Sun Shop is now **open** for everyone.")
        if normalized in {"close", "closed", "off", "disable", "disabled"}:
            await self.set_shop_open(False)
            return await ctx.send("☀️ Apollo's Sun Shop is now **closed** for everyone.")
        if normalized in {"status", "state"}:
            open_ = await self.shop_open()
            return await ctx.send(
                f"☀️ Apollo's Sun Shop is currently **{'open' if open_ else 'closed'}**."
            )
        await ctx.send("Use `open`, `close`, or `status`.")

    @summer_admin.command(name="shop")
    async def summer_admin_shop_state(self, ctx, action: str = "status"):
        await self._set_or_show_shop_state(ctx, action)

    @summer_admin.command(name="openshop", aliases=["shopfor", "panel"])
    async def summer_admin_open_shop_panel(self, ctx, target: Optional[UserWithCharacter] = None):
        owner = target or ctx.author
        view = SummerSunShopView(
            self,
            ctx,
            side="crates",
            owner_id=owner.id,
            opener_id=ctx.author.id,
        )
        await ctx.send(
            content=f"Opening Apollo's Sun Shop for {owner.mention}.",
            embed=await self.build_shop_embed(owner.id, "crates"),
            view=view,
        )

    @summer_admin.command(name="minigamemedal", aliases=["mgmedal"])
    async def summer_admin_minigame_medal(
        self,
        ctx,
        target: UserWithCharacter,
        amount: int,
        medal_type: str,
    ):
        normalized_medal = self.normalize_medal_type(medal_type)
        if normalized_medal is None:
            return await ctx.send("Invalid medal type. Use gold, silver, or bronze.")

        success = await self.add_minigame_medals(target.id, normalized_medal, amount)
        if not success:
            return await ctx.send("Could not update minigame medals for that user.")

        await ctx.send(
            f"Gave **{amount}** {MEDAL_EMOJIS[normalized_medal]} minigame medal(s) to **{target}**."
        )

    @summer_admin.command(name="minigamemedals", aliases=["mgmedals"])
    async def summer_admin_minigame_medals(
        self,
        ctx,
        targets: commands.Greedy[UserWithCharacter],
        amount: int,
        medal_type: str,
    ):
        if not targets:
            return await ctx.send("No valid users with characters were provided.")

        normalized_medal = self.normalize_medal_type(medal_type)
        if normalized_medal is None:
            return await ctx.send("Invalid medal type. Use gold, silver, or bronze.")

        success_count = 0
        failed = []
        for target in targets:
            try:
                success = await self.add_minigame_medals(
                    target.id, normalized_medal, amount
                )
            except Exception:
                success = False
            if success:
                success_count += 1
            else:
                failed.append(str(target))

        await ctx.send(
            f"Gave **{amount}** {MEDAL_EMOJIS[normalized_medal]} minigame medal(s) to **{success_count}** user(s)."
        )
        if failed:
            await ctx.send(f"Failed: {', '.join(failed[:10])}")

    @summer_admin.command(name="orders")
    async def summer_admin_orders(self, ctx):
        rows = await self.bot.pool.fetch(
            """
            SELECT soo.*, profile.name
            FROM summer_olympics_orders soo
            JOIN profile ON profile."user" = soo."user"
            WHERE fulfilled = FALSE
            ORDER BY created_at ASC
            LIMIT 10;
            """
        )
        if not rows:
            return await ctx.send("No open Summer Olympics fulfillment orders.")

        embed = discord.Embed(
            title="☀️ Summer Olympics Orders",
            colour=discord.Color.gold(),
        )
        for row in rows:
            item = MEDAL_SHOP_ITEMS.get(row["item_id"], {})
            label = item.get("label", row["item_id"])
            option = f"\nOption: {row['option']}" if row["option"] else ""
            embed.add_field(
                name=f"Order #{row['id']} - {label}",
                value=(
                    f"User: **{row['name']}** (`{row['user']}`)\n"
                    f"Quantity: **{row['quantity']}**{option}"
                ),
                inline=False,
            )
        await ctx.send(embed=embed)

    async def _fulfill_order(self, order_id: int) -> tuple[bool, str]:
        row = await self.bot.pool.fetchrow(
            """
            UPDATE summer_olympics_orders
            SET fulfilled = TRUE, fulfilled_at = NOW()
            WHERE id = $1 AND fulfilled = FALSE
            RETURNING *;
            """,
            order_id,
        )
        if not row:
            return False, "No open order found with that ID."

        item = MEDAL_SHOP_ITEMS.get(row["item_id"], {})
        label = item.get("label", row["item_id"])
        try:
            user = await self.bot.fetch_user(int(row["user"]))
            await user.send(
                f"☀️ Apollo's messengers have delivered your **{label}** "
                f"(Summer Olympics order #{order_id})."
            )
            dm_text = " User was notified."
        except Exception:
            dm_text = " I could not DM the user."
        return True, f"Marked Summer Olympics order **#{order_id}** as fulfilled.{dm_text}"

    @summer_admin.command(name="fulfill")
    async def summer_admin_fulfill(self, ctx, order_id: int):
        _ok, message = await self._fulfill_order(order_id)
        await ctx.send(message)

    @is_gm()
    @summerolympics.command(name="af", aliases=["adminfulfill"])
    async def summer_admin_fulfill_alias(self, ctx, order_id: int):
        _ok, message = await self._fulfill_order(order_id)
        await ctx.send(message)

    @summer_admin.command(name="logmarathontop", aliases=["logtop", "marathonlog"])
    async def summer_admin_log_marathon_top(self, ctx):
        rows = await self.bot.pool.fetch(
            """
            SELECT sop.*, profile.name
            FROM summer_olympics_profiles sop
            JOIN profile ON profile."user" = sop."user"
            WHERE marathon_finished_at IS NOT NULL
            ORDER BY marathon_finished_at ASC, sop."user" ASC
            LIMIT 5;
            """,
        )
        if not rows:
            return await ctx.send("No marathon finishers to log yet.")

        lines = []
        for index, row in enumerate(rows, start=1):
            name = row["name"] or f"<@{row['user']}>"
            lines.append(
                f"**{marathon_place_label(index)}** - {name} (`{int(row['meters'] or 0):,}m`)"
            )
        message = "☀️ **Summer Olympic Games Marathon Top 5**\n" + "\n".join(lines)
        try:
            await self.bot.public_log(message)
            await ctx.send("Marathon top 5 logged.")
        except Exception:
            await ctx.send(message)


async def setup(bot):
    await bot.add_cog(SummerOlympics(bot))
