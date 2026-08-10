from __future__ import annotations

import datetime
import json
import random
import re
from typing import Dict, List, Optional, Tuple

import discord
from discord.ext import commands, tasks

from classes.classes import Tank
from classes.classes import from_string as class_from_string
from classes.items import ItemType
from utils.checks import has_char, is_gm, user_is_gm


UTC = datetime.timezone.utc
_DURATION_RE = re.compile(r"(\d+)\s*([smhd])", re.I)


class Agon(commands.Cog):
    """Weekly arena ladder with carryover ranking and controlled registration."""

    FREE_REROLLS_PER_DAY = 3
    DAILY_FIGHTS = 5
    REROLL_COST_DRACHMAS = 5
    MAX_SAME_OPPONENT_FIGHTS = 3
    VEILED_FINALE_HOURS = 2
    REGISTER_IMAGE_URL = "https://i.imgur.com/kXQ4wUC.png"
    AGON_PING_ROLE_ID = 1473292774496407705
    AGON_ANNOUNCE_CHANNEL_ID = 1323388505530957885
    MIN_POOL_SIZE = 10
    NPC_ID_BASE = 9_000_000_000_000_000

    # 1 = top pool
    POOL_META = {
        1: ("Chrysos", "A"),
        2: ("Argyros", "B"),
        3: ("Chalkos", "C"),
    }

    # reward table: (money, drachmas, crate_type, crate_amount)
    REWARDS = {
        1: {
            1: (500_000, 50, "fortune", 1),
            2: (350_000, 40, "legendary", 1),
            3: (250_000, 30, "mystery", 10),
            "rest": (150_000, 20, "magic", 15),
        },
        2: {
            1: (250_000, 30, "fortune", 1),
            2: (150_000, 20, "legendary", 1),
            3: (100_000, 15, "magic", 15),
            "rest": (50_000, 10, "rare", 20),
        },
        3: {
            1: (150_000, 20, "fortune", 1),
            2: (75_000, 15, "legendary", 1),
            3: (50_000, 10, "magic", 10),
            "rest": (50_000, 5, None, 0),
        },
    }

    CRATE_COLUMNS = {
        "fortune": "crates_fortune",
        "legendary": "crates_legendary",
        "mystery": "crates_mystery",
        "magic": "crates_magic",
        "rare": "crates_rare",
    }

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        # Initialize schema before commands are used so failures are visible on load/reload.
        await self.initialize_tables()
        if not self.season_loop.is_running():
            self.season_loop.start()

    def cog_unload(self):
        if self.season_loop.is_running():
            self.season_loop.cancel()

    @staticmethod
    def _utc_now() -> datetime.datetime:
        return datetime.datetime.now(UTC)

    @staticmethod
    def _season_id_from_start(season_start: datetime.datetime) -> int:
        return int(season_start.timestamp())

    @staticmethod
    def _season_code(season_id: Optional[int]) -> str:
        if not season_id:
            return "N/A"
        try:
            dt = datetime.datetime.fromtimestamp(int(season_id), tz=UTC)
        except Exception:
            return str(season_id)
        yy = dt.year % 100
        mm = dt.month
        week_slot = ((dt.day - 1) // 7) + 1
        return f"{yy:02d}{mm:02d}{week_slot:02d}"

    def _current_weekly_start(self, now: Optional[datetime.datetime] = None) -> datetime.datetime:
        now = now or self._utc_now()
        monday_date = now.date() - datetime.timedelta(days=now.weekday())
        season_start = datetime.datetime.combine(
            monday_date,
            datetime.time(hour=0, minute=1, tzinfo=UTC),
        )
        if now < season_start:
            season_start -= datetime.timedelta(days=7)
        return season_start

    @staticmethod
    def _weekly_boundaries_from_start(
        season_start: datetime.datetime,
    ) -> Tuple[datetime.datetime, datetime.datetime, datetime.datetime]:
        active_end = season_start + datetime.timedelta(days=6, hours=15, minutes=59)
        registration_open = active_end + datetime.timedelta(minutes=30)
        registration_end = season_start + datetime.timedelta(days=7)
        return active_end, registration_open, registration_end

    def _next_sunday_1600_after(self, dt: datetime.datetime) -> datetime.datetime:
        target_date = dt.date() + datetime.timedelta(days=(6 - dt.weekday()) % 7)
        target = datetime.datetime.combine(
            target_date,
            datetime.time(hour=16, minute=0, tzinfo=UTC),
        )
        if target <= dt:
            target += datetime.timedelta(days=7)
        return target

    def _next_monday_0001_after(self, dt: datetime.datetime) -> datetime.datetime:
        target_date = dt.date() + datetime.timedelta(days=(7 - dt.weekday()) % 7)
        target = datetime.datetime.combine(
            target_date,
            datetime.time(hour=0, minute=1, tzinfo=UTC),
        )
        if target <= dt:
            target += datetime.timedelta(days=7)
        return target

    def _pool_label(self, pool_tier: int) -> str:
        name, letter = self.POOL_META.get(pool_tier, (f"Pool {pool_tier}", "?"))
        return f"{name} (Pool {letter})"

    def _combatant_label(
        self,
        ctx: commands.Context,
        user_id: int,
        row=None,
        mention_real: bool = False,
    ) -> str:
        if row and bool(row.get("is_npc", False)):
            npc_name = row.get("npc_name") or "Sparring Shade"
            return f"`{discord.utils.escape_markdown(str(npc_name))}` [NPC]"
        if mention_real:
            return f"<@{user_id}>"
        member = ctx.guild.get_member(user_id) if ctx.guild else None
        if member:
            return discord.utils.escape_mentions(discord.utils.escape_markdown(member.display_name))
        cached_user = self.bot.get_user(user_id)
        if cached_user:
            return discord.utils.escape_mentions(discord.utils.escape_markdown(cached_user.name))
        return f"User {user_id}"

    async def _event_channel(self) -> Optional[discord.abc.Messageable]:
        channel_id = int(self.AGON_ANNOUNCE_CHANNEL_ID or 0)
        if channel_id <= 0:
            channel_id = getattr(getattr(self.bot, "config", None), "game", None)
            channel_id = getattr(channel_id, "bot_event_channel", None)
        if not channel_id:
            return None
        channel = self.bot.get_channel(channel_id)
        if channel:
            return channel
        try:
            return await self.bot.fetch_channel(channel_id)
        except Exception:
            # Fallback for misconfigured override.
            fallback_id = getattr(getattr(self.bot, "config", None), "game", None)
            fallback_id = getattr(fallback_id, "bot_event_channel", None)
            if not fallback_id or int(fallback_id) == int(channel_id):
                return None
            channel = self.bot.get_channel(fallback_id)
            if channel:
                return channel
            try:
                return await self.bot.fetch_channel(fallback_id)
            except Exception:
                return None

    async def _announce_with_ping(self, message: str):
        channel = await self._event_channel()
        if not channel:
            return
        await channel.send(
            f"<@&{self.AGON_PING_ROLE_ID}> {message}",
            allowed_mentions=discord.AllowedMentions(
                roles=True,
                users=False,
                everyone=False,
            ),
        )

    @staticmethod
    def _format_reward_crates(crate_map: Dict[str, int]) -> str:
        if not crate_map:
            return "No crates"
        parts = []
        for crate_type, amount in crate_map.items():
            parts.append(f"{int(amount)} {crate_type}")
        return ", ".join(parts)

    async def _send_settlement_dms(self, season_id: int, rewards: List[Dict[str, object]]):
        if not rewards:
            return

        season_code = self._season_code(season_id)
        for reward in rewards:
            user_id = int(reward["user_id"])
            user = self.bot.get_user(user_id)
            if user is None:
                try:
                    user = await self.bot.fetch_user(user_id)
                except Exception:
                    user = None
            if user is None:
                continue

            pool_tier = int(reward["pool_tier"])
            final_rank = int(reward["final_rank"])
            final_kleos = int(reward.get("final_kleos", 0))
            next_pool_tier = int(reward["next_pool_tier"])
            next_rank = int(reward["next_rank"])
            money = int(reward["reward_money"])
            drachmas = int(reward["reward_drachmas"])
            crates = reward.get("reward_crates") or {}
            if not isinstance(crates, dict):
                crates = {}

            movement = int(reward["movement"])
            if movement < 0:
                movement_text = "Promoted"
            elif movement > 0:
                movement_text = "Demoted"
            else:
                movement_text = "Stayed"

            embed = discord.Embed(
                title=f"Agon Rewards - Season {season_code}",
                description=(
                    f"You finished **#{final_rank}** in **{self._pool_label(pool_tier)}**.\n"
                    f"Final Kleos: **{final_kleos}**\n"
                    f"Movement: **{movement_text}**"
                ),
                color=discord.Color.gold(),
            )
            embed.add_field(
                name="Rewards",
                value=(
                    f"Money: `${money:,}`\n"
                    f"Drachmas: `{drachmas:,}`\n"
                    f"Crates: {self._format_reward_crates(crates)}"
                ),
                inline=False,
            )
            embed.add_field(
                name="Next Seed",
                value=f"{self._pool_label(next_pool_tier)} - rank `{next_rank}`",
                inline=False,
            )
            try:
                await user.send(embed=embed)
            except Exception:
                continue

    def _phase_text(self, phase: str) -> str:
        phase = (phase or "").upper()
        if phase == "ACTIVE":
            return "Active"
        if phase == "REGISTRATION":
            return "Registration Open"
        if phase == "SETTLEMENT":
            return "Settlement"
        return "Stopped"

    def _parse_duration(self, value: str) -> Optional[datetime.timedelta]:
        raw = (value or "").strip().lower()
        if not raw:
            return None
        if not re.fullmatch(r"\s*(\d+\s*[smhd]\s*)+", raw):
            return None

        total = datetime.timedelta()
        for amount, unit in _DURATION_RE.findall(raw):
            n = int(amount)
            if unit == "s":
                total += datetime.timedelta(seconds=n)
            elif unit == "m":
                total += datetime.timedelta(minutes=n)
            elif unit == "h":
                total += datetime.timedelta(hours=n)
            elif unit == "d":
                total += datetime.timedelta(days=n)

        if total.total_seconds() <= 0:
            return None
        return total

    async def initialize_tables(self):
        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agon_meta (
                    id SMALLINT PRIMARY KEY,
                    last_started_season INTEGER,
                    last_settled_season INTEGER,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await conn.execute(
                """
                INSERT INTO agon_meta (id)
                VALUES (1)
                ON CONFLICT (id) DO NOTHING
                """
            )

            await conn.execute("ALTER TABLE agon_meta ADD COLUMN IF NOT EXISTS phase VARCHAR(20)")
            await conn.execute("ALTER TABLE agon_meta ADD COLUMN IF NOT EXISTS current_season_id INTEGER")
            await conn.execute("ALTER TABLE agon_meta ADD COLUMN IF NOT EXISTS next_season_id INTEGER")
            await conn.execute("ALTER TABLE agon_meta ADD COLUMN IF NOT EXISTS active_start TIMESTAMPTZ")
            await conn.execute("ALTER TABLE agon_meta ADD COLUMN IF NOT EXISTS active_end TIMESTAMPTZ")
            await conn.execute("ALTER TABLE agon_meta ADD COLUMN IF NOT EXISTS registration_open TIMESTAMPTZ")
            await conn.execute("ALTER TABLE agon_meta ADD COLUMN IF NOT EXISTS registration_end TIMESTAMPTZ")
            await conn.execute("ALTER TABLE agon_meta ADD COLUMN IF NOT EXISTS manual_mode BOOLEAN NOT NULL DEFAULT FALSE")
            await conn.execute("ALTER TABLE agon_meta ADD COLUMN IF NOT EXISTS pool_count SMALLINT NOT NULL DEFAULT 3")
            await conn.execute(
                "ALTER TABLE agon_meta ADD COLUMN IF NOT EXISTS scoring_version SMALLINT NOT NULL DEFAULT 0"
            )

            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agon_registrations (
                    season_id INTEGER NOT NULL,
                    user_id BIGINT NOT NULL,
                    xp_snapshot BIGINT NOT NULL DEFAULT 0,
                    registered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (season_id, user_id)
                )
                """
            )

            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agon_entries (
                    season_id INTEGER NOT NULL,
                    user_id BIGINT NOT NULL,
                    pool_tier SMALLINT NOT NULL CHECK (pool_tier BETWEEN 1 AND 3),
                    rank_pos INTEGER NOT NULL CHECK (rank_pos >= 1),
                    wins INTEGER NOT NULL DEFAULT 0,
                    losses INTEGER NOT NULL DEFAULT 0,
                    daily_cycle_date DATE NOT NULL DEFAULT CURRENT_DATE,
                    fights_used_today INTEGER NOT NULL DEFAULT 0,
                    rerolls_used_today INTEGER NOT NULL DEFAULT 0,
                    current_target_id BIGINT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (season_id, user_id),
                    CONSTRAINT agon_entries_rank_unique
                        UNIQUE (season_id, pool_tier, rank_pos)
                        DEFERRABLE INITIALLY DEFERRED
                )
                """
            )
            await conn.execute("ALTER TABLE agon_entries ADD COLUMN IF NOT EXISTS is_npc BOOLEAN NOT NULL DEFAULT FALSE")
            await conn.execute("ALTER TABLE agon_entries ADD COLUMN IF NOT EXISTS npc_power INTEGER NOT NULL DEFAULT 0")
            await conn.execute("ALTER TABLE agon_entries ADD COLUMN IF NOT EXISTS npc_name VARCHAR(64)")
            await conn.execute("ALTER TABLE agon_entries ADD COLUMN IF NOT EXISTS kleos INTEGER NOT NULL DEFAULT 0")
            await conn.execute("ALTER TABLE agon_entries ADD COLUMN IF NOT EXISTS seed_rank INTEGER")
            await conn.execute(
                "UPDATE agon_entries SET seed_rank = rank_pos WHERE seed_rank IS NULL"
            )
            await conn.execute(
                "ALTER TABLE agon_entries ALTER COLUMN seed_rank SET DEFAULT 1"
            )
            await conn.execute(
                "ALTER TABLE agon_entries ALTER COLUMN seed_rank SET NOT NULL"
            )
            await conn.execute("ALTER TABLE agon_entries ADD COLUMN IF NOT EXISTS veiled_rank INTEGER")
            await conn.execute("ALTER TABLE agon_entries ADD COLUMN IF NOT EXISTS veiled_kleos INTEGER")
            await conn.execute("ALTER TABLE agon_entries ADD COLUMN IF NOT EXISTS veiled_wins INTEGER")
            await conn.execute("ALTER TABLE agon_entries ADD COLUMN IF NOT EXISTS veiled_losses INTEGER")

            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agon_fights (
                    id BIGSERIAL PRIMARY KEY,
                    season_id INTEGER NOT NULL,
                    fight_day DATE NOT NULL,
                    pool_tier SMALLINT NOT NULL CHECK (pool_tier BETWEEN 1 AND 3),
                    attacker_id BIGINT NOT NULL,
                    defender_id BIGINT NOT NULL,
                    winner_id BIGINT NOT NULL,
                    attacker_rank_before INTEGER NOT NULL,
                    defender_rank_before INTEGER NOT NULL,
                    attacker_rank_after INTEGER NOT NULL,
                    defender_rank_after INTEGER NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS agon_fights_season_attacker_defender_idx
                ON agon_fights (season_id, attacker_id, defender_id)
                """
            )
            await conn.execute(
                "ALTER TABLE agon_fights ADD COLUMN IF NOT EXISTS kleos_awarded INTEGER NOT NULL DEFAULT 0"
            )

            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agon_results (
                    season_id INTEGER NOT NULL,
                    user_id BIGINT NOT NULL,
                    pool_tier SMALLINT NOT NULL CHECK (pool_tier BETWEEN 1 AND 3),
                    final_rank INTEGER NOT NULL,
                    next_pool_tier SMALLINT NOT NULL CHECK (next_pool_tier BETWEEN 1 AND 3),
                    next_rank INTEGER NOT NULL DEFAULT 1,
                    movement SMALLINT NOT NULL DEFAULT 0,
                    reward_money BIGINT NOT NULL DEFAULT 0,
                    reward_drachmas BIGINT NOT NULL DEFAULT 0,
                    reward_crates JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (season_id, user_id)
                )
                """
            )
            await conn.execute("ALTER TABLE agon_results ADD COLUMN IF NOT EXISTS next_rank INTEGER NOT NULL DEFAULT 1")
            await conn.execute(
                "ALTER TABLE agon_results ADD COLUMN IF NOT EXISTS final_kleos INTEGER NOT NULL DEFAULT 0"
            )

            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agon_user_stats (
                    user_id BIGINT PRIMARY KEY,
                    top1_finishes INTEGER NOT NULL DEFAULT 0,
                    top2_finishes INTEGER NOT NULL DEFAULT 0,
                    top3_finishes INTEGER NOT NULL DEFAULT 0,
                    top_pool_top10_streak INTEGER NOT NULL DEFAULT 0,
                    last_pool_tier SMALLINT,
                    last_rank INTEGER,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )

            # Reward crates include fortune; ensure profile has this column on older DBs.
            for col in self.CRATE_COLUMNS.values():
                await conn.execute(f'ALTER TABLE profile ADD COLUMN IF NOT EXISTS "{col}" BIGINT DEFAULT 0')

            # ALTER TABLE invalidates asyncpg's prepared SELECT * plans. Refresh the
            # pool-wide statement/schema cache before opening the data migration transaction.
            await conn.reload_schema_state()
            await self._ensure_meta_defaults(conn, for_update=False)
            async with conn.transaction():
                await self._migrate_kleos_scoring(conn)

    async def _ensure_meta_defaults(self, conn, for_update: bool = False):
        row = await conn.fetchrow(
            f'SELECT * FROM agon_meta WHERE id = 1 {"FOR UPDATE" if for_update else ""}'
        )
        if not row:
            await conn.execute("INSERT INTO agon_meta (id) VALUES (1) ON CONFLICT (id) DO NOTHING")
            row = await conn.fetchrow(
                f'SELECT * FROM agon_meta WHERE id = 1 {"FOR UPDATE" if for_update else ""}'
            )

        phase = (row["phase"] or "").upper() if row["phase"] is not None else None
        if phase is None:
            # Fresh install defaults to STOPPED until a GM opens registration.
            await conn.execute(
                """
                UPDATE agon_meta
                SET phase = 'STOPPED',
                    pool_count = COALESCE(pool_count, 3),
                    updated_at = NOW()
                WHERE id = 1
                """
            )
            return await conn.fetchrow(
                f'SELECT * FROM agon_meta WHERE id = 1 {"FOR UPDATE" if for_update else ""}'
            )

        if phase == "STOPPED":
            return row

        needs_bootstrap = (
            row["next_season_id"] is None
            or row["registration_end"] is None
            or (
                phase in {"ACTIVE", "SETTLEMENT"}
                and (row["current_season_id"] is None or row["active_end"] is None)
            )
        )

        if not needs_bootstrap:
            return row

        now = self._utc_now()
        season_start = self._current_weekly_start(now)
        active_end, registration_open, registration_end = self._weekly_boundaries_from_start(season_start)

        if now < active_end:
            phase = "ACTIVE"
        elif now < registration_open:
            phase = "SETTLEMENT"
        elif now < registration_end:
            phase = "REGISTRATION"
        else:
            season_start += datetime.timedelta(days=7)
            active_end, registration_open, registration_end = self._weekly_boundaries_from_start(season_start)
            phase = "ACTIVE"

        current_season_id = self._season_id_from_start(season_start)
        next_season_id = self._season_id_from_start(registration_end)

        await conn.execute(
            """
            UPDATE agon_meta
            SET phase = $1::varchar,
                current_season_id = $2,
                next_season_id = $3,
                active_start = $4,
                active_end = $5,
                registration_open = $6,
                registration_end = $7,
                last_started_season = COALESCE(last_started_season, $2),
                last_settled_season = CASE
                    WHEN $1::varchar = 'ACTIVE'::varchar THEN last_settled_season
                    ELSE COALESCE(last_settled_season, $2)
                END,
                updated_at = NOW()
            WHERE id = 1
            """,
            phase,
            current_season_id,
            next_season_id,
            season_start,
            active_end,
            registration_open,
            registration_end,
        )

        return await conn.fetchrow(
            f'SELECT * FROM agon_meta WHERE id = 1 {"FOR UPDATE" if for_update else ""}'
        )

    def _derive_phase(self, row, now: datetime.datetime) -> str:
        phase = (row["phase"] or "STOPPED").upper()
        if phase == "STOPPED":
            return phase

        if phase == "ACTIVE" and row["active_end"] and now >= row["active_end"]:
            return "SETTLEMENT"
        if phase == "SETTLEMENT" and row["registration_open"] and now >= row["registration_open"]:
            return "REGISTRATION"
        if phase == "REGISTRATION" and row["registration_end"] and now >= row["registration_end"]:
            return "SETTLEMENT"
        return phase

    async def _timeline(self, conn) -> Dict[str, object]:
        row = await self._ensure_meta_defaults(conn, for_update=False)
        now = self._utc_now()
        phase = self._derive_phase(row, now)
        return {
            "now": now,
            "phase": phase,
            "current_season_id": row["current_season_id"],
            "next_season_id": row["next_season_id"],
            "active_end": row["active_end"],
            "registration_open": row["registration_open"],
            "registration_end": row["registration_end"],
            "pool_count": int(row["pool_count"] or 3),
        }

    def _is_veiled(self, timeline: Dict[str, object]) -> bool:
        active_end = timeline.get("active_end")
        now = timeline.get("now") or self._utc_now()
        if timeline.get("phase") != "ACTIVE" or not active_end:
            return False
        veil_start = active_end - datetime.timedelta(hours=self.VEILED_FINALE_HOURS)
        return veil_start <= now < active_end

    async def _refresh_daily_counters(self, conn, entry_row):
        today = self._utc_now().date()
        if entry_row["daily_cycle_date"] == today:
            return entry_row

        await conn.execute(
            """
            UPDATE agon_entries
            SET daily_cycle_date = $1,
                fights_used_today = 0,
                rerolls_used_today = 0,
                current_target_id = NULL,
                updated_at = NOW()
            WHERE season_id = $2 AND user_id = $3
            """,
            today,
            entry_row["season_id"],
            entry_row["user_id"],
        )
        return await conn.fetchrow(
            "SELECT * FROM agon_entries WHERE season_id = $1 AND user_id = $2",
            entry_row["season_id"],
            entry_row["user_id"],
        )

    async def _pick_random_target(
        self,
        conn,
        season_id: int,
        pool_tier: int,
        attacker_id: int,
        exclude_ids: Optional[List[int]] = None,
    ):
        exclude_ids = exclude_ids or []
        today = self._utc_now().date()

        if exclude_ids:
            row = await conn.fetchrow(
                """
                WITH prior AS (
                    SELECT defender_id, COUNT(*)::int AS cnt
                    FROM agon_fights
                    WHERE season_id = $1
                      AND attacker_id = $2
                      AND fight_day = $3
                    GROUP BY defender_id
                )
                SELECT e.user_id,
                       COALESCE(e.veiled_rank, e.rank_pos) AS rank_pos,
                       e.is_npc,
                       e.npc_name,
                       COALESCE(p.cnt, 0) AS fights_against
                FROM agon_entries e
                LEFT JOIN prior p ON p.defender_id = e.user_id
                WHERE e.season_id = $1
                  AND e.pool_tier = $4
                  AND e.user_id <> $2
                  AND e.user_id <> ALL($5::bigint[])
                  AND COALESCE(p.cnt, 0) < $6
                ORDER BY RANDOM()
                LIMIT 1
                """,
                season_id,
                attacker_id,
                today,
                pool_tier,
                exclude_ids,
                self.MAX_SAME_OPPONENT_FIGHTS,
            )
        else:
            row = await conn.fetchrow(
                """
                WITH prior AS (
                    SELECT defender_id, COUNT(*)::int AS cnt
                    FROM agon_fights
                    WHERE season_id = $1
                      AND attacker_id = $2
                      AND fight_day = $3
                    GROUP BY defender_id
                )
                SELECT e.user_id,
                       COALESCE(e.veiled_rank, e.rank_pos) AS rank_pos,
                       e.is_npc,
                       e.npc_name,
                       COALESCE(p.cnt, 0) AS fights_against
                FROM agon_entries e
                LEFT JOIN prior p ON p.defender_id = e.user_id
                WHERE e.season_id = $1
                  AND e.pool_tier = $4
                  AND e.user_id <> $2
                  AND COALESCE(p.cnt, 0) < $5
                ORDER BY RANDOM()
                LIMIT 1
                """,
                season_id,
                attacker_id,
                today,
                pool_tier,
                self.MAX_SAME_OPPONENT_FIGHTS,
            )
        return row

    async def _simulate_fight(self, conn, attacker_row, defender_row) -> Tuple[int, int, int]:
        attacker_id = int(attacker_row["user_id"])
        defender_id = int(defender_row["user_id"])

        if bool(attacker_row["is_npc"]):
            attacker_base = max(1, int(attacker_row["npc_power"] or 1))
        else:
            attacker_base = await self._agon_power_for(conn, attacker_id)

        if bool(defender_row["is_npc"]):
            defender_base = max(1, int(defender_row["npc_power"] or 1))
        else:
            defender_base = await self._agon_power_for(conn, defender_id)

        attacker_score = int(attacker_base) + random.randint(1, 7)
        defender_score = int(defender_base) + random.randint(1, 7)

        if attacker_score == defender_score:
            winner_id = random.choice([attacker_id, defender_id])
        else:
            winner_id = attacker_id if attacker_score > defender_score else defender_id
        return winner_id, int(attacker_score), int(defender_score)

    async def _agon_power_for(self, conn, user_id: int) -> int:
        profile = await conn.fetchrow(
            'SELECT "class", "race" FROM profile WHERE "user" = $1;',
            user_id,
        )
        if not profile:
            return 1

        classes = profile["class"] or []
        race = profile["race"]
        items = await self.bot.get_equipped_items_for(user_id, conn=conn)
        base_power = int(
            sum(
                await self.bot.get_damage_armor_for(
                    user_id,
                    items=items,
                    classes=classes,
                    race=race,
                    conn=conn,
                )
            )
        )

        parsed_classes = [
            cls_obj for class_name in classes if (cls_obj := class_from_string(class_name))
        ]
        tank_grade = max(
            (cls_obj.class_grade() for cls_obj in parsed_classes if cls_obj.in_class_line(Tank)),
            default=0,
        )
        if tank_grade > 0:
            base_power += tank_grade
            if any(ItemType.from_string(item["type"]) == ItemType.Shield for item in items):
                base_power += 5

        return max(1, int(base_power))

    @staticmethod
    def _kleos_for_fight(
        attacker_rank: int,
        defender_rank: int,
        attacker_won: bool,
        prior_count: int,
    ) -> Tuple[int, str]:
        rank_gap = attacker_rank - defender_rank
        if abs(rank_gap) <= 2:
            base_points = 4 if attacker_won else 1
            opponent_band = "an opponent within 2 ranks"
        elif rank_gap > 2:
            base_points = 5 if attacker_won else 2
            opponent_band = "a higher-ranked opponent"
        else:
            base_points = 3 if attacker_won else 1
            opponent_band = "a lower-ranked opponent"

        repeat_penalty = min(max(int(prior_count), 0), 2)
        return max(1, base_points - repeat_penalty), opponent_band

    async def _recalculate_pool_ranks(self, conn, season_id: int, pool_tier: int):
        await conn.execute("SET CONSTRAINTS agon_entries_rank_unique DEFERRED")
        await conn.execute(
            """
            WITH opponent_strength AS (
                SELECT f.attacker_id AS user_id,
                       COALESCE(SUM(opponent.kleos), 0::bigint) AS strength
                FROM agon_fights f
                JOIN agon_entries opponent
                  ON opponent.season_id = f.season_id
                 AND opponent.user_id = f.defender_id
                WHERE f.season_id = $1
                  AND f.pool_tier = $2
                GROUP BY f.attacker_id
            ), ordered AS (
                SELECT e.user_id,
                       ROW_NUMBER() OVER (
                           ORDER BY e.kleos DESC,
                                    e.wins DESC,
                                    COALESCE(s.strength, 0::bigint) DESC,
                                    e.seed_rank ASC,
                                    e.user_id ASC
                       )::integer AS new_rank
                FROM agon_entries e
                LEFT JOIN opponent_strength s ON s.user_id = e.user_id
                WHERE e.season_id = $1
                  AND e.pool_tier = $2
            )
            UPDATE agon_entries e
            SET rank_pos = ordered.new_rank,
                updated_at = NOW()
            FROM ordered
            WHERE e.season_id = $1
              AND e.pool_tier = $2
              AND e.user_id = ordered.user_id
            """,
            season_id,
            pool_tier,
        )

    async def _recalculate_all_ranks(self, conn, season_id: int):
        pool_tiers = await conn.fetch(
            """
            SELECT DISTINCT pool_tier
            FROM agon_entries
            WHERE season_id = $1
            ORDER BY pool_tier
            """,
            season_id,
        )
        for row in pool_tiers:
            await self._recalculate_pool_ranks(conn, season_id, int(row["pool_tier"]))

    async def _migrate_kleos_scoring(self, conn):
        meta = await conn.fetchrow("SELECT * FROM agon_meta WHERE id = 1 FOR UPDATE")
        if int(meta["scoring_version"] or 0) >= 1:
            return

        season_id = int(meta["current_season_id"] or 0)
        phase = (meta["phase"] or "STOPPED").upper()
        if season_id and phase == "ACTIVE":
            await conn.execute(
                """
                UPDATE agon_entries
                SET seed_rank = rank_pos,
                    kleos = 0,
                    wins = 0,
                    losses = 0,
                    veiled_rank = NULL,
                    veiled_kleos = NULL,
                    veiled_wins = NULL,
                    veiled_losses = NULL
                WHERE season_id = $1
                """,
                season_id,
            )
            await conn.execute(
                """
                WITH scored AS (
                    SELECT id,
                           GREATEST(
                               1,
                               CASE
                                   WHEN ABS(attacker_rank_before - defender_rank_before) <= 2
                                       THEN CASE WHEN winner_id = attacker_id THEN 4 ELSE 1 END
                                   WHEN attacker_rank_before > defender_rank_before
                                       THEN CASE WHEN winner_id = attacker_id THEN 5 ELSE 2 END
                                   ELSE CASE WHEN winner_id = attacker_id THEN 3 ELSE 1 END
                               END
                               - LEAST(
                                   (
                                       ROW_NUMBER() OVER (
                                           PARTITION BY season_id, attacker_id, defender_id, fight_day
                                           ORDER BY created_at, id
                                       ) - 1
                                   )::integer,
                                   2
                               )
                           )::integer AS points
                    FROM agon_fights
                    WHERE season_id = $1
                )
                UPDATE agon_fights f
                SET kleos_awarded = scored.points
                FROM scored
                WHERE f.id = scored.id
                """,
                season_id,
            )
            await conn.execute(
                """
                WITH totals AS (
                    SELECT attacker_id AS user_id,
                           COALESCE(SUM(kleos_awarded), 0)::integer AS kleos,
                           COUNT(*) FILTER (WHERE winner_id = attacker_id)::integer AS wins,
                           COUNT(*) FILTER (WHERE winner_id <> attacker_id)::integer AS losses
                    FROM agon_fights
                    WHERE season_id = $1
                    GROUP BY attacker_id
                )
                UPDATE agon_entries e
                SET kleos = totals.kleos,
                    wins = totals.wins,
                    losses = totals.losses,
                    updated_at = NOW()
                FROM totals
                WHERE e.season_id = $1
                  AND e.user_id = totals.user_id
                """,
                season_id,
            )
            await self._recalculate_all_ranks(conn, season_id)

        await conn.execute(
            """
            UPDATE agon_meta
            SET scoring_version = 1,
                updated_at = NOW()
            WHERE id = 1
            """
        )

    async def _ensure_veiled_snapshot(self, conn, season_id: int) -> bool:
        already_frozen = await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1
                FROM agon_entries
                WHERE season_id = $1
                  AND veiled_rank IS NOT NULL
            )
            """,
            season_id,
        )
        if already_frozen:
            return False

        await conn.fetch(
            """
            SELECT user_id
            FROM agon_entries
            WHERE season_id = $1
            FOR UPDATE
            """,
            season_id,
        )
        already_frozen = await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1
                FROM agon_entries
                WHERE season_id = $1
                  AND veiled_rank IS NOT NULL
            )
            """,
            season_id,
        )
        if already_frozen:
            return False

        await self._recalculate_all_ranks(conn, season_id)
        await conn.execute(
            """
            UPDATE agon_entries
            SET veiled_rank = rank_pos,
                veiled_kleos = kleos,
                veiled_wins = wins,
                veiled_losses = losses,
                updated_at = NOW()
            WHERE season_id = $1
            """,
            season_id,
        )
        return True

    def _reward_for(self, pool_tier: int, final_rank: int) -> Tuple[int, int, Optional[str], int]:
        pool_reward = self.REWARDS.get(pool_tier, self.REWARDS[3])
        return pool_reward.get(final_rank, pool_reward["rest"])

    async def _apply_reward(
        self,
        conn,
        user_id: int,
        money: int,
        drachmas: int,
        crate_type: Optional[str],
        crate_amount: int,
    ) -> Dict[str, int]:
        if money > 0:
            await conn.execute(
                'UPDATE profile SET "money" = COALESCE("money", 0) + $1 WHERE "user" = $2',
                money,
                user_id,
            )
        if drachmas > 0:
            await conn.execute(
                'UPDATE profile SET dragoncoins = COALESCE(dragoncoins, 0) + $1 WHERE "user" = $2',
                drachmas,
                user_id,
            )

        crate_payload: Dict[str, int] = {}
        if crate_type and crate_amount > 0:
            col = self.CRATE_COLUMNS.get(crate_type)
            if col:
                await conn.execute(
                    f'UPDATE profile SET "{col}" = COALESCE("{col}", 0) + $1 WHERE "user" = $2',
                    crate_amount,
                    user_id,
                )
                crate_payload[crate_type] = crate_amount
        return crate_payload

    def _seed_initial_assignments(self, participants: List[Tuple[int, int]], pool_count: int) -> Dict[int, List[int]]:
        assignments: Dict[int, List[int]] = {1: [], 2: [], 3: []}
        participants = sorted(participants, key=lambda x: (-x[1], x[0]))
        n = len(participants)

        if pool_count == 2:
            top_count = n // 2
            for idx, (uid, _xp) in enumerate(participants):
                assignments[1 if idx < top_count else 2].append(uid)
            return assignments

        base = n // 3
        pool_sizes = {
            1: base,
            2: base,
            3: n - (base * 2),  # leftovers to lowest pool
        }

        cursor = 0
        for tier in (1, 2, 3):
            for _ in range(pool_sizes[tier]):
                assignments[tier].append(participants[cursor][0])
                cursor += 1
        return assignments

    async def _start_season(
        self,
        conn,
        season_id: int,
        previous_season_id: Optional[int],
    ) -> Tuple[int, int]:
        regs = await conn.fetch(
            """
            SELECT r.user_id, COALESCE(p.xp, 0) AS xp
            FROM agon_registrations r
            LEFT JOIN profile p ON p."user" = r.user_id
            WHERE r.season_id = $1
            ORDER BY COALESCE(p.xp, 0) DESC, r.user_id ASC
            """,
            season_id,
        )

        await conn.execute("DELETE FROM agon_entries WHERE season_id = $1", season_id)

        if not regs:
            return 0, 2

        participants = [(int(r["user_id"]), int(r["xp"] or 0)) for r in regs]
        pool_count = 2 if len(participants) <= 20 else 3

        assignments: Dict[int, List[int]] = {1: [], 2: [], 3: []}

        has_prev_results = False
        prev_rows = []
        if previous_season_id:
            has_prev_results = bool(
                await conn.fetchval(
                    "SELECT 1 FROM agon_results WHERE season_id = $1 LIMIT 1",
                    previous_season_id,
                )
            )
            if has_prev_results:
                user_ids = [uid for uid, _xp in participants]
                prev_rows = await conn.fetch(
                    """
                    SELECT user_id, next_pool_tier, next_rank
                    FROM agon_results
                    WHERE season_id = $1
                      AND user_id = ANY($2::bigint[])
                    """,
                    previous_season_id,
                    user_ids,
                )

        if not previous_season_id or not has_prev_results:
            assignments = self._seed_initial_assignments(participants, pool_count)
        else:
            prev_map = {
                int(r["user_id"]): (
                    min(int(r["next_pool_tier"]), pool_count),
                    int(r["next_rank"]),
                )
                for r in prev_rows
            }

            bucket: Dict[int, List[Tuple[bool, int, int, int]]] = {1: [], 2: [], 3: []}
            lowest_tier = pool_count
            for uid, xp in participants:
                if uid in prev_map:
                    tier, rank_key = prev_map[uid]
                    bucket[tier].append((True, rank_key, -xp, uid))
                else:
                    # Missed previous week (or brand new): goes to lowest pool.
                    bucket[lowest_tier].append((False, 10_000_000, -xp, uid))

            for tier in range(1, pool_count + 1):
                ordered = sorted(bucket[tier], key=lambda x: (not x[0], x[1], x[2], x[3]))
                assignments[tier] = [uid for _carry, _rank, _xp, uid in ordered]

        might_by_user: Dict[int, int] = {}
        for uid, xp in participants:
            might = 0
            try:
                might = await self._agon_power_for(conn, uid)
            except Exception:
                might = 0
            if might <= 0:
                might = max(10, int((xp or 0) ** 0.5))
            might_by_user[uid] = might

        global_might = (
            int(sum(might_by_user.values()) / len(might_by_user))
            if might_by_user
            else 25
        )

        npc_data: Dict[int, Tuple[int, str]] = {}
        for tier in range(1, pool_count + 1):
            needed = max(0, self.MIN_POOL_SIZE - len(assignments[tier]))
            if needed <= 0:
                continue

            pool_mights = [might_by_user[uid] for uid in assignments[tier] if uid in might_by_user]
            base_might = int(sum(pool_mights) / len(pool_mights)) if pool_mights else global_might
            pool_letter = self.POOL_META.get(tier, ("Pool", str(tier)))[1]

            for idx in range(1, needed + 1):
                npc_id = self.NPC_ID_BASE + (season_id * 1000) + (tier * 100) + idx
                npc_power = max(5, int(base_might * random.uniform(0.9, 1.1)))
                npc_name = f"Sparring Shade {pool_letter}{idx}"
                assignments[tier].append(npc_id)
                npc_data[npc_id] = (npc_power, npc_name)

        rows = []
        today = self._utc_now().date()
        for tier in range(1, pool_count + 1):
            for rank_pos, user_id in enumerate(assignments[tier], start=1):
                is_npc = user_id in npc_data
                npc_power, npc_name = npc_data.get(user_id, (0, None))
                rows.append(
                    (
                        season_id,
                        user_id,
                        tier,
                        rank_pos,
                        rank_pos,
                        today,
                        is_npc,
                        npc_power,
                        npc_name,
                    )
                )

        if rows:
            await conn.executemany(
                """
                INSERT INTO agon_entries (
                    season_id, user_id, pool_tier, rank_pos, seed_rank,
                    daily_cycle_date, is_npc, npc_power, npc_name
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (season_id, user_id)
                DO UPDATE SET
                    pool_tier = EXCLUDED.pool_tier,
                    rank_pos = EXCLUDED.rank_pos,
                    seed_rank = EXCLUDED.seed_rank,
                    daily_cycle_date = EXCLUDED.daily_cycle_date,
                    is_npc = EXCLUDED.is_npc,
                    npc_power = EXCLUDED.npc_power,
                    npc_name = EXCLUDED.npc_name,
                    kleos = 0,
                    wins = 0,
                    losses = 0,
                    fights_used_today = 0,
                    rerolls_used_today = 0,
                    current_target_id = NULL,
                    veiled_rank = NULL,
                    veiled_kleos = NULL,
                    veiled_wins = NULL,
                    veiled_losses = NULL,
                    updated_at = NOW()
                """,
                rows,
            )

        return len(participants), pool_count

    async def _settle_season(self, conn, season_id: int) -> Tuple[int, List[Dict[str, object]]]:
        await self._recalculate_all_ranks(conn, season_id)
        rows = await conn.fetch(
            """
            SELECT season_id, user_id, pool_tier, rank_pos, kleos, is_npc
            FROM agon_entries
            WHERE season_id = $1
            ORDER BY pool_tier ASC, rank_pos ASC
            """,
            season_id,
        )
        if not rows:
            return 0, []

        real_rows = [r for r in rows if not bool(r["is_npc"])]
        if not real_rows:
            return 0, []

        pool_count = max(int(r["pool_tier"]) for r in real_rows)
        by_pool: Dict[int, List] = {tier: [] for tier in range(1, pool_count + 1)}
        for row in real_rows:
            by_pool[int(row["pool_tier"])].append(row)

        promoted_to: Dict[int, List] = {tier: [] for tier in range(1, pool_count + 1)}
        demoted_to: Dict[int, List] = {tier: [] for tier in range(1, pool_count + 1)}
        retained: Dict[int, List] = {tier: [] for tier in range(1, pool_count + 1)}

        for tier in range(1, pool_count + 1):
            pool_rows = by_pool.get(tier, [])
            n = len(pool_rows)

            promote_rows = []
            if tier > 1 and n > 0:
                promote_n = min(3, n)
                promote_rows = pool_rows[:promote_n]
                promoted_to[tier - 1].extend(promote_rows)

            remaining = [r for r in pool_rows if r not in promote_rows]

            demote_rows = []
            if tier < pool_count and remaining:
                # Pool A has no promotion step to protect its winners. Exclude
                # its top three so a small human pool cannot demote its champion.
                demotion_candidates = remaining[3:] if tier == 1 else remaining
                demote_n = min(3, len(demotion_candidates))
                if demote_n > 0:
                    demote_rows = demotion_candidates[-demote_n:]
                    demoted_to[tier + 1].extend(demote_rows)

            retained[tier] = [r for r in remaining if r not in demote_rows]

        next_map: Dict[int, Tuple[int, int]] = {}
        for tier in range(1, pool_count + 1):
            ordered = demoted_to[tier] + retained[tier] + promoted_to[tier]
            for rank_pos, row in enumerate(ordered, start=1):
                next_map[int(row["user_id"])] = (tier, rank_pos)

        await conn.execute("DELETE FROM agon_results WHERE season_id = $1", season_id)

        top_pool_rows = by_pool.get(1, [])
        top_pool_top10 = {int(r["user_id"]) for r in top_pool_rows[:10]}

        participants = {int(r["user_id"]) for r in real_rows}
        inserts = []
        dm_rewards: List[Dict[str, object]] = []
        for row in real_rows:
            user_id = int(row["user_id"])
            pool_tier = int(row["pool_tier"])
            final_rank = int(row["rank_pos"])
            final_kleos = int(row["kleos"])

            next_pool_tier, next_rank = next_map.get(user_id, (pool_tier, final_rank))
            movement = -1 if next_pool_tier < pool_tier else 1 if next_pool_tier > pool_tier else 0

            money, drachmas, crate_type, crate_amount = self._reward_for(pool_tier, final_rank)
            reward_crates = await self._apply_reward(
                conn,
                user_id,
                money,
                drachmas,
                crate_type,
                crate_amount,
            )
            dm_rewards.append(
                {
                    "user_id": user_id,
                    "pool_tier": pool_tier,
                    "final_rank": final_rank,
                    "final_kleos": final_kleos,
                    "next_pool_tier": next_pool_tier,
                    "next_rank": next_rank,
                    "movement": movement,
                    "reward_money": money,
                    "reward_drachmas": drachmas,
                    "reward_crates": reward_crates,
                }
            )

            inserts.append(
                (
                    season_id,
                    user_id,
                    pool_tier,
                    final_rank,
                    final_kleos,
                    next_pool_tier,
                    next_rank,
                    movement,
                    money,
                    drachmas,
                    json.dumps(reward_crates),
                )
            )

        await conn.executemany(
            """
            INSERT INTO agon_results (
                season_id,
                user_id,
                pool_tier,
                final_rank,
                final_kleos,
                next_pool_tier,
                next_rank,
                movement,
                reward_money,
                reward_drachmas,
                reward_crates
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb)
            ON CONFLICT (season_id, user_id)
            DO UPDATE SET
                pool_tier = EXCLUDED.pool_tier,
                final_rank = EXCLUDED.final_rank,
                final_kleos = EXCLUDED.final_kleos,
                next_pool_tier = EXCLUDED.next_pool_tier,
                next_rank = EXCLUDED.next_rank,
                movement = EXCLUDED.movement,
                reward_money = EXCLUDED.reward_money,
                reward_drachmas = EXCLUDED.reward_drachmas,
                reward_crates = EXCLUDED.reward_crates,
                created_at = NOW()
            """,
            inserts,
        )

        if participants:
            await conn.executemany(
                """
                INSERT INTO agon_user_stats (user_id)
                VALUES ($1)
                ON CONFLICT (user_id) DO NOTHING
                """,
                [(uid,) for uid in participants],
            )

            # Streak: everyone not in top10 this season resets.
            if top_pool_top10:
                await conn.execute(
                    """
                    UPDATE agon_user_stats
                    SET top_pool_top10_streak = 0,
                        updated_at = NOW()
                    WHERE user_id <> ALL($1::bigint[])
                    """,
                    list(top_pool_top10),
                )
                await conn.execute(
                    """
                    UPDATE agon_user_stats
                    SET top_pool_top10_streak = top_pool_top10_streak + 1,
                        updated_at = NOW()
                    WHERE user_id = ANY($1::bigint[])
                    """,
                    list(top_pool_top10),
                )
            else:
                await conn.execute(
                    """
                    UPDATE agon_user_stats
                    SET top_pool_top10_streak = 0,
                        updated_at = NOW()
                    """
                )

            absolute_top3 = [int(r["user_id"]) for r in top_pool_rows[:3]]
            for pos, user_id in enumerate(absolute_top3, start=1):
                if pos == 1:
                    await conn.execute(
                        """
                        UPDATE agon_user_stats
                        SET top1_finishes = top1_finishes + 1,
                            updated_at = NOW()
                        WHERE user_id = $1
                        """,
                        user_id,
                    )
                elif pos == 2:
                    await conn.execute(
                        """
                        UPDATE agon_user_stats
                        SET top2_finishes = top2_finishes + 1,
                            updated_at = NOW()
                        WHERE user_id = $1
                        """,
                        user_id,
                    )
                elif pos == 3:
                    await conn.execute(
                        """
                        UPDATE agon_user_stats
                        SET top3_finishes = top3_finishes + 1,
                            updated_at = NOW()
                        WHERE user_id = $1
                        """,
                        user_id,
                    )

            # Store carryover position earned after settlement.
            await conn.execute(
                """
                UPDATE agon_user_stats s
                SET last_pool_tier = r.next_pool_tier,
                    last_rank = r.next_rank,
                    updated_at = NOW()
                FROM agon_results r
                WHERE r.season_id = $1
                  AND r.user_id = s.user_id
                """,
                season_id,
            )

        return len(real_rows), dm_rewards

    @tasks.loop(seconds=30)
    async def season_loop(self):
        started_count = 0
        started_season_id = None
        registration_opened = False
        registration_season_id = None
        registration_end_ts = None
        settled_season_id = None
        settlement_rewards: List[Dict[str, object]] = []

        async with self.bot.pool.acquire() as conn:
            async with conn.transaction():
                meta = await self._ensure_meta_defaults(conn, for_update=True)
                now = self._utc_now()
                phase = (meta["phase"] or "STOPPED").upper()

                if phase == "ACTIVE" and meta["active_end"]:
                    veil_start = meta["active_end"] - datetime.timedelta(
                        hours=self.VEILED_FINALE_HOURS
                    )
                    if veil_start <= now < meta["active_end"]:
                        current = int(meta["current_season_id"] or 0)
                        if current:
                            await self._ensure_veiled_snapshot(conn, current)

                if phase == "ACTIVE" and meta["active_end"] and now >= meta["active_end"]:
                    current = int(meta["current_season_id"] or 0)
                    if current and (
                        meta["last_settled_season"] is None
                        or int(meta["last_settled_season"]) < current
                    ):
                        _settled_rows, settlement_rewards = await self._settle_season(conn, current)
                        settled_season_id = current
                        await conn.execute(
                            """
                            UPDATE agon_meta
                            SET last_settled_season = $1,
                                updated_at = NOW()
                            WHERE id = 1
                            """,
                            current,
                        )

                    await conn.execute(
                        "UPDATE agon_meta SET phase = 'SETTLEMENT', updated_at = NOW() WHERE id = 1"
                    )
                    meta = await conn.fetchrow("SELECT * FROM agon_meta WHERE id = 1 FOR UPDATE")
                    phase = "SETTLEMENT"

                if phase == "SETTLEMENT" and meta["registration_open"] and now >= meta["registration_open"]:
                    await conn.execute(
                        "UPDATE agon_meta SET phase = 'REGISTRATION', updated_at = NOW() WHERE id = 1"
                    )
                    meta = await conn.fetchrow("SELECT * FROM agon_meta WHERE id = 1 FOR UPDATE")
                    phase = "REGISTRATION"
                    registration_opened = True
                    registration_season_id = int(meta["next_season_id"]) if meta["next_season_id"] else None
                    registration_end_ts = meta["registration_end"]

                if phase == "REGISTRATION" and meta["registration_end"] and now >= meta["registration_end"]:
                    season_id = int(meta["next_season_id"])
                    prev_season_id = int(meta["current_season_id"]) if meta["current_season_id"] else None

                    started_count, pool_count = await self._start_season(
                        conn,
                        season_id,
                        prev_season_id,
                    )
                    started_season_id = season_id

                    season_start = meta["registration_end"]
                    if bool(meta["manual_mode"]):
                        active_end = self._next_sunday_1600_after(season_start)
                    else:
                        active_end = season_start + datetime.timedelta(days=6, hours=15, minutes=59)

                    registration_open = active_end + datetime.timedelta(minutes=30)
                    registration_end = self._next_monday_0001_after(active_end)
                    next_season_id = self._season_id_from_start(registration_end)

                    await conn.execute(
                        """
                        UPDATE agon_meta
                        SET phase = 'ACTIVE',
                            current_season_id = $1,
                            next_season_id = $2,
                            active_start = $3,
                            active_end = $4,
                            registration_open = $5,
                            registration_end = $6,
                            manual_mode = FALSE,
                            pool_count = $7,
                            last_started_season = GREATEST(COALESCE(last_started_season, 0), $1),
                            updated_at = NOW()
                        WHERE id = 1
                        """,
                        season_id,
                        next_season_id,
                        season_start,
                        active_end,
                        registration_open,
                        registration_end,
                        pool_count,
                    )

        if registration_opened:
            close_text = (
                f" Registration closes <t:{int(registration_end_ts.timestamp())}:R>."
                if registration_end_ts
                else ""
            )
            season_text = (
                f" for season `{self._season_code(registration_season_id)}`"
                if registration_season_id
                else ""
            )
            await self._announce_with_ping(
                f"`Agon` registration is now open{season_text}.{close_text} Use `$agon register`."
            )

        if started_season_id:
            await self._announce_with_ping(
                f"`Agon` season `{self._season_code(started_season_id)}` is now **ACTIVE**. Fights are open with `$agon fight` "
                f"({started_count} registered players)."
            )

        if settled_season_id and settlement_rewards:
            await self._send_settlement_dms(settled_season_id, settlement_rewards)

    @season_loop.before_loop
    async def before_season_loop(self):
        await self.bot.wait_until_ready()

    @commands.group(name="agon", aliases=["ag"], invoke_without_command=True)
    async def agon_group(self, ctx: commands.Context):
        await ctx.invoke(self.agon_help)

    @agon_group.command(name="help")
    async def agon_help(self, ctx: commands.Context):
        async with self.bot.pool.acquire() as conn:
            timeline = await self._timeline(conn)

        is_gm_user = await user_is_gm(self.bot, ctx.author)

        phase = timeline["phase"]
        active_end = timeline["active_end"]
        reg_open = timeline["registration_open"]
        reg_end = timeline["registration_end"]
        current_season_id = timeline["current_season_id"]
        current_season_code = self._season_code(current_season_id)

        summary = [
            f"Phase: **{self._phase_text(phase)}**",
            f"Current season: `{current_season_code}`",
        ]
        if active_end:
            summary.append(f"Fights lock: <t:{int(active_end.timestamp())}:R>")
            veil_start = active_end - datetime.timedelta(hours=self.VEILED_FINALE_HOURS)
            summary.append(f"Veiled Finale begins: <t:{int(veil_start.timestamp())}:R>")
        if reg_open:
            summary.append(f"Registration opens: <t:{int(reg_open.timestamp())}:R>")
        if reg_end:
            summary.append(f"Registration ends / next season starts: <t:{int(reg_end.timestamp())}:R>")

        how_it_works = "\n".join(
            [
                "- Rankings use weekly **Kleos**, not direct position swaps.",
                "- Only the player spending a daily fight earns Kleos; defending is passive.",
                "- You must register every week. If you miss a week, your next return starts in the lowest pool.",
                "- Pool count is dynamic: 2 pools when 20 or fewer players register, otherwise 3 pools.",
                "- Pools are filled to at least 10 entries using NPC Sparring Shades.",
                "- Daily: 5 fights, 3 free rerolls, then 5 drachmas per reroll.",
                "- You can fight the same opponent up to 3 times per UTC day.",
            ]
        )
        kleos_scoring = "\n".join(
            [
                "- Opponent 3+ ranks higher: **5** for a win, **2** for a defeat.",
                "- Opponent within 2 ranks: **4** for a win, **1** for a defeat.",
                "- Opponent 3+ ranks lower: **3** for a win, **1** for a defeat.",
                "- Same opponent that day: first fight full points, second **-1**, third **-2**.",
                "- A completed fight always awards at least **1 Kleos**.",
            ]
        )
        standings_rules = "\n".join(
            [
                "- Order: Kleos, wins, opponent strength, then previous-season seed.",
                "- Kleos resets weekly; your settled pool and rank become next week's seed.",
                "- Sunday 14:00-16:00 UTC is the **Veiled Finale**: fights count, but live ranks and totals are hidden.",
                "- During the finale, target numbers and scoring bands use the frozen 14:00 ranks.",
                "- Final top/bottom 3 determine promotions and demotions; Pool A's top 3 cannot be demoted.",
                "- Undersized pools move fewer than 3 players when needed; rewards use final rank.",
            ]
        )
        player_commands = "\n".join(
            [
                "`$agon register`",
                "`$agon status`",
                "`$agon target`",
                "`$agon reroll`",
                "`$agon fight [rank|@user]`",
                "`$agon leaderboard [A|B|C]`",
            ]
        )

        embed = discord.Embed(
            title="Agon Help",
            description="\n".join(summary),
            color=discord.Color.gold(),
        )
        embed.add_field(name="How It Works", value=how_it_works, inline=False)
        embed.add_field(name="Kleos Scoring", value=kleos_scoring, inline=False)
        embed.add_field(name="Standings & Finale", value=standings_rules, inline=False)
        embed.add_field(name="Player Commands", value=player_commands, inline=False)

        if is_gm_user:
            gm_commands = "\n".join(
                [
                    "`$gmagon start <duration>` (example: `$gmagon start 3h`)",
                    "`$gmagon stop`",
                    "`$gmagon reset`",
                    "`$gmagon status`",
                ]
            )
            embed.add_field(name="GM Commands", value=gm_commands, inline=False)

        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @has_char()
    @agon_group.command(name="register")
    async def agon_register(self, ctx: commands.Context):
        async with self.bot.pool.acquire() as conn:
            timeline = await self._timeline(conn)
            if timeline["phase"] != "REGISTRATION":
                reg_open = timeline["registration_open"]
                if reg_open:
                    return await ctx.send(
                        f"Registration is closed. Next window opens <t:{int(reg_open.timestamp())}:R>."
                    )
                return await ctx.send("Agon is currently stopped. A GM must start registration.")

            next_season_id = timeline["next_season_id"]
            if not next_season_id:
                return await ctx.send("Registration is not configured yet.")

            xp = await conn.fetchval(
                'SELECT COALESCE(xp, 0) FROM profile WHERE "user" = $1',
                ctx.author.id,
            )
            await conn.execute(
                """
                INSERT INTO agon_registrations (season_id, user_id, xp_snapshot, registered_at)
                VALUES ($1, $2, $3, NOW())
                ON CONFLICT (season_id, user_id)
                DO UPDATE SET
                    xp_snapshot = EXCLUDED.xp_snapshot,
                    registered_at = NOW()
                """,
                int(next_season_id),
                ctx.author.id,
                int(xp or 0),
            )

        embed = discord.Embed(
            title="Agon Registration Confirmed",
            description=f"You are registered for Agon season `{self._season_code(next_season_id)}`.",
            color=discord.Color.gold(),
        )
        embed.set_image(url=self.REGISTER_IMAGE_URL)
        await ctx.send(embed=embed)

    @has_char()
    @agon_group.command(name="status")
    async def agon_status(self, ctx: commands.Context):
        async with self.bot.pool.acquire() as conn:
            timeline = await self._timeline(conn)
            phase = timeline["phase"]
            season_id = timeline["current_season_id"]

            if phase == "STOPPED":
                return await ctx.send("Agon is currently stopped.")

            if not season_id:
                return await ctx.send("Agon season metadata is not ready yet.")
            veiled = self._is_veiled(timeline)
            if veiled:
                async with conn.transaction():
                    await self._ensure_veiled_snapshot(conn, int(season_id))

            entry = await conn.fetchrow(
                "SELECT * FROM agon_entries WHERE season_id = $1 AND user_id = $2",
                int(season_id),
                ctx.author.id,
            )

            if not entry:
                if phase == "REGISTRATION" and timeline["next_season_id"]:
                    is_registered = await conn.fetchval(
                        """
                        SELECT 1
                        FROM agon_registrations
                        WHERE season_id = $1 AND user_id = $2
                        """,
                        int(timeline["next_season_id"]),
                        ctx.author.id,
                    )
                    if is_registered:
                        return await ctx.send(
                            f"You are already registered for upcoming season `{self._season_code(timeline['next_season_id'])}`."
                        )
                    return await ctx.send(
                        "You are not registered for the upcoming season yet. Use `$agon register`."
                    )

                return await ctx.send(
                    "You are not in the current Agon ladder. Register during the registration window."
                )

            entry = await self._refresh_daily_counters(conn, entry)

            fights_left = self.DAILY_FIGHTS - int(entry["fights_used_today"])
            rerolls_used = int(entry["rerolls_used_today"])
            rerolls_left = max(0, self.FREE_REROLLS_PER_DAY - rerolls_used)
            paid = max(0, rerolls_used - self.FREE_REROLLS_PER_DAY)
            display_rank = int(
                entry["veiled_rank"]
                if veiled and entry["veiled_rank"] is not None
                else entry["rank_pos"]
            )
            display_kleos = int(
                entry["veiled_kleos"]
                if veiled and entry["veiled_kleos"] is not None
                else entry["kleos"]
            )
            display_wins = int(
                entry["veiled_wins"]
                if veiled and entry["veiled_wins"] is not None
                else entry["wins"]
            )
            display_losses = int(
                entry["veiled_losses"]
                if veiled and entry["veiled_losses"] is not None
                else entry["losses"]
            )

            target_text = "None"
            if entry["current_target_id"]:
                target_row = await conn.fetchrow(
                    """
                    SELECT user_id,
                           CASE
                               WHEN $3::boolean THEN COALESCE(veiled_rank, rank_pos)
                               ELSE rank_pos
                           END AS rank_pos,
                           pool_tier,
                           is_npc,
                           npc_name
                    FROM agon_entries
                    WHERE season_id = $1 AND user_id = $2
                    """,
                    int(season_id),
                    int(entry["current_target_id"]),
                    veiled,
                )
                if target_row:
                    target_label = self._combatant_label(
                        ctx,
                        int(target_row["user_id"]),
                        target_row,
                    )
                    target_text = (
                        f"{target_label} (rank {int(target_row['rank_pos'])}, "
                        f"{self._pool_label(int(target_row['pool_tier']))})"
                    )

            await ctx.send(
                "**Agon Status**\n"
                f"- Phase: **{self._phase_text(phase)}**\n"
                f"- Season: `{self._season_code(season_id)}`\n"
                f"- Pool: **{self._pool_label(int(entry['pool_tier']))}**\n"
                f"- Rank: **{display_rank}**\n"
                f"- Kleos: **{display_kleos}**\n"
                f"- Record: **{display_wins}W / {display_losses}L**\n"
                f"- Daily fights left: **{fights_left}/{self.DAILY_FIGHTS}**\n"
                f"- Free rerolls left: **{rerolls_left}/{self.FREE_REROLLS_PER_DAY}**\n"
                f"- Paid rerolls used today: **{paid}**\n"
                f"- Current target: {target_text}"
                + ("\n- **Veiled Finale:** live Kleos and rank are hidden." if veiled else ""),
                allowed_mentions=discord.AllowedMentions.none(),
            )

    @has_char()
    @agon_group.command(name="target")
    async def agon_target(self, ctx: commands.Context):
        async with self.bot.pool.acquire() as conn:
            timeline = await self._timeline(conn)
            if timeline["phase"] != "ACTIVE":
                return await ctx.send("Agon fights are not active right now.")

            season_id = timeline["current_season_id"]
            if not season_id:
                return await ctx.send("No active season ID found.")
            if self._is_veiled(timeline):
                async with conn.transaction():
                    await self._ensure_veiled_snapshot(conn, int(season_id))

            entry = await conn.fetchrow(
                "SELECT * FROM agon_entries WHERE season_id = $1 AND user_id = $2",
                int(season_id),
                ctx.author.id,
            )
            if not entry:
                return await ctx.send("You are not in this season's Agon ladder.")

            entry = await self._refresh_daily_counters(conn, entry)

            target_row = None
            if entry["current_target_id"]:
                target_row = await conn.fetchrow(
                    """
                    SELECT user_id,
                           COALESCE(veiled_rank, rank_pos) AS rank_pos,
                           is_npc,
                           npc_name
                    FROM agon_entries
                    WHERE season_id = $1 AND user_id = $2
                    """,
                    int(season_id),
                    int(entry["current_target_id"]),
                )

            if not target_row:
                target_row = await self._pick_random_target(
                    conn,
                    int(season_id),
                    int(entry["pool_tier"]),
                    ctx.author.id,
                )
                if not target_row:
                    return await ctx.send(
                        "No valid target found (you may already have fought everyone 3 times today)."
                    )

                await conn.execute(
                    """
                    UPDATE agon_entries
                    SET current_target_id = $1,
                        updated_at = NOW()
                    WHERE season_id = $2 AND user_id = $3
                    """,
                    int(target_row["user_id"]),
                    int(season_id),
                    ctx.author.id,
                )

            target_label = self._combatant_label(
                ctx,
                int(target_row["user_id"]),
                target_row,
            )
            await ctx.send(
                f"Current target: {target_label} (rank **{int(target_row['rank_pos'])}** in {self._pool_label(int(entry['pool_tier']))})."
            )

    @has_char()
    @agon_group.command(name="reroll")
    async def agon_reroll(self, ctx: commands.Context):
        async with self.bot.pool.acquire() as conn:
            timeline = await self._timeline(conn)
            if timeline["phase"] != "ACTIVE":
                return await ctx.send("Agon fights are not active right now.")

            season_id = timeline["current_season_id"]
            if not season_id:
                return await ctx.send("No active season ID found.")
            if self._is_veiled(timeline):
                async with conn.transaction():
                    await self._ensure_veiled_snapshot(conn, int(season_id))

            entry = await conn.fetchrow(
                "SELECT * FROM agon_entries WHERE season_id = $1 AND user_id = $2 FOR UPDATE",
                int(season_id),
                ctx.author.id,
            )
            if not entry:
                return await ctx.send("You are not in this season's Agon ladder.")

            entry = await self._refresh_daily_counters(conn, entry)

            rerolls_used = int(entry["rerolls_used_today"])
            cost = 0 if rerolls_used < self.FREE_REROLLS_PER_DAY else self.REROLL_COST_DRACHMAS

            if cost > 0:
                coins = await conn.fetchval(
                    'SELECT COALESCE(dragoncoins, 0) FROM profile WHERE "user" = $1',
                    ctx.author.id,
                )
                if int(coins or 0) < cost:
                    return await ctx.send(
                        f"You need **{cost}** drachmas for this reroll, but you only have **{int(coins or 0)}**."
                    )
                await conn.execute(
                    'UPDATE profile SET dragoncoins = COALESCE(dragoncoins, 0) - $1 WHERE "user" = $2',
                    cost,
                    ctx.author.id,
                )

            exclude_ids = []
            if entry["current_target_id"]:
                exclude_ids.append(int(entry["current_target_id"]))

            target_row = await self._pick_random_target(
                conn,
                int(season_id),
                int(entry["pool_tier"]),
                ctx.author.id,
                exclude_ids=exclude_ids,
            )
            if not target_row:
                target_row = await self._pick_random_target(
                    conn,
                    int(season_id),
                    int(entry["pool_tier"]),
                    ctx.author.id,
                )

            if not target_row:
                return await ctx.send("No valid reroll target found.")

            await conn.execute(
                """
                UPDATE agon_entries
                SET current_target_id = $1,
                    rerolls_used_today = rerolls_used_today + 1,
                    updated_at = NOW()
                WHERE season_id = $2 AND user_id = $3
                """,
                int(target_row["user_id"]),
                int(season_id),
                ctx.author.id,
            )

            target_label = self._combatant_label(
                ctx,
                int(target_row["user_id"]),
                target_row,
            )
            msg = f"New target: {target_label} (rank **{int(target_row['rank_pos'])}**)."
            if cost > 0:
                msg += f" You paid **{cost}** drachmas."
            await ctx.send(msg)

    async def _resolve_fight_target(
        self,
        ctx: commands.Context,
        conn,
        season_id: int,
        entry,
        target: Optional[str],
    ):
        pool_tier = int(entry["pool_tier"])

        # No explicit target -> use current target or create one.
        if not target:
            target_id = entry["current_target_id"]
            target_row = None
            if target_id:
                target_row = await conn.fetchrow(
                    """
                    SELECT *
                    FROM agon_entries
                    WHERE season_id = $1 AND user_id = $2
                    """,
                    season_id,
                    int(target_id),
                )
            if not target_row:
                target_row = await self._pick_random_target(
                    conn,
                    season_id,
                    pool_tier,
                    ctx.author.id,
                )
                if target_row:
                    await conn.execute(
                        """
                        UPDATE agon_entries
                        SET current_target_id = $1,
                            updated_at = NOW()
                        WHERE season_id = $2 AND user_id = $3
                        """,
                        int(target_row["user_id"]),
                        season_id,
                        ctx.author.id,
                    )
            return target_row

        target = target.strip()

        # Rank-based target: $agon fight 4
        if target.isdigit():
            target_rank = int(target)
            return await conn.fetchrow(
                """
                SELECT *
                FROM agon_entries
                WHERE season_id = $1
                  AND pool_tier = $2
                  AND COALESCE(veiled_rank, rank_pos) = $3
                """,
                season_id,
                pool_tier,
                target_rank,
            )

        # Mention/member target.
        mentioned = None
        if ctx.message.mentions:
            mentioned = ctx.message.mentions[0]
        else:
            try:
                mentioned = await commands.MemberConverter().convert(ctx, target)
            except Exception:
                mentioned = None

        if not mentioned:
            return None

        return await conn.fetchrow(
            """
            SELECT *
            FROM agon_entries
            WHERE season_id = $1 AND pool_tier = $2 AND user_id = $3
            """,
            season_id,
            pool_tier,
            int(mentioned.id),
        )

    @has_char()
    @agon_group.command(name="fight")
    async def agon_fight(self, ctx: commands.Context, *, target: str = None):
        async with self.bot.pool.acquire() as conn:
            timeline = await self._timeline(conn)
            if timeline["phase"] != "ACTIVE":
                return await ctx.send("Agon fights are not active right now.")

            season_id = timeline["current_season_id"]
            if not season_id:
                return await ctx.send("No active season ID found.")
            veiled = self._is_veiled(timeline)

            async with conn.transaction():
                if veiled:
                    await self._ensure_veiled_snapshot(conn, int(season_id))

                entry = await conn.fetchrow(
                    "SELECT * FROM agon_entries WHERE season_id = $1 AND user_id = $2 FOR UPDATE",
                    int(season_id),
                    ctx.author.id,
                )
                if not entry:
                    return await ctx.send("You are not in this season's Agon ladder.")

                entry = await self._refresh_daily_counters(conn, entry)

                if int(entry["fights_used_today"]) >= self.DAILY_FIGHTS:
                    return await ctx.send("You already used all 5 Agon fights for today.")

                target_row = await self._resolve_fight_target(
                    ctx,
                    conn,
                    int(season_id),
                    entry,
                    target,
                )
                if not target_row:
                    return await ctx.send("Target not found in your pool.")

                attacker_id = int(entry["user_id"])
                defender_id = int(target_row["user_id"])
                if attacker_id == defender_id:
                    return await ctx.send("You cannot fight yourself.")

                prior_count = await conn.fetchval(
                    """
                    SELECT COUNT(*)
                    FROM agon_fights
                    WHERE season_id = $1
                      AND attacker_id = $2
                      AND defender_id = $3
                      AND fight_day = $4
                    """,
                    int(season_id),
                    attacker_id,
                    defender_id,
                    self._utc_now().date(),
                )
                if int(prior_count) >= self.MAX_SAME_OPPONENT_FIGHTS:
                    return await ctx.send(
                        f"You already fought this opponent {self.MAX_SAME_OPPONENT_FIGHTS} times today."
                    )

                # Lock full pool rows so simultaneous fights cannot corrupt rank order.
                pool_tier = int(entry["pool_tier"])
                await conn.fetch(
                    """
                    SELECT user_id
                    FROM agon_entries
                    WHERE season_id = $1 AND pool_tier = $2
                    FOR UPDATE
                    """,
                    int(season_id),
                    pool_tier,
                )

                # Re-read ranks under lock.
                attacker_row = await conn.fetchrow(
                    "SELECT * FROM agon_entries WHERE season_id = $1 AND user_id = $2",
                    int(season_id),
                    attacker_id,
                )
                defender_row = await conn.fetchrow(
                    "SELECT * FROM agon_entries WHERE season_id = $1 AND user_id = $2",
                    int(season_id),
                    defender_id,
                )

                attacker_rank_before = int(attacker_row["rank_pos"])
                defender_rank_before = int(defender_row["rank_pos"])
                attacker_scoring_rank = (
                    int(attacker_row["veiled_rank"])
                    if veiled and attacker_row["veiled_rank"] is not None
                    else attacker_rank_before
                )
                defender_scoring_rank = (
                    int(defender_row["veiled_rank"])
                    if veiled and defender_row["veiled_rank"] is not None
                    else defender_rank_before
                )

                winner_id, attacker_score, defender_score = await self._simulate_fight(
                    conn,
                    attacker_row,
                    defender_row,
                )
                attacker_won = winner_id == attacker_id
                kleos_awarded, opponent_band = self._kleos_for_fight(
                    attacker_scoring_rank,
                    defender_scoring_rank,
                    attacker_won,
                    int(prior_count),
                )

                updated_attacker = await conn.fetchrow(
                    """
                    UPDATE agon_entries
                    SET kleos = kleos + $1,
                        wins = wins + $2,
                        losses = losses + $3,
                        fights_used_today = fights_used_today + 1,
                        updated_at = NOW(),
                        current_target_id = NULL
                    WHERE season_id = $4 AND user_id = $5
                    RETURNING kleos
                    """,
                    kleos_awarded,
                    1 if attacker_won else 0,
                    0 if attacker_won else 1,
                    int(season_id),
                    attacker_id,
                )
                attacker_kleos_after = int(updated_attacker["kleos"])

                fight_id = await conn.fetchval(
                    """
                    INSERT INTO agon_fights (
                        season_id,
                        fight_day,
                        pool_tier,
                        attacker_id,
                        defender_id,
                        winner_id,
                        attacker_rank_before,
                        defender_rank_before,
                        attacker_rank_after,
                        defender_rank_after,
                        kleos_awarded
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                    RETURNING id
                    """,
                    int(season_id),
                    self._utc_now().date(),
                    pool_tier,
                    attacker_id,
                    defender_id,
                    winner_id,
                    attacker_rank_before,
                    defender_rank_before,
                    attacker_rank_before,
                    defender_rank_before,
                    kleos_awarded,
                )

                await self._recalculate_pool_ranks(conn, int(season_id), pool_tier)
                new_ranks = await conn.fetch(
                    """
                    SELECT user_id, rank_pos
                    FROM agon_entries
                    WHERE season_id = $1
                      AND user_id = ANY($2::bigint[])
                    """,
                    int(season_id),
                    [attacker_id, defender_id],
                )
                rank_by_user = {int(row["user_id"]): int(row["rank_pos"]) for row in new_ranks}
                attacker_new_rank = rank_by_user.get(attacker_id, attacker_rank_before)
                defender_new_rank = rank_by_user.get(defender_id, defender_rank_before)

                await conn.execute(
                    """
                    UPDATE agon_fights
                    SET attacker_rank_after = $1,
                        defender_rank_after = $2
                    WHERE id = $3
                    """,
                    attacker_new_rank,
                    defender_new_rank,
                    int(fight_id),
                )

                await conn.execute(
                    'UPDATE profile SET pvpwins = COALESCE(pvpwins, 0) + 1 WHERE "user" = $1',
                    winner_id,
                )

        attacker_label = self._combatant_label(ctx, attacker_id, attacker_row)
        defender_label = self._combatant_label(ctx, defender_id, defender_row)
        winner_label = attacker_label if winner_id == attacker_id else defender_label
        loser_label = defender_label if winner_id == attacker_id else attacker_label

        duel_embed = discord.Embed(
            title="Agon Duel",
            color=discord.Color.orange(),
        )
        duel_embed.add_field(
            name="Attacker",
            value=f"{attacker_label} (score `{attacker_score}`)",
            inline=False,
        )
        duel_embed.add_field(
            name="Defender",
            value=f"{defender_label} (score `{defender_score}`)",
            inline=False,
        )
        duel_embed.add_field(
            name="Result",
            value=f"Winner: {winner_label}\nLoser: {loser_label}",
            inline=False,
        )
        duel_embed.add_field(
            name="Kleos Earned",
            value=(
                f"**+{kleos_awarded} Kleos** for fighting {opponent_band}.\n"
                f"This was fight **{int(prior_count) + 1}/{self.MAX_SAME_OPPONENT_FIGHTS}** "
                "against this opponent today."
            ),
            inline=False,
        )
        if veiled:
            duel_embed.add_field(
                name="Veiled Finale",
                value="Your live Kleos total and rank are hidden until settlement.",
                inline=False,
            )
        else:
            duel_embed.add_field(
                name="Standings",
                value=(
                    f"Total Kleos: **{attacker_kleos_after}**\n"
                    f"Attacker rank: **#{attacker_rank_before} -> #{attacker_new_rank}**"
                ),
                inline=False,
            )
        await ctx.send(embed=duel_embed, allowed_mentions=discord.AllowedMentions.none())

    @has_char()
    @agon_group.command(name="leaderboard", aliases=["lb"])
    async def agon_leaderboard(self, ctx: commands.Context, pool: str = None):
        async with self.bot.pool.acquire() as conn:
            timeline = await self._timeline(conn)
            season_id = timeline["current_season_id"]
            if not season_id:
                return await ctx.send("No active Agon season right now.")
            veiled = self._is_veiled(timeline)
            if veiled:
                async with conn.transaction():
                    await self._ensure_veiled_snapshot(conn, int(season_id))

            pool_tier = None
            if pool:
                normalized = pool.strip().lower()
                if normalized in {"a", "1", "chrysos"}:
                    pool_tier = 1
                elif normalized in {"b", "2", "argyros"}:
                    pool_tier = 2
                elif normalized in {"c", "3", "chalkos"}:
                    pool_tier = 3

            if pool_tier is None:
                entry = await conn.fetchrow(
                    "SELECT pool_tier FROM agon_entries WHERE season_id = $1 AND user_id = $2",
                    int(season_id),
                    ctx.author.id,
                )
                pool_tier = int(entry["pool_tier"]) if entry else 1

            rows = await conn.fetch(
                """
                SELECT CASE
                           WHEN $3::boolean THEN COALESCE(e.veiled_rank, e.rank_pos)
                           ELSE e.rank_pos
                       END AS display_rank,
                       CASE
                           WHEN $3::boolean THEN COALESCE(e.veiled_kleos, e.kleos)
                           ELSE e.kleos
                       END AS display_kleos,
                       CASE
                           WHEN $3::boolean THEN COALESCE(e.veiled_wins, e.wins)
                           ELSE e.wins
                       END AS display_wins,
                       CASE
                           WHEN $3::boolean THEN COALESCE(e.veiled_losses, e.losses)
                           ELSE e.losses
                       END AS display_losses,
                       e.user_id,
                       e.is_npc,
                       e.npc_name
                FROM agon_entries e
                WHERE e.season_id = $1
                  AND e.pool_tier = $2
                ORDER BY display_rank ASC
                LIMIT 20
                """,
                int(season_id),
                pool_tier,
                veiled,
            )
            if not rows:
                return await ctx.send("No leaderboard data yet for this pool.")

        lines = []
        for row in rows:
            user_id = int(row["user_id"])
            if bool(row["is_npc"]):
                display_name = row["npc_name"] or f"Sparring Shade {user_id}"
                display_name = discord.utils.escape_markdown(str(display_name))
                lines.append(
                    f"`#{int(row['display_rank']):02d}` **{display_name}** `[NPC]`  "
                    f"K:{int(row['display_kleos'])}  "
                    f"W:{int(row['display_wins'])} L:{int(row['display_losses'])}"
                )
            else:
                member = ctx.guild.get_member(user_id) if ctx.guild else None
                display_name = member.display_name if member else None
                if not display_name:
                    cached_user = self.bot.get_user(user_id)
                    display_name = cached_user.name if cached_user else f"User {user_id}"
                display_name = discord.utils.escape_mentions(discord.utils.escape_markdown(display_name))
                lines.append(
                    f"`#{int(row['display_rank']):02d}` **{display_name}** (`{user_id}`)  "
                    f"K:{int(row['display_kleos'])}  "
                    f"W:{int(row['display_wins'])} L:{int(row['display_losses'])}"
                )

        embed = discord.Embed(
            title=(
                f"Agon Leaderboard - {self._pool_label(pool_tier)}"
                + (" [Veiled Snapshot]" if veiled else "")
            ),
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        footer = f"Season {self._season_code(season_id)}"
        if veiled:
            footer += " | Live standings hidden until settlement"
        embed.set_footer(text=footer)
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @commands.group(name="gmagon", invoke_without_command=True)
    @is_gm()
    async def gmagon_group(self, ctx: commands.Context):
        await ctx.send(
            "GM Agon commands:\n"
            "- `$gmagon start <duration>`\n"
            "- `$gmagon stop`\n"
            "- `$gmagon reset`\n"
            "- `$gmagon status`"
        )

    @gmagon_group.command(name="status")
    @is_gm()
    async def gmagon_status(self, ctx: commands.Context):
        async with self.bot.pool.acquire() as conn:
            timeline = await self._timeline(conn)
            registered_count = 0
            if timeline["next_season_id"]:
                registered_count = await conn.fetchval(
                    """
                    SELECT COUNT(*)
                    FROM agon_registrations
                    WHERE season_id = $1
                    """,
                    int(timeline["next_season_id"]),
                )
        current_code = self._season_code(timeline["current_season_id"])
        next_code = self._season_code(timeline["next_season_id"])
        msg = [
            "**GM Agon Status**",
            f"- Phase: **{self._phase_text(timeline['phase'])}**",
            f"- Current season: `{current_code}`",
            f"- Next season: `{next_code}`",
            f"- Pool count: `{timeline['pool_count']}`",
            f"- Registered right now: `{int(registered_count or 0)}`",
        ]
        if self._is_veiled(timeline):
            msg.append("- Veiled Finale: **active** (live standings hidden)")
        if timeline["active_end"]:
            msg.append(f"- Active end: <t:{int(timeline['active_end'].timestamp())}:F>")
        if timeline["registration_open"]:
            msg.append(f"- Registration open: <t:{int(timeline['registration_open'].timestamp())}:F>")
        if timeline["registration_end"]:
            msg.append(f"- Registration end: <t:{int(timeline['registration_end'].timestamp())}:F>")
        await ctx.send("\n".join(msg))

    @gmagon_group.command(name="start")
    @is_gm()
    async def gmagon_start(self, ctx: commands.Context, *, duration: str):
        delta = self._parse_duration(duration)
        if delta is None:
            return await ctx.send("Invalid duration. Example: `$gmagon start 3h` or `$gmagon start 1h30m`.")

        now = self._utc_now()
        reg_end = now + delta
        season_id = self._season_id_from_start(reg_end)

        async with self.bot.pool.acquire() as conn:
            async with conn.transaction():
                meta = await self._ensure_meta_defaults(conn, for_update=True)
                phase = (meta["phase"] or "STOPPED").upper()
                if phase == "ACTIVE":
                    return await ctx.send("Agon is active. Use `$gmagon stop` first.")

                await conn.execute(
                    """
                    UPDATE agon_meta
                    SET phase = 'REGISTRATION',
                        manual_mode = TRUE,
                        registration_open = $1,
                        registration_end = $2,
                        next_season_id = $3,
                        updated_at = NOW()
                    WHERE id = 1
                    """,
                    now,
                    reg_end,
                    season_id,
                )

        await ctx.send(
            f"Agon registration opened now and will last until <t:{int(reg_end.timestamp())}:F>. "
            f"Season `{self._season_code(season_id)}` will start automatically at close."
        )
        await self._announce_with_ping(
            f"`Agon` registration is now open for season `{self._season_code(season_id)}`. "
            f"Registration closes <t:{int(reg_end.timestamp())}:R>. Use `$agon register`."
        )

    @gmagon_group.command(name="stop")
    @is_gm()
    async def gmagon_stop(self, ctx: commands.Context):
        settled_rows = 0
        settlement_rewards: List[Dict[str, object]] = []
        settled_season_id = None
        async with self.bot.pool.acquire() as conn:
            async with conn.transaction():
                meta = await self._ensure_meta_defaults(conn, for_update=True)
                phase = (meta["phase"] or "STOPPED").upper()

                current_season_id = int(meta["current_season_id"] or 0)
                if phase == "ACTIVE" and current_season_id:
                    if (
                        meta["last_settled_season"] is None
                        or int(meta["last_settled_season"]) < current_season_id
                    ):
                        settled_rows, settlement_rewards = await self._settle_season(conn, current_season_id)
                        settled_season_id = current_season_id
                        await conn.execute(
                            """
                            UPDATE agon_meta
                            SET last_settled_season = $1,
                                updated_at = NOW()
                            WHERE id = 1
                            """,
                            current_season_id,
                        )

                await conn.execute(
                    """
                    UPDATE agon_meta
                    SET phase = 'STOPPED',
                        manual_mode = FALSE,
                        active_start = NULL,
                        active_end = NULL,
                        registration_open = NULL,
                        registration_end = NULL,
                        next_season_id = NULL,
                        updated_at = NOW()
                    WHERE id = 1
                    """
                )

        if settled_rows:
            await ctx.send(f"Agon stopped. Active season settled early; processed **{settled_rows}** players.")
            if settled_season_id and settlement_rewards:
                await self._send_settlement_dms(settled_season_id, settlement_rewards)
        else:
            await ctx.send("Agon stopped.")

    @gmagon_group.command(name="reset")
    @is_gm()
    async def gmagon_reset(self, ctx: commands.Context):
        async with self.bot.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("TRUNCATE agon_registrations")
                await conn.execute("TRUNCATE agon_entries")
                await conn.execute("TRUNCATE agon_fights RESTART IDENTITY")
                await conn.execute("TRUNCATE agon_results")
                await conn.execute("TRUNCATE agon_user_stats")

                await conn.execute(
                    """
                    UPDATE agon_meta
                    SET phase = 'STOPPED',
                        current_season_id = NULL,
                        next_season_id = NULL,
                        active_start = NULL,
                        active_end = NULL,
                        registration_open = NULL,
                        registration_end = NULL,
                        manual_mode = FALSE,
                        pool_count = 3,
                        last_started_season = NULL,
                        last_settled_season = NULL,
                        updated_at = NOW()
                    WHERE id = 1
                    """
                )

        await ctx.send("Agon has been fully reset and stopped.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Agon(bot))
