"""Shared faction standing, membership, and content-access services.

This module intentionally contains no Discord command code.  Campaigns, shops,
and conversations can all use the same transaction-aware API without depending
on one another's cogs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .core import (
    DEFAULT_TIERS,
    StandingTier,
    normalize_faction_key,
    normalize_tiers,
    resolve_tier,
    tier_for_points,
)


STANDING_CONDITION_TYPES = {"reputation", "faction_standing"}
MEMBERSHIP_CONDITION_TYPE = "faction_membership"
SYSTEM_UNLOCK_CONDITION_TYPE = "system_unlock"
CONTENT_CONDITION_TYPES = (
    STANDING_CONDITION_TYPES
    | {MEMBERSHIP_CONDITION_TYPE, SYSTEM_UNLOCK_CONDITION_TYPE}
)
VALID_OPERATORS = {"=", "==", "is", "!=", "is_not", ">", ">=", "<", "<=", "contains"}


def _row_value(row: Any, key: str, default=None):
    if row is None:
        return default
    try:
        value = row[key]
    except (KeyError, TypeError):
        try:
            value = row.get(key, default)
        except AttributeError:
            return default
    return default if value is None else value


def _load_json(value: object, default):
    if isinstance(value, (list, dict)):
        return value
    if not value:
        return default
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return default
    return parsed if isinstance(parsed, type(default)) else default


def _dump_tiers(tiers: tuple[StandingTier, ...]) -> str:
    return json.dumps(
        [
            {
                "key": tier.key,
                "name": tier.name,
                "minimum_points": tier.minimum_points,
                "rank": tier.rank,
            }
            for tier in tiers
        ],
        sort_keys=True,
    )


def _compare(actual, operator: str, expected) -> bool:
    operator = str(operator or ">=").strip().lower()
    if operator not in VALID_OPERATORS:
        raise ValueError(f"Unsupported condition operator `{operator}`.")
    if operator in {"=", "==", "is"}:
        return str(actual).casefold() == str(expected).casefold()
    if operator in {"!=", "is_not"}:
        return str(actual).casefold() != str(expected).casefold()
    if operator == "contains":
        return str(expected).casefold() in str(actual).casefold()
    try:
        actual_number = float(actual)
        expected_number = float(expected)
    except (TypeError, ValueError) as exc:
        raise ValueError("Ordered conditions require numeric values.") from exc
    if operator == ">":
        return actual_number > expected_number
    if operator == ">=":
        return actual_number >= expected_number
    if operator == "<":
        return actual_number < expected_number
    return actual_number <= expected_number


def _is_integer(value: object) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    try:
        return float(value).is_integer()
    except (TypeError, ValueError, OverflowError):
        return False


def _tier_for_rank_delta(
    current: StandingTier, delta: int, tiers: tuple[StandingTier, ...]
) -> StandingTier:
    ordered = sorted(tiers, key=lambda tier: tier.rank)
    current_index = next(
        (index for index, tier in enumerate(ordered) if tier.key == current.key),
        0,
    )
    target_index = max(0, min(len(ordered) - 1, current_index + int(delta)))
    return ordered[target_index]


@dataclass(frozen=True)
class FactionDefinition:
    key: str
    name: str
    description: str
    emoji: str
    tiers: tuple[StandingTier, ...]
    allegiance_group: str
    rivals: tuple[str, ...]
    allies: tuple[str, ...]
    is_active: bool = True

    @classmethod
    def fallback(cls, key: object) -> "FactionDefinition":
        faction_key = normalize_faction_key(key)
        return cls(
            key=faction_key,
            name=faction_key.replace("_", " ").title() or "Unknown Faction",
            description="",
            emoji="",
            tiers=DEFAULT_TIERS,
            allegiance_group="",
            rivals=(),
            allies=(),
            is_active=True,
        )

    @classmethod
    def from_row(cls, row, *, fallback_key: object = "") -> "FactionDefinition":
        key = normalize_faction_key(
            _row_value(row, "faction_key", _row_value(row, "reputation_key", fallback_key))
        )
        if not _row_value(row, "faction_name") and not _row_value(row, "name"):
            return cls.fallback(key)
        raw_tiers = _load_json(_row_value(row, "tiers_json", "[]"), [])
        try:
            tiers = normalize_tiers(raw_tiers)
        except ValueError:
            tiers = DEFAULT_TIERS
        return cls(
            key=key,
            name=str(_row_value(row, "faction_name", _row_value(row, "name", ""))).strip()
            or key.replace("_", " ").title(),
            description=str(_row_value(row, "faction_description", _row_value(row, "description", ""))).strip(),
            emoji=str(_row_value(row, "emoji", "")).strip(),
            tiers=tiers,
            allegiance_group=normalize_faction_key(_row_value(row, "allegiance_group", "")),
            rivals=tuple(
                normalize_faction_key(value)
                for value in _load_json(_row_value(row, "rivals_json", "[]"), [])
                if normalize_faction_key(value)
            ),
            allies=tuple(
                normalize_faction_key(value)
                for value in _load_json(_row_value(row, "allies_json", "[]"), [])
                if normalize_faction_key(value)
            ),
            is_active=bool(_row_value(row, "is_active", True)),
        )


@dataclass(frozen=True)
class FactionStanding:
    user_id: int
    faction: FactionDefinition
    points: int
    tier: StandingTier
    membership: str = "none"

    @property
    def rank(self) -> int:
        return self.tier.rank

    @property
    def next_tier(self) -> StandingTier | None:
        return next(
            (tier for tier in self.faction.tiers if tier.minimum_points > self.points),
            None,
        )

    @property
    def points_to_next_tier(self) -> int | None:
        next_tier = self.next_tier
        return None if next_tier is None else next_tier.minimum_points - self.points

    def summary(self) -> str:
        membership = "" if self.membership == "none" else f" | {self.membership.replace('_', ' ').title()}"
        return f"**{self.tier.name}** ({self.points:+,}){membership}"


@dataclass(frozen=True)
class StandingChange:
    old: FactionStanding
    new: FactionStanding
    requested_delta: int
    applied: bool
    event_key: str = ""

    @property
    def tier_changed(self) -> bool:
        return self.old.tier.key != self.new.tier.key

    def message(self) -> str:
        if not self.applied:
            return ""
        actual_delta = self.new.points - self.old.points
        line = (
            f"**{self.new.faction.name}** reputation: {actual_delta:+,} "
            f"({self.new.points:+,}, {self.new.tier.name})"
        )
        if self.tier_changed:
            line += f" — standing changed from {self.old.tier.name} to **{self.new.tier.name}**"
        return line


@dataclass(frozen=True)
class MembershipChange:
    user_id: int
    faction: FactionDefinition
    old_status: str
    new_status: str
    applied: bool
    event_key: str = ""

    def message(self) -> str:
        if not self.applied:
            return ""
        return (
            f"**{self.faction.name}** membership: "
            f"{self.old_status.replace('_', ' ').title()} → "
            f"**{self.new_status.replace('_', ' ').title()}**"
        )


class FactionService:
    """Transaction-aware facade used by every faction-aware feature."""

    def __init__(self, bot):
        self.bot = bot

    async def get_definition(
        self, faction_key: object, *, conn=None
    ) -> FactionDefinition:
        key = normalize_faction_key(faction_key)
        if not key:
            raise ValueError("Faction key is required.")
        local = conn is None
        if local:
            conn = await self.bot.pool.acquire()
        try:
            row = await conn.fetchrow(
                """
                SELECT faction_key, name AS faction_name,
                       description AS faction_description, emoji, tiers_json,
                       allegiance_group, rivals_json, allies_json, is_active
                FROM faction_definitions
                WHERE faction_key=$1
                """,
                key,
            )
            return FactionDefinition.from_row(row, fallback_key=key)
        finally:
            if local:
                await self.bot.pool.release(conn)

    async def ensure_definition(
        self, faction_key: object, *, conn
    ) -> FactionDefinition:
        key = normalize_faction_key(faction_key)
        if not key:
            raise ValueError("Faction key is required.")
        fallback = FactionDefinition.fallback(key)
        await conn.execute(
            """
            INSERT INTO faction_definitions (
                faction_key, name, description, emoji, tiers_json,
                allegiance_group, rivals_json, allies_json, is_active, updated_at
            ) VALUES ($1,$2,'','',$3,'','[]','[]',TRUE,NOW())
            ON CONFLICT (faction_key) DO NOTHING
            """,
            key,
            fallback.name,
            _dump_tiers(fallback.tiers),
        )
        return await self.get_definition(key, conn=conn)

    async def upsert_definition(
        self,
        faction_key: object,
        *,
        name: str,
        description: str = "",
        emoji: str = "",
        tiers=None,
        allegiance_group: object = "",
        rivals=(),
        allies=(),
        is_active: bool = True,
        created_by: int | None = None,
        conn=None,
    ) -> FactionDefinition:
        key = normalize_faction_key(faction_key)
        if not key:
            raise ValueError("Faction key is required.")
        display_name = str(name or "").strip()
        if not display_name:
            raise ValueError("Faction name is required.")
        normalized_tiers = normalize_tiers(tiers or DEFAULT_TIERS)
        rival_keys = sorted({normalize_faction_key(value) for value in rivals if normalize_faction_key(value)} - {key})
        ally_keys = sorted({normalize_faction_key(value) for value in allies if normalize_faction_key(value)} - {key})
        local = conn is None
        if local:
            conn = await self.bot.pool.acquire()
        try:
            await conn.execute(
                """
                INSERT INTO faction_definitions (
                    faction_key, name, description, emoji, tiers_json,
                    allegiance_group, rivals_json, allies_json, is_active,
                    created_by, updated_at
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,NOW())
                ON CONFLICT (faction_key) DO UPDATE SET
                    name=EXCLUDED.name,
                    description=EXCLUDED.description,
                    emoji=EXCLUDED.emoji,
                    tiers_json=EXCLUDED.tiers_json,
                    allegiance_group=EXCLUDED.allegiance_group,
                    rivals_json=EXCLUDED.rivals_json,
                    allies_json=EXCLUDED.allies_json,
                    is_active=EXCLUDED.is_active,
                    updated_at=NOW()
                """,
                key,
                display_name,
                str(description or "").strip(),
                str(emoji or "").strip(),
                _dump_tiers(normalized_tiers),
                normalize_faction_key(allegiance_group),
                json.dumps(rival_keys),
                json.dumps(ally_keys),
                bool(is_active),
                created_by,
            )
            return await self.get_definition(key, conn=conn)
        finally:
            if local:
                await self.bot.pool.release(conn)

    async def list_definitions(self, *, active_only: bool = False, conn=None) -> list[FactionDefinition]:
        local = conn is None
        if local:
            conn = await self.bot.pool.acquire()
        try:
            rows = await conn.fetch(
                """
                SELECT faction_key, name AS faction_name,
                       description AS faction_description, emoji, tiers_json,
                       allegiance_group, rivals_json, allies_json, is_active
                FROM faction_definitions
                WHERE ($1 = FALSE OR is_active = TRUE)
                ORDER BY name
                """,
                bool(active_only),
            )
            return [FactionDefinition.from_row(row) for row in rows]
        finally:
            if local:
                await self.bot.pool.release(conn)

    async def get_membership(self, user_id: int, faction_key: object, *, conn=None) -> str:
        key = normalize_faction_key(faction_key)
        local = conn is None
        if local:
            conn = await self.bot.pool.acquire()
        try:
            status = await conn.fetchval(
                "SELECT status FROM player_faction_memberships WHERE user_id=$1 AND faction_key=$2",
                int(user_id),
                key,
            )
            return normalize_faction_key(status) or "none"
        finally:
            if local:
                await self.bot.pool.release(conn)

    async def get_standing(
        self, user_id: int, faction_key: object, *, conn=None
    ) -> FactionStanding:
        key = normalize_faction_key(faction_key)
        if not key:
            raise ValueError("Faction key is required.")
        local = conn is None
        if local:
            conn = await self.bot.pool.acquire()
        try:
            definition = await self.get_definition(key, conn=conn)
            row = await conn.fetchrow(
                """
                SELECT pr.points, pfm.status AS membership
                FROM (SELECT $1::BIGINT AS user_id) requested
                LEFT JOIN player_reputation pr
                  ON pr.user_id=requested.user_id AND pr.reputation_key=$2
                LEFT JOIN player_faction_memberships pfm
                  ON pfm.user_id=requested.user_id AND pfm.faction_key=$2
                """,
                int(user_id),
                key,
            )
            points = int(_row_value(row, "points", 0))
            membership = normalize_faction_key(_row_value(row, "membership", "none")) or "none"
            return FactionStanding(
                user_id=int(user_id),
                faction=definition,
                points=points,
                tier=tier_for_points(points, definition.tiers),
                membership=membership,
            )
        finally:
            if local:
                await self.bot.pool.release(conn)

    async def list_standings(self, user_id: int, *, conn=None) -> list[FactionStanding]:
        local = conn is None
        if local:
            conn = await self.bot.pool.acquire()
        try:
            rows = await conn.fetch(
                """
                WITH faction_keys AS (
                    SELECT reputation_key AS faction_key
                    FROM player_reputation WHERE user_id=$1
                    UNION
                    SELECT faction_key
                    FROM player_faction_memberships WHERE user_id=$1
                )
                SELECT keys.faction_key AS reputation_key,
                       COALESCE(pr.points, 0) AS points,
                       COALESCE(pfm.status, 'none') AS membership,
                       fd.name AS faction_name,
                       fd.description AS faction_description,
                       fd.emoji, fd.tiers_json, fd.allegiance_group,
                       fd.rivals_json, fd.allies_json, fd.is_active
                FROM faction_keys keys
                LEFT JOIN player_reputation pr
                  ON pr.user_id=$1 AND pr.reputation_key=keys.faction_key
                LEFT JOIN player_faction_memberships pfm
                  ON pfm.user_id=$1 AND pfm.faction_key=keys.faction_key
                LEFT JOIN faction_definitions fd
                  ON fd.faction_key=keys.faction_key
                """,
                int(user_id),
            )
            standings = []
            for row in rows:
                definition = FactionDefinition.from_row(
                    row, fallback_key=_row_value(row, "reputation_key", "")
                )
                points = int(_row_value(row, "points", 0))
                standings.append(
                    FactionStanding(
                        user_id=int(user_id),
                        faction=definition,
                        points=points,
                        tier=tier_for_points(points, definition.tiers),
                        membership=normalize_faction_key(_row_value(row, "membership", "none")) or "none",
                    )
                )
            return sorted(
                standings,
                key=lambda standing: (-standing.rank, -standing.points, standing.faction.name.casefold()),
            )
        finally:
            if local:
                await self.bot.pool.release(conn)

    async def change_standing(
        self,
        user_id: int,
        faction_key: object,
        delta: int = 0,
        *,
        source: str,
        event_key: object = "",
        metadata: dict | None = None,
        rank_delta: int = 0,
        set_rank: int | str | None = None,
        set_points: int | None = None,
        conn=None,
    ) -> StandingChange:
        key = normalize_faction_key(faction_key)
        if not key:
            raise ValueError("Faction key is required.")
        normalized_event_key = str(event_key or "").strip()
        local = conn is None
        if local:
            conn = await self.bot.pool.acquire()
        try:
            transaction = conn.transaction() if local else None
            if transaction is not None:
                await transaction.start()
            try:
                definition = await self.ensure_definition(key, conn=conn)
                neutral = tier_for_points(0, definition.tiers)
                await conn.execute(
                    """
                    INSERT INTO player_reputation (
                        user_id, reputation_key, points, rank, tier_key, updated_at
                    ) VALUES ($1,$2,0,$3,$4,NOW())
                    ON CONFLICT (user_id, reputation_key) DO NOTHING
                    """,
                    int(user_id),
                    key,
                    neutral.rank,
                    neutral.key,
                )
                row = await conn.fetchrow(
                    """
                    SELECT points, rank
                    FROM player_reputation
                    WHERE user_id=$1 AND reputation_key=$2
                    FOR UPDATE
                    """,
                    int(user_id),
                    key,
                )
                membership = await self.get_membership(user_id, key, conn=conn)
                old_points = int(_row_value(row, "points", 0))
                old_tier = tier_for_points(old_points, definition.tiers)
                old = FactionStanding(int(user_id), definition, old_points, old_tier, membership)

                if normalized_event_key:
                    duplicate = await conn.fetchval(
                        """
                        SELECT 1 FROM faction_reputation_events
                        WHERE user_id=$1 AND faction_key=$2 AND event_key=$3
                        """,
                        int(user_id),
                        key,
                        normalized_event_key,
                    )
                    if duplicate:
                        if transaction is not None:
                            await transaction.commit()
                        return StandingChange(old, old, int(delta), False, normalized_event_key)

                if set_points is not None:
                    new_points = int(set_points)
                else:
                    new_points = old_points + int(delta)

                if set_rank is not None:
                    target_tier = resolve_tier(set_rank, definition.tiers)
                    if target_tier is None:
                        raise ValueError(f"Unknown standing tier or rank `{set_rank}`.")
                    new_points = target_tier.minimum_points
                elif int(rank_delta):
                    target_tier = _tier_for_rank_delta(
                        old_tier, int(rank_delta), definition.tiers
                    )
                    new_points = target_tier.minimum_points

                new_tier = tier_for_points(new_points, definition.tiers)
                await conn.execute(
                    """
                    UPDATE player_reputation
                    SET points=$3, rank=$4, tier_key=$5, updated_at=NOW()
                    WHERE user_id=$1 AND reputation_key=$2
                    """,
                    int(user_id),
                    key,
                    new_points,
                    new_tier.rank,
                    new_tier.key,
                )
                await conn.execute(
                    """
                    INSERT INTO faction_reputation_events (
                        user_id, faction_key, delta, old_points, new_points,
                        old_rank, new_rank, source, event_key, metadata_json, occurred_at
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,NOW())
                    """,
                    int(user_id),
                    key,
                    new_points - old_points,
                    old_points,
                    new_points,
                    old_tier.rank,
                    new_tier.rank,
                    str(source or "system").strip() or "system",
                    normalized_event_key or None,
                    json.dumps(metadata or {}, sort_keys=True, default=str),
                )
                new = FactionStanding(int(user_id), definition, new_points, new_tier, membership)
                change = StandingChange(old, new, int(delta), True, normalized_event_key)
                if transaction is not None:
                    await transaction.commit()
            except Exception:
                if transaction is not None:
                    await transaction.rollback()
                raise
        finally:
            if local:
                await self.bot.pool.release(conn)

        dispatcher = getattr(self.bot, "dispatch", None)
        if callable(dispatcher):
            dispatcher("faction_standing_changed", change)
            if change.tier_changed:
                dispatcher("faction_tier_changed", change)
        return change

    async def set_membership(
        self,
        user_id: int,
        faction_key: object,
        status: object,
        *,
        source: str,
        event_key: object = "",
        metadata: dict | None = None,
        conn=None,
    ) -> MembershipChange:
        key = normalize_faction_key(faction_key)
        normalized_status = normalize_faction_key(status) or "none"
        if not key:
            raise ValueError("Faction key is required.")
        normalized_event_key = str(event_key or "").strip()
        local = conn is None
        if local:
            conn = await self.bot.pool.acquire()
        try:
            transaction = conn.transaction() if local else None
            if transaction is not None:
                await transaction.start()
            try:
                definition = await self.ensure_definition(key, conn=conn)
                await conn.execute(
                    """
                    INSERT INTO player_faction_memberships (
                        user_id, faction_key, status, joined_at, updated_at
                    ) VALUES ($1,$2,'none',NOW(),NOW())
                    ON CONFLICT (user_id, faction_key) DO NOTHING
                    """,
                    int(user_id),
                    key,
                )
                row = await conn.fetchrow(
                    """
                    SELECT status FROM player_faction_memberships
                    WHERE user_id=$1 AND faction_key=$2
                    FOR UPDATE
                    """,
                    int(user_id),
                    key,
                )
                old_status = normalize_faction_key(_row_value(row, "status", "none")) or "none"
                if normalized_event_key:
                    duplicate = await conn.fetchval(
                        """
                        SELECT 1 FROM faction_membership_events
                        WHERE user_id=$1 AND faction_key=$2 AND event_key=$3
                        """,
                        int(user_id),
                        key,
                        normalized_event_key,
                    )
                    if duplicate:
                        if transaction is not None:
                            await transaction.commit()
                        return MembershipChange(
                            int(user_id), definition, old_status, old_status, False, normalized_event_key
                        )
                await conn.execute(
                    """
                    INSERT INTO player_faction_memberships (
                        user_id, faction_key, status, joined_at, updated_at
                    ) VALUES ($1,$2,$3,NOW(),NOW())
                    ON CONFLICT (user_id, faction_key) DO UPDATE SET
                        status=EXCLUDED.status, updated_at=NOW()
                    """,
                    int(user_id),
                    key,
                    normalized_status,
                )
                await conn.execute(
                    """
                    INSERT INTO faction_membership_events (
                        user_id, faction_key, old_status, new_status,
                        source, event_key, metadata_json, occurred_at
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,NOW())
                    """,
                    int(user_id),
                    key,
                    old_status,
                    normalized_status,
                    str(source or "system").strip() or "system",
                    normalized_event_key or None,
                    json.dumps(metadata or {}, sort_keys=True, default=str),
                )
                change = MembershipChange(
                    int(user_id), definition, old_status, normalized_status, True, normalized_event_key
                )
                if transaction is not None:
                    await transaction.commit()
            except Exception:
                if transaction is not None:
                    await transaction.rollback()
                raise
        finally:
            if local:
                await self.bot.pool.release(conn)

        dispatcher = getattr(self.bot, "dispatch", None)
        if callable(dispatcher):
            dispatcher("faction_membership_changed", change)
        return change

    async def check_condition(self, user_id: int, condition: dict, *, conn=None) -> tuple[bool, str]:
        if not isinstance(condition, dict):
            return False, "This content has an invalid faction requirement."
        condition_type = str(condition.get("type") or "").strip().lower()
        key = normalize_faction_key(condition.get("key"))
        default_operator = (
            "=="
            if condition_type in {MEMBERSHIP_CONDITION_TYPE, SYSTEM_UNLOCK_CONDITION_TYPE}
            else ">="
        )
        operator = str(condition.get("operator") or default_operator).strip().lower()
        expected = condition.get(
            "value", "member" if condition_type == MEMBERSHIP_CONDITION_TYPE else 1
        )
        custom_description = str(condition.get("description") or "").strip()

        if condition_type in STANDING_CONDITION_TYPES:
            if not key:
                return False, custom_description or "This standing requirement has no faction key."
            standing = await self.get_standing(user_id, key, conn=conn)
            field = str(condition.get("field") or "rank").strip().lower()
            try:
                if field == "points":
                    passed = _compare(standing.points, operator, expected)
                    requirement = f"{expected} points"
                elif field == "rank":
                    target = resolve_tier(expected, standing.faction.tiers)
                    expected_rank = target.rank if target is not None else int(expected)
                    passed = _compare(standing.rank, operator, expected_rank)
                    requirement = target.name if target is not None else f"rank {expected_rank}"
                elif field == "tier":
                    target = resolve_tier(expected, standing.faction.tiers)
                    if target is None:
                        raise ValueError(f"Unknown faction tier `{expected}`.")
                    if operator in {"=", "==", "is", "!=", "is_not"}:
                        passed = _compare(standing.tier.key, operator, target.key)
                    else:
                        passed = _compare(standing.rank, operator, target.rank)
                    requirement = target.name
                else:
                    raise ValueError(f"Unsupported faction standing field `{field}`.")
            except (TypeError, ValueError) as exc:
                return False, custom_description or str(exc)
            default = (
                f"Requires **{requirement}** with **{standing.faction.name}**. "
                f"Current standing: {standing.tier.name} ({standing.points:+,})."
            )
            return passed, custom_description or default

        if condition_type == MEMBERSHIP_CONDITION_TYPE:
            if not key:
                return False, custom_description or "This membership requirement has no faction key."
            standing = await self.get_standing(user_id, key, conn=conn)
            expected_status = normalize_faction_key(expected) or "member"
            try:
                passed = _compare(standing.membership, operator, expected_status)
            except ValueError as exc:
                return False, custom_description or str(exc)
            default = (
                f"Requires **{expected_status.replace('_', ' ').title()}** membership "
                f"with **{standing.faction.name}**."
            )
            return passed, custom_description or default

        if condition_type == SYSTEM_UNLOCK_CONDITION_TYPE:
            if not key:
                return False, custom_description or "This history requirement has no unlock key."
            local = conn is None
            if local:
                conn = await self.bot.pool.acquire()
            try:
                present = bool(
                    await conn.fetchval(
                        "SELECT 1 FROM player_system_unlocks WHERE user_id=$1 AND unlock_key=$2",
                        int(user_id),
                        key,
                    )
                )
            finally:
                if local:
                    await self.bot.pool.release(conn)
            expected_bool = str(expected).strip().lower() not in {"0", "false", "no", "none"}
            try:
                passed = _compare(present, operator, expected_bool)
            except ValueError as exc:
                return False, custom_description or str(exc)
            return passed, custom_description or f"Requires story unlock **{key}**."

        return False, custom_description or f"Unsupported faction condition `{condition_type}`."

    async def check_conditions(self, user_id: int, conditions: list, *, conn=None) -> tuple[bool, str | None]:
        local = conn is None
        if local:
            conn = await self.bot.pool.acquire()
        try:
            for condition in conditions or []:
                passed, reason = await self.check_condition(user_id, condition, conn=conn)
                if not passed:
                    return False, reason
            return True, None
        finally:
            if local:
                await self.bot.pool.release(conn)

    @staticmethod
    def validate_content_conditions(conditions: object) -> list[dict]:
        if isinstance(conditions, dict):
            conditions = [conditions]
        if not isinstance(conditions, list) or not conditions:
            raise ValueError("Provide one or more faction conditions.")
        validated = []
        for condition in conditions:
            if not isinstance(condition, dict):
                raise ValueError("Every faction condition must be a JSON object.")
            condition_type = str(condition.get("type") or "").strip().lower()
            if condition_type not in CONTENT_CONDITION_TYPES:
                raise ValueError(f"Unsupported content-gate condition `{condition_type}`.")
            if not normalize_faction_key(condition.get("key")):
                raise ValueError(f"Condition `{condition_type}` requires a key.")
            default_operator = (
                "=="
                if condition_type in {MEMBERSHIP_CONDITION_TYPE, SYSTEM_UNLOCK_CONDITION_TYPE}
                else ">="
            )
            operator = str(condition.get("operator") or default_operator).strip().lower()
            if operator not in VALID_OPERATORS:
                raise ValueError(f"Unsupported condition operator `{operator}`.")
            normalized = dict(condition)
            if condition_type in STANDING_CONDITION_TYPES:
                field = str(condition.get("field") or "rank").strip().lower()
                if field not in {"points", "rank", "tier"}:
                    raise ValueError(
                        "Faction standing field must be `points`, `rank`, or `tier`."
                    )
                if operator == "contains":
                    raise ValueError("Faction standing conditions do not support `contains`.")
                value = condition.get("value", 1)
                if field in {"points", "rank"} and not _is_integer(value):
                    raise ValueError(f"Faction standing field `{field}` requires an integer value.")
                if field == "tier" and not (
                    _is_integer(value) or normalize_faction_key(value)
                ):
                    raise ValueError("Faction tier requires a tier key, name, or rank.")
                normalized["field"] = field
            elif condition_type == MEMBERSHIP_CONDITION_TYPE:
                if operator not in {"=", "==", "is", "!=", "is_not"}:
                    raise ValueError("Faction membership conditions only support equality.")
                raw_status = condition.get("value", "member")
                status = normalize_faction_key(raw_status)
                if not status:
                    raise ValueError("Faction membership requires a status value.")
                normalized["field"] = "status"
                normalized["value"] = status
            elif condition_type == SYSTEM_UNLOCK_CONDITION_TYPE and operator not in {
                "=",
                "==",
                "is",
                "!=",
                "is_not",
            }:
                raise ValueError("System unlock conditions only support equality.")
            normalized["type"] = condition_type
            normalized["key"] = normalize_faction_key(condition.get("key"))
            normalized["operator"] = operator
            validated.append(normalized)
        return validated

    async def set_content_access_rule(
        self,
        *,
        scope: object,
        target_key: object,
        subtarget_key: object = "",
        conditions: object,
        failure_message: str = "",
        created_by: int | None = None,
        conn=None,
    ) -> None:
        normalized_scope = normalize_faction_key(scope)
        normalized_target = normalize_faction_key(target_key)
        normalized_subtarget = normalize_faction_key(subtarget_key)
        if not normalized_scope or not normalized_target:
            raise ValueError("Gate scope and target key are required.")
        validated = self.validate_content_conditions(conditions)
        local = conn is None
        if local:
            conn = await self.bot.pool.acquire()
        try:
            await conn.execute(
                """
                INSERT INTO faction_access_rules (
                    scope, target_key, subtarget_key, conditions_json,
                    failure_message, is_active, created_by, updated_at
                ) VALUES ($1,$2,$3,$4,$5,TRUE,$6,NOW())
                ON CONFLICT (scope, target_key, subtarget_key) DO UPDATE SET
                    conditions_json=EXCLUDED.conditions_json,
                    failure_message=EXCLUDED.failure_message,
                    is_active=TRUE,
                    created_by=EXCLUDED.created_by,
                    updated_at=NOW()
                """,
                normalized_scope,
                normalized_target,
                normalized_subtarget,
                json.dumps(validated, sort_keys=True),
                str(failure_message or "").strip(),
                created_by,
            )
        finally:
            if local:
                await self.bot.pool.release(conn)

    async def remove_content_access_rule(
        self, *, scope: object, target_key: object, subtarget_key: object = "", conn=None
    ) -> bool:
        local = conn is None
        if local:
            conn = await self.bot.pool.acquire()
        try:
            result = await conn.execute(
                """
                DELETE FROM faction_access_rules
                WHERE scope=$1 AND target_key=$2 AND subtarget_key=$3
                """,
                normalize_faction_key(scope),
                normalize_faction_key(target_key),
                normalize_faction_key(subtarget_key),
            )
            return str(result).endswith(" 1")
        finally:
            if local:
                await self.bot.pool.release(conn)

    async def check_content_access(
        self,
        user_id: int,
        *,
        scope: object,
        target_key: object,
        subtarget_key: object = "",
        conn=None,
    ) -> tuple[bool, str | None]:
        normalized_scope = normalize_faction_key(scope)
        normalized_target = normalize_faction_key(target_key)
        normalized_subtarget = normalize_faction_key(subtarget_key)
        local = conn is None
        if local:
            conn = await self.bot.pool.acquire()
        try:
            rows = await conn.fetch(
                """
                SELECT subtarget_key, conditions_json, failure_message
                FROM faction_access_rules
                WHERE scope=$1 AND target_key=$2 AND is_active=TRUE
                  AND (subtarget_key='' OR subtarget_key=$3)
                ORDER BY CASE WHEN subtarget_key='' THEN 0 ELSE 1 END
                """,
                normalized_scope,
                normalized_target,
                normalized_subtarget,
            )
            for row in rows:
                raw_conditions = _row_value(row, "conditions_json", "")
                try:
                    conditions = (
                        raw_conditions
                        if isinstance(raw_conditions, list)
                        else json.loads(str(raw_conditions))
                    )
                except (TypeError, ValueError, json.JSONDecodeError):
                    return False, "This faction access rule is invalid. Please notify a GM."
                if not isinstance(conditions, list) or not conditions:
                    return False, "This faction access rule is invalid. Please notify a GM."
                passed, reason = await self.check_conditions(
                    int(user_id), conditions, conn=conn
                )
                if not passed:
                    return False, str(_row_value(row, "failure_message", "")).strip() or reason
            return True, None
        finally:
            if local:
                await self.bot.pool.release(conn)


__all__ = [
    "CONTENT_CONDITION_TYPES",
    "FactionDefinition",
    "FactionService",
    "FactionStanding",
    "MembershipChange",
    "StandingChange",
]
