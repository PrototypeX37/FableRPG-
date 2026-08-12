"""Shared faction standing system for campaigns, conversations, and shops."""

from __future__ import annotations

import json
import logging

import discord

from discord.ext import commands

from utils.checks import has_char, is_gm

from .core import DEFAULT_TIERS, normalize_faction_key, normalize_tiers
from .service import FactionService


logger = logging.getLogger(__name__)


class Factions(commands.Cog):
    """Owns faction definitions and exposes one standing API to other cogs."""

    def __init__(self, bot):
        self.bot = bot
        self.service = FactionService(bot)

    async def cog_load(self):
        await self.ensure_tables()

    async def ensure_tables(self) -> None:
        default_tiers_json = json.dumps(
            [
                {
                    "key": tier.key,
                    "name": tier.name,
                    "minimum_points": tier.minimum_points,
                    "rank": tier.rank,
                }
                for tier in DEFAULT_TIERS
            ],
            sort_keys=True,
        )
        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS faction_definitions (
                    faction_key TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    emoji TEXT NOT NULL DEFAULT '',
                    tiers_json TEXT NOT NULL,
                    allegiance_group TEXT NOT NULL DEFAULT '',
                    rivals_json TEXT NOT NULL DEFAULT '[]',
                    allies_json TEXT NOT NULL DEFAULT '[]',
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_by BIGINT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS player_reputation (
                    user_id BIGINT NOT NULL,
                    reputation_key TEXT NOT NULL,
                    points INTEGER NOT NULL DEFAULT 0,
                    rank INTEGER NOT NULL DEFAULT 0,
                    tier_key TEXT NOT NULL DEFAULT 'neutral',
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (user_id, reputation_key)
                )
                """
            )
            await conn.execute(
                """
                ALTER TABLE player_reputation
                ADD COLUMN IF NOT EXISTS tier_key TEXT NOT NULL DEFAULT 'neutral'
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS player_faction_memberships (
                    user_id BIGINT NOT NULL,
                    faction_key TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'none',
                    joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (user_id, faction_key)
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS faction_reputation_events (
                    event_id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    faction_key TEXT NOT NULL,
                    delta INTEGER NOT NULL,
                    old_points INTEGER NOT NULL,
                    new_points INTEGER NOT NULL,
                    old_rank INTEGER NOT NULL,
                    new_rank INTEGER NOT NULL,
                    source TEXT NOT NULL DEFAULT 'system',
                    event_key TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS faction_membership_events (
                    event_id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    faction_key TEXT NOT NULL,
                    old_status TEXT NOT NULL,
                    new_status TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'system',
                    event_key TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS faction_access_rules (
                    scope TEXT NOT NULL,
                    target_key TEXT NOT NULL,
                    subtarget_key TEXT NOT NULL DEFAULT '',
                    conditions_json TEXT NOT NULL DEFAULT '[]',
                    failure_message TEXT NOT NULL DEFAULT '',
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_by BIGINT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (scope, target_key, subtarget_key)
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS faction_system_migrations (
                    migration_key TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            # Permanent narrative facts remain separate from reversible standing.
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS player_system_unlocks (
                    user_id BIGINT NOT NULL,
                    unlock_key TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'system',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    unlocked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (user_id, unlock_key)
                )
                """
            )

            migrations = (
                "ALTER TABLE faction_definitions ADD COLUMN IF NOT EXISTS description TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE faction_definitions ADD COLUMN IF NOT EXISTS emoji TEXT NOT NULL DEFAULT ''",
                f"ALTER TABLE faction_definitions ADD COLUMN IF NOT EXISTS tiers_json TEXT NOT NULL DEFAULT '{default_tiers_json}'",
                "ALTER TABLE faction_definitions ADD COLUMN IF NOT EXISTS allegiance_group TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE faction_definitions ADD COLUMN IF NOT EXISTS rivals_json TEXT NOT NULL DEFAULT '[]'",
                "ALTER TABLE faction_definitions ADD COLUMN IF NOT EXISTS allies_json TEXT NOT NULL DEFAULT '[]'",
                "ALTER TABLE faction_definitions ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE",
                "ALTER TABLE faction_definitions ADD COLUMN IF NOT EXISTS created_by BIGINT",
                "ALTER TABLE faction_definitions ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
                "ALTER TABLE faction_definitions ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
            )
            for statement in migrations:
                await conn.execute(statement)

            legacy_ranks_migrated = await conn.fetchval(
                """
                SELECT 1 FROM faction_system_migrations
                WHERE migration_key='legacy_rank_to_derived_tiers_v1'
                """
            )
            if not legacy_ranks_migrated:
                # Old campaigns mutated rank independently of points. Translate
                # that rank once so existing unlocks survive the new derived tiers.
                await conn.execute(
                    """
                    UPDATE player_reputation
                    SET points = CASE
                            WHEN rank >= 4 THEN GREATEST(points, 1000)
                            WHEN rank = 3 THEN GREATEST(points, 600)
                            WHEN rank = 2 THEN GREATEST(points, 300)
                            WHEN rank = 1 THEN GREATEST(points, 100)
                            WHEN rank <= -3 THEN LEAST(points, -1000)
                            WHEN rank = -2 THEN LEAST(points, -500)
                            WHEN rank = -1 THEN LEAST(points, -100)
                            ELSE points
                        END,
                        updated_at = NOW()
                    """
                )
                await conn.execute(
                    """
                    UPDATE player_reputation
                    SET rank = CASE
                            WHEN points >= 1000 THEN 4
                            WHEN points >= 600 THEN 3
                            WHEN points >= 300 THEN 2
                            WHEN points >= 100 THEN 1
                            WHEN points >= 0 THEN 0
                            WHEN points >= -100 THEN -1
                            WHEN points >= -500 THEN -2
                            ELSE -3
                        END,
                        tier_key = CASE
                            WHEN points >= 1000 THEN 'revered'
                            WHEN points >= 600 THEN 'honored'
                            WHEN points >= 300 THEN 'trusted'
                            WHEN points >= 100 THEN 'friendly'
                            WHEN points >= 0 THEN 'neutral'
                            WHEN points >= -100 THEN 'unfriendly'
                            WHEN points >= -500 THEN 'hostile'
                            ELSE 'hated'
                        END,
                        updated_at = NOW()
                    """
                )
                await conn.execute(
                    """
                    INSERT INTO faction_system_migrations (migration_key, applied_at)
                    VALUES ('legacy_rank_to_derived_tiers_v1', NOW())
                    ON CONFLICT (migration_key) DO NOTHING
                    """
                )

            indexes = (
                "CREATE INDEX IF NOT EXISTS faction_definitions_active_name_idx ON faction_definitions (is_active, name)",
                "CREATE INDEX IF NOT EXISTS player_reputation_user_points_idx ON player_reputation (user_id, points DESC)",
                "CREATE INDEX IF NOT EXISTS faction_reputation_events_user_time_idx ON faction_reputation_events (user_id, occurred_at DESC)",
                "CREATE UNIQUE INDEX IF NOT EXISTS faction_reputation_events_idempotency_idx ON faction_reputation_events (user_id, faction_key, event_key) WHERE event_key IS NOT NULL",
                "CREATE INDEX IF NOT EXISTS faction_membership_events_user_time_idx ON faction_membership_events (user_id, occurred_at DESC)",
                "CREATE UNIQUE INDEX IF NOT EXISTS faction_membership_events_idempotency_idx ON faction_membership_events (user_id, faction_key, event_key) WHERE event_key IS NOT NULL",
                "CREATE INDEX IF NOT EXISTS faction_access_rules_lookup_idx ON faction_access_rules (scope, target_key, is_active)",
            )
            for statement in indexes:
                await conn.execute(statement)

    # Public integration API -------------------------------------------------

    async def get_standing(self, user_id: int, faction_key: object, *, conn=None):
        return await self.service.get_standing(user_id, faction_key, conn=conn)

    async def change_standing(self, user_id: int, faction_key: object, delta: int = 0, **kwargs):
        return await self.service.change_standing(user_id, faction_key, delta, **kwargs)

    async def set_membership(self, user_id: int, faction_key: object, status: object, **kwargs):
        return await self.service.set_membership(user_id, faction_key, status, **kwargs)

    async def evaluate_condition(self, user_id: int, condition: dict, *, conn=None):
        return await self.service.check_condition(user_id, condition, conn=conn)

    async def check_content_access(
        self,
        user_id: int,
        *,
        scope: object,
        target_key: object,
        subtarget_key: object = "",
        conn=None,
    ):
        return await self.service.check_content_access(
            user_id,
            scope=scope,
            target_key=target_key,
            subtarget_key=subtarget_key,
            conn=conn,
        )

    # Player UI --------------------------------------------------------------

    async def send_overview(self, ctx) -> None:
        standings = await self.service.list_standings(ctx.author.id)
        if not standings:
            await ctx.send(
                "You have not met any factions yet. Campaign choices and faction jobs will appear here once they affect your standing."
            )
            return
        embed = discord.Embed(
            title="Faction Standing",
            description=(
                "Standing is derived automatically from signed reputation points. "
                "Membership is separate from how much a faction likes you."
            ),
            color=0x5D2E12,
        )
        for standing in standings[:20]:
            next_line = "Maximum standing"
            if standing.next_tier is not None:
                next_line = (
                    f"{standing.points_to_next_tier:,} to {standing.next_tier.name}"
                )
            icon = f"{standing.faction.emoji} " if standing.faction.emoji else ""
            embed.add_field(
                name=f"{icon}{standing.faction.name}",
                value=f"{standing.summary()}\n{next_line}",
                inline=True,
            )
        embed.set_footer(text=f"Use {ctx.clean_prefix}faction <name> for details")
        await ctx.send(embed=embed)

    async def send_faction_detail(self, ctx, faction_key: object) -> None:
        standing = await self.service.get_standing(ctx.author.id, faction_key)
        faction = standing.faction
        title = f"{faction.emoji} {faction.name}".strip()
        embed = discord.Embed(
            title=title,
            description=faction.description or "No faction description has been written yet.",
            color=0x5D2E12,
        )
        embed.add_field(name="Your standing", value=standing.summary(), inline=False)
        if standing.next_tier is None:
            progress = "You have reached the highest standing tier."
        else:
            progress = (
                f"**{standing.points_to_next_tier:,}** points until "
                f"**{standing.next_tier.name}**."
            )
        embed.add_field(name="Progress", value=progress, inline=False)
        ladder = "\n".join(
            f"{'→' if tier.key == standing.tier.key else '•'} {tier.name}: {tier.minimum_points:+,}"
            for tier in faction.tiers
        )
        embed.add_field(name="Standing tiers", value=ladder, inline=False)
        relations = []
        if faction.allies:
            relations.append("Allies: " + ", ".join(key.replace("_", " ").title() for key in faction.allies))
        if faction.rivals:
            relations.append("Rivals: " + ", ".join(key.replace("_", " ").title() for key in faction.rivals))
        if relations:
            embed.add_field(name="Relations", value="\n".join(relations), inline=False)
        await ctx.send(embed=embed)

    @commands.group(
        name="faction",
        aliases=["factions", "reputation"],
        invoke_without_command=True,
    )
    @has_char()
    async def faction(self, ctx, *, faction_key: str | None = None):
        """View your standing with the factions you have encountered."""
        if faction_key:
            return await self.send_faction_detail(ctx, faction_key)
        await self.send_overview(ctx)

    @faction.command(name="list", aliases=["catalog"])
    @has_char()
    async def faction_list(self, ctx):
        definitions = await self.service.list_definitions(active_only=True)
        if not definitions:
            return await ctx.send("No curated factions have been published yet.")
        embed = discord.Embed(title="Faction Directory", color=0x5D2E12)
        for faction in definitions[:25]:
            icon = f"{faction.emoji} " if faction.emoji else ""
            embed.add_field(
                name=f"{icon}{faction.name}",
                value=faction.description[:180] or f"Key: `{faction.key}`",
                inline=False,
            )
        await ctx.send(embed=embed)

    @faction.command(name="history", aliases=["ledger"])
    @has_char()
    async def faction_history(self, ctx, *, faction_key: str | None = None):
        key = normalize_faction_key(faction_key)
        rows = await self.bot.pool.fetch(
            """
            SELECT faction_key, delta, old_points, new_points, source, occurred_at
            FROM faction_reputation_events
            WHERE user_id=$1 AND ($2='' OR faction_key=$2)
            ORDER BY occurred_at DESC
            LIMIT 15
            """,
            ctx.author.id,
            key,
        )
        if not rows:
            return await ctx.send("No faction-standing changes were found.")
        lines = [
            (
                f"`{row['occurred_at']:%Y-%m-%d}` **{str(row['faction_key']).replace('_', ' ').title()}** "
                f"{int(row['delta']):+,} → {int(row['new_points']):+,} · {row['source']}"
            )
            for row in rows
        ]
        await ctx.send(embed=discord.Embed(title="Faction History", description="\n".join(lines), color=0x5D2E12))

    # GM authoring -----------------------------------------------------------

    @is_gm()
    @commands.group(name="gmfaction", aliases=["gmfactions"], invoke_without_command=True)
    async def gmfaction(self, ctx):
        await ctx.send(
            "Faction tools: `create`, `thresholds`, `relations`, `adjust`, `set`, "
            "`membership`, `gate`, `gateclear`, `gates`."
        )

    @gmfaction.command(name="create", aliases=["edit"])
    async def gmfaction_create(self, ctx, *, definition: str):
        """Create/edit: key | display name | emoji | description"""
        parts = [part.strip() for part in str(definition).split("|", 3)]
        if len(parts) < 2:
            return await ctx.send("Use `key | display name | emoji | description`.")
        key, name = parts[:2]
        emoji = parts[2] if len(parts) >= 3 else ""
        description = parts[3] if len(parts) >= 4 else ""
        faction = await self.service.upsert_definition(
            key,
            name=name,
            emoji=emoji,
            description=description,
            created_by=ctx.author.id,
        )
        await ctx.send(f"Saved faction **{faction.name}** (`{faction.key}`).")

    @gmfaction.command(name="thresholds", aliases=["tiers"])
    async def gmfaction_thresholds(self, ctx, faction_key: str, *, tiers_json: str):
        """Replace a faction's tiers with a JSON array."""
        try:
            tiers = normalize_tiers(json.loads(tiers_json))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return await ctx.send(f"Invalid tier JSON: {exc}")
        current = await self.service.get_definition(faction_key)
        faction = await self.service.upsert_definition(
            current.key,
            name=current.name,
            description=current.description,
            emoji=current.emoji,
            tiers=tiers,
            allegiance_group=current.allegiance_group,
            rivals=current.rivals,
            allies=current.allies,
            is_active=current.is_active,
            created_by=ctx.author.id,
        )
        await ctx.send(f"Updated **{faction.name}** with {len(faction.tiers)} standing tiers.")

    @gmfaction.command(name="relations")
    async def gmfaction_relations(self, ctx, faction_key: str, relation: str, *, faction_keys: str):
        relation = normalize_faction_key(relation)
        if relation not in {"allies", "rivals"}:
            return await ctx.send("Relation must be `allies` or `rivals`.")
        values = [value.strip() for value in faction_keys.split(",") if value.strip()]
        current = await self.service.get_definition(faction_key)
        kwargs = {"allies": current.allies, "rivals": current.rivals}
        kwargs[relation] = values
        faction = await self.service.upsert_definition(
            current.key,
            name=current.name,
            description=current.description,
            emoji=current.emoji,
            tiers=current.tiers,
            allegiance_group=current.allegiance_group,
            is_active=current.is_active,
            created_by=ctx.author.id,
            **kwargs,
        )
        await ctx.send(f"Updated {relation} for **{faction.name}**.")

    @gmfaction.command(name="adjust", aliases=["add"])
    async def gmfaction_adjust(
        self, ctx, user: discord.User, faction_key: str, amount: int, *, source: str = "gm"
    ):
        change = await self.service.change_standing(
            user.id,
            faction_key,
            amount,
            source=f"gm:{source}",
            event_key=f"gm:{ctx.message.id}",
            metadata={"gm_user_id": ctx.author.id},
        )
        await ctx.send(change.message() or "That standing change was already applied.")

    @gmfaction.command(name="set")
    async def gmfaction_set(self, ctx, user: discord.User, faction_key: str, points: int):
        change = await self.service.change_standing(
            user.id,
            faction_key,
            source="gm:set",
            set_points=points,
            event_key=f"gm:{ctx.message.id}",
            metadata={"gm_user_id": ctx.author.id},
        )
        await ctx.send(change.message() or "That standing change was already applied.")

    @gmfaction.command(name="membership", aliases=["member"])
    async def gmfaction_membership(
        self, ctx, user: discord.User, faction_key: str, status: str
    ):
        change = await self.service.set_membership(
            user.id,
            faction_key,
            status,
            source="gm:membership",
            event_key=f"gm:{ctx.message.id}",
            metadata={"gm_user_id": ctx.author.id},
        )
        await ctx.send(change.message() or "That membership change was already applied.")

    @gmfaction.command(name="gate")
    async def gmfaction_gate(
        self,
        ctx,
        scope: str,
        target_key: str,
        subtarget_key: str,
        *,
        rule_json: str,
    ):
        """Set a gate: scope target subtarget|- {condition JSON}."""
        try:
            payload = json.loads(rule_json)
            if isinstance(payload, dict) and "conditions" in payload:
                conditions = payload["conditions"]
                failure_message = str(payload.get("failure_message") or "")
            else:
                conditions = payload
                failure_message = ""
            await self.service.set_content_access_rule(
                scope=scope,
                target_key=target_key,
                subtarget_key="" if subtarget_key in {"-", "all", "*"} else subtarget_key,
                conditions=conditions,
                failure_message=failure_message,
                created_by=ctx.author.id,
            )
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return await ctx.send(f"Invalid faction gate: {exc}")
        await ctx.send("Faction access rule saved.")

    @gmfaction.command(name="gateclear", aliases=["ungate"])
    async def gmfaction_gateclear(
        self, ctx, scope: str, target_key: str, subtarget_key: str = "-"
    ):
        removed = await self.service.remove_content_access_rule(
            scope=scope,
            target_key=target_key,
            subtarget_key="" if subtarget_key in {"-", "all", "*"} else subtarget_key,
        )
        await ctx.send("Faction access rule removed." if removed else "No matching rule was found.")

    @gmfaction.command(name="gates")
    async def gmfaction_gates(self, ctx):
        rows = await self.bot.pool.fetch(
            """
            SELECT scope, target_key, subtarget_key, conditions_json, failure_message
            FROM faction_access_rules
            WHERE is_active=TRUE
            ORDER BY scope, target_key, subtarget_key
            LIMIT 30
            """
        )
        if not rows:
            return await ctx.send("No faction access rules are configured.")
        lines = []
        for row in rows:
            target = str(row["target_key"])
            if row["subtarget_key"]:
                target += f":{row['subtarget_key']}"
            conditions = json.loads(row["conditions_json"] or "[]")
            lines.append(f"`{row['scope']} {target}` — {len(conditions)} condition(s)")
        await ctx.send("Faction gates:\n" + "\n".join(lines))


async def setup(bot):
    await bot.add_cog(Factions(bot))
