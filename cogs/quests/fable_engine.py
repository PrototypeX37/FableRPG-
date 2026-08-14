"""Core persistence and value semantics for the Fable V2 story engine.

The campaign cog remains responsible for Discord presentation and for adapting
legacy quest/faction conditions.  This module owns the parts that must be
shared by every future story surface: campaign runs, typed run state,
idempotent effects, and an append-only audit trail.
"""

from __future__ import annotations

import copy
import json
import math
import re
import uuid
from dataclasses import dataclass
from typing import Any


STATE_CONDITION_TYPES = {"state", "story_state"}
STATE_EFFECT_TYPES = {
    "state_set",
    "state_increment",
    "state_add",
    "state_remove",
    "state_delete",
}
MODEL_CONDITION_TYPES = {"relationship", "location_state", "war_state"}
MODEL_EFFECT_TYPES = {
    "relationship_change",
    "relationship_set",
    "relationship_flag_add",
    "relationship_flag_remove",
    "location_set",
    "war_set",
    "war_increment",
}
STATE_VALUE_TYPES = {"null", "boolean", "integer", "number", "string", "list", "object"}
STATE_OPERATORS = {
    "=",
    "==",
    "is",
    "!=",
    "is_not",
    ">",
    ">=",
    "<",
    "<=",
    "contains",
    "not_contains",
    "in",
    "not_in",
    "exists",
    "not_exists",
}


class FableStateError(ValueError):
    """Raised when authored state data cannot be applied safely."""


@dataclass(frozen=True)
class StateMutation:
    key: str
    before: Any
    after: Any
    deleted: bool = False


def normalize_state_key(value: object) -> str:
    """Return a stable dotted key while retaining useful author namespaces."""
    segments = []
    for segment in str(value or "").strip().split("."):
        cleaned = re.sub(r"[^a-z0-9]+", "_", segment.strip().lower()).strip("_")
        if cleaned:
            segments.append(cleaned)
    return ".".join(segments)


def model_state_key(model_type: object, key: object, field: object) -> str:
    """Translate an authored companion/location/war field to scoped state."""
    model = str(model_type or "").strip().lower()
    prefixes = {
        "relationship": "companion",
        "location_state": "location",
        "war_state": "war",
    }
    prefix = prefixes.get(model)
    entity_key = normalize_state_key(key).replace(".", "_")
    field_key = normalize_state_key(field).replace(".", "_")
    if not prefix or not entity_key or not field_key:
        raise FableStateError(f"Invalid {model or 'story model'} key or field.")
    return f"{prefix}.{entity_key}.{field_key}"


def expand_model_effect(effect: dict) -> dict:
    """Compile an expressive authored effect into one atomic state effect."""
    effect_type = str(effect.get("type") or "").strip().lower()
    if effect_type not in MODEL_EFFECT_TYPES:
        return copy.deepcopy(effect)

    if effect_type.startswith("relationship_"):
        model_type = "relationship"
        default_field = "flags" if "flag_" in effect_type else "trust"
    elif effect_type.startswith("location_"):
        model_type = "location_state"
        default_field = "status"
    else:
        model_type = "war_state"
        default_field = "strength"
    key = model_state_key(model_type, effect.get("key"), effect.get("field") or default_field)

    compiled = {
        "key": key,
        "message": str(effect.get("message") or "").strip(),
    }
    if effect_type in {"relationship_change", "war_increment"}:
        compiled.update(
            {
                "type": "state_increment",
                "delta": effect.get("delta", effect.get("value", 0)),
            }
        )
        if effect.get("minimum") is not None:
            compiled["minimum"] = effect.get("minimum")
        if effect.get("maximum") is not None:
            compiled["maximum"] = effect.get("maximum")
    elif effect_type == "relationship_flag_add":
        compiled.update({"type": "state_add", "value": effect.get("value")})
    elif effect_type == "relationship_flag_remove":
        compiled.update({"type": "state_remove", "value": effect.get("value")})
    else:
        compiled.update(
            {
                "type": "state_set",
                "value": effect.get("value"),
                "value_type": effect.get("value_type"),
            }
        )
    return compiled


def infer_state_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        if not math.isfinite(value):
            raise FableStateError("State numbers must be finite.")
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "object"
    raise FableStateError(f"Unsupported story-state value `{type(value).__name__}`.")


def coerce_state_value(value: Any, value_type: str | None = None) -> tuple[str, Any]:
    """Validate and, when explicitly requested, coerce a JSON-safe state value."""
    requested = str(value_type or "").strip().lower()
    if not requested:
        requested = infer_state_type(value)
    if requested not in STATE_VALUE_TYPES:
        raise FableStateError(f"Unsupported story-state value type `{requested}`.")

    try:
        if requested == "null":
            normalized = None
        elif requested == "boolean":
            if isinstance(value, str):
                lowered = value.strip().lower()
                if lowered not in {"true", "false", "1", "0", "yes", "no"}:
                    raise FableStateError(f"Cannot coerce `{value}` to boolean.")
                normalized = lowered in {"true", "1", "yes"}
            else:
                normalized = bool(value)
        elif requested == "integer":
            if isinstance(value, bool):
                raise FableStateError("Booleans cannot be stored as integers.")
            normalized = int(value)
        elif requested == "number":
            if isinstance(value, bool):
                raise FableStateError("Booleans cannot be stored as numbers.")
            normalized = float(value)
            if not math.isfinite(normalized):
                raise FableStateError("State numbers must be finite.")
        elif requested == "string":
            normalized = str(value)
        elif requested == "list":
            if not isinstance(value, list):
                raise FableStateError("A list state value must be a JSON array.")
            normalized = copy.deepcopy(value)
        else:
            if not isinstance(value, dict):
                raise FableStateError("An object state value must be a JSON object.")
            normalized = copy.deepcopy(value)
    except (TypeError, ValueError) as exc:
        if isinstance(exc, FableStateError):
            raise
        raise FableStateError(f"Cannot coerce `{value}` to {requested}.") from exc

    # This rejects sets, custom objects, NaN nested inside containers, and
    # anything else PostgreSQL JSON storage could not reproduce faithfully.
    try:
        json.dumps(normalized, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise FableStateError("Story-state values must be valid JSON.") from exc
    return requested, normalized


def compare_state_values(actual: Any, operator: str, expected: Any, *, exists: bool) -> bool:
    operator = str(operator or "==").strip().lower()
    if operator == "exists":
        return exists
    if operator == "not_exists":
        return not exists
    if not exists:
        return False
    if operator in {"=", "==", "is"}:
        return actual == expected
    if operator in {"!=", "is_not"}:
        return actual != expected
    if operator in {"contains", "not_contains"}:
        try:
            contained = expected in actual
        except (TypeError, KeyError):
            contained = str(expected).casefold() in str(actual).casefold()
        return not contained if operator == "not_contains" else contained
    if operator in {"in", "not_in"}:
        try:
            contained = actual in expected
        except (TypeError, KeyError):
            contained = str(actual).casefold() in str(expected).casefold()
        return not contained if operator == "not_in" else contained
    try:
        left = float(actual)
        right = float(expected)
    except (TypeError, ValueError):
        return False
    if operator == ">":
        return left > right
    if operator == ">=":
        return left >= right
    if operator == "<":
        return left < right
    if operator == "<=":
        return left <= right
    return False


class FableStateService:
    """Database adapter for V2 campaign runs and scoped story state."""

    async def initialize(self, conn) -> None:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fable_campaign_runs (
                run_id TEXT PRIMARY KEY,
                user_id BIGINT NOT NULL,
                campaign_key TEXT NOT NULL,
                run_kind TEXT NOT NULL DEFAULT 'canonical',
                is_canonical BOOLEAN NOT NULL DEFAULT TRUE,
                content_version INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'active',
                current_node_key TEXT NOT NULL,
                history_json TEXT NOT NULL DEFAULT '[]',
                choices_json TEXT NOT NULL DEFAULT '{}',
                unlocks_json TEXT NOT NULL DEFAULT '[]',
                campaign_json_snapshot TEXT NOT NULL DEFAULT '{}',
                ending_snapshot_json TEXT NOT NULL DEFAULT '{}',
                started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                completed_at TIMESTAMPTZ
            )
            """
        )
        await conn.execute(
            """
            ALTER TABLE fable_campaign_runs
            ADD COLUMN IF NOT EXISTS campaign_json_snapshot TEXT NOT NULL DEFAULT '{}'
            """
        )
        await conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS fable_campaign_runs_canonical_idx
            ON fable_campaign_runs (user_id, campaign_key)
            WHERE is_canonical = TRUE
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS fable_campaign_runs_active_idx
            ON fable_campaign_runs (user_id, status, updated_at DESC)
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fable_run_state (
                run_id TEXT NOT NULL REFERENCES fable_campaign_runs(run_id) ON DELETE CASCADE,
                state_key TEXT NOT NULL,
                value_type TEXT NOT NULL,
                value_json TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (run_id, state_key)
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fable_player_preferences (
                user_id BIGINT PRIMARY KEY,
                selected_run_id TEXT REFERENCES fable_campaign_runs(run_id) ON DELETE SET NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fable_scenario_attempts (
                attempt_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES fable_campaign_runs(run_id) ON DELETE CASCADE,
                user_id BIGINT NOT NULL,
                campaign_key TEXT NOT NULL,
                node_key TEXT NOT NULL,
                scenario_key TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                current_stage_key TEXT NOT NULL,
                history_json TEXT NOT NULL DEFAULT '[]',
                result_json TEXT NOT NULL DEFAULT '{}',
                started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                completed_at TIMESTAMPTZ,
                UNIQUE (run_id, node_key)
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fable_warfront_attempts (
                attempt_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES fable_campaign_runs(run_id) ON DELETE CASCADE,
                user_id BIGINT NOT NULL,
                campaign_key TEXT NOT NULL,
                node_key TEXT NOT NULL,
                warfront_key TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'planning',
                player_front_key TEXT NOT NULL DEFAULT '',
                assignments_json TEXT NOT NULL DEFAULT '{}',
                result_json TEXT NOT NULL DEFAULT '{}',
                started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                completed_at TIMESTAMPTZ,
                UNIQUE (run_id, node_key)
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fable_processed_events (
                run_id TEXT NOT NULL REFERENCES fable_campaign_runs(run_id) ON DELETE CASCADE,
                event_key TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'campaign',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (run_id, event_key)
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS player_fable_rewards (
                user_id BIGINT NOT NULL,
                reward_type TEXT NOT NULL,
                reward_key TEXT NOT NULL,
                display_name TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT 'campaign',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                unlocked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (user_id, reward_type, reward_key)
            )
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS player_fable_rewards_user_idx
            ON player_fable_rewards (user_id, reward_type, unlocked_at)
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fable_state_events (
                id BIGSERIAL PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES fable_campaign_runs(run_id) ON DELETE CASCADE,
                user_id BIGINT NOT NULL,
                event_key TEXT NOT NULL,
                effect_index INTEGER NOT NULL,
                state_key TEXT NOT NULL,
                effect_type TEXT NOT NULL,
                before_json TEXT,
                after_json TEXT,
                source TEXT NOT NULL DEFAULT 'campaign',
                occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (run_id, event_key, effect_index)
            )
            """
        )

        # V1 campaigns become canonical V2 runs without invalidating the old
        # table.  The deterministic UUID-shaped key makes this migration safe
        # to run on every cog load.
        await conn.execute(
            """
            INSERT INTO fable_campaign_runs (
                run_id, user_id, campaign_key, run_kind, is_canonical,
                content_version, status, current_node_key, history_json,
                choices_json, unlocks_json, campaign_json_snapshot,
                started_at, updated_at, completed_at
            )
            SELECT
                md5(pc.user_id::text || ':' || pc.campaign_key),
                pc.user_id, pc.campaign_key, 'canonical', TRUE, 1,
                pc.status, pc.current_node_key, pc.history_json,
                pc.choices_json, pc.unlocks_json,
                COALESCE(cc.campaign_json, '{}'), pc.started_at,
                pc.updated_at, pc.completed_at
            FROM player_campaigns pc
            LEFT JOIN campaign_content cc ON cc.campaign_key = pc.campaign_key
            ON CONFLICT DO NOTHING
            """
        )
        await conn.execute(
            """
            UPDATE fable_campaign_runs fcr
            SET campaign_json_snapshot = cc.campaign_json
            FROM campaign_content cc
            WHERE fcr.campaign_key = cc.campaign_key
              AND COALESCE(fcr.campaign_json_snapshot, '{}') = '{}'
            """
        )

    def new_run_id(self) -> str:
        return str(uuid.uuid4())

    async def canonical_run_id(self, conn, user_id: int, campaign_key: str) -> str | None:
        return await conn.fetchval(
            """
            SELECT run_id
            FROM fable_campaign_runs
            WHERE user_id=$1 AND campaign_key=$2 AND is_canonical=TRUE
            """,
            user_id,
            campaign_key,
        )

    async def get_state(
        self,
        conn,
        *,
        user_id: int,
        campaign_key: str,
        state_key: object,
        run_id: str | None = None,
    ) -> tuple[bool, Any]:
        key = normalize_state_key(state_key)
        if not key:
            raise FableStateError("Story-state conditions need a key.")
        resolved_run = run_id or await self.canonical_run_id(conn, user_id, campaign_key)
        if not resolved_run:
            return False, None
        raw = await conn.fetchval(
            "SELECT value_json FROM fable_run_state WHERE run_id=$1 AND state_key=$2",
            resolved_run,
            key,
        )
        if raw is None:
            return False, None
        return True, json.loads(str(raw))

    async def snapshot(self, conn, run_id: str) -> dict[str, Any]:
        rows = await conn.fetch(
            "SELECT state_key, value_json FROM fable_run_state WHERE run_id=$1 ORDER BY state_key",
            run_id,
        )
        return {str(row["state_key"]): json.loads(str(row["value_json"])) for row in rows}

    async def grant_reward(
        self,
        conn,
        *,
        user_id: int,
        reward_type: object,
        reward_key: object,
        display_name: str = "",
        source: str = "campaign",
        metadata: dict | None = None,
    ) -> bool:
        normalized_type = normalize_state_key(reward_type).replace(".", "_")
        normalized_key = normalize_state_key(reward_key).replace(".", "_")
        if not normalized_type or not normalized_key:
            raise FableStateError("Fable rewards need a type and key.")
        inserted = await conn.fetchval(
            """
            INSERT INTO player_fable_rewards (
                user_id, reward_type, reward_key, display_name,
                source, metadata_json, unlocked_at
            ) VALUES ($1,$2,$3,$4,$5,$6,NOW())
            ON CONFLICT (user_id, reward_type, reward_key) DO NOTHING
            RETURNING reward_key
            """,
            user_id,
            normalized_type,
            normalized_key,
            str(display_name or ""),
            str(source or "campaign"),
            json.dumps(metadata or {}, allow_nan=False, sort_keys=True),
        )
        return bool(inserted)

    async def has_reward(
        self,
        conn,
        *,
        user_id: int,
        reward_type: object,
        reward_key: object,
    ) -> bool:
        normalized_type = normalize_state_key(reward_type).replace(".", "_")
        normalized_key = normalize_state_key(reward_key).replace(".", "_")
        if not normalized_type or not normalized_key:
            return False
        return bool(
            await conn.fetchval(
                """
                SELECT 1 FROM player_fable_rewards
                WHERE user_id=$1 AND reward_type=$2 AND reward_key=$3
                """,
                user_id,
                normalized_type,
                normalized_key,
            )
        )

    async def list_rewards(
        self,
        conn,
        *,
        user_id: int,
        reward_type: object = "",
    ) -> list[dict]:
        normalized_type = normalize_state_key(reward_type).replace(".", "_")
        rows = await conn.fetch(
            """
            SELECT reward_type, reward_key, display_name, source,
                   metadata_json, unlocked_at
            FROM player_fable_rewards
            WHERE user_id=$1 AND ($2='' OR reward_type=$2)
            ORDER BY reward_type, unlocked_at, reward_key
            """,
            user_id,
            normalized_type,
        )
        return [dict(row) for row in rows]

    async def apply_effects(
        self,
        conn,
        *,
        user_id: int,
        campaign_key: str,
        effects: list[dict],
        event_key: str,
        source: str,
        run_id: str | None = None,
        metadata: dict | None = None,
    ) -> list[StateMutation]:
        state_effects = [
            effect
            for effect in effects or []
            if isinstance(effect, dict)
            and str(effect.get("type") or "").strip().lower() in STATE_EFFECT_TYPES
        ]
        if not state_effects:
            return []
        if not event_key:
            raise FableStateError("State-changing campaign effects require an event key.")
        resolved_run = run_id or await self.canonical_run_id(conn, user_id, campaign_key)
        if not resolved_run:
            raise FableStateError("No canonical campaign run exists for this story-state effect.")

        inserted = await conn.fetchval(
            """
            INSERT INTO fable_processed_events (run_id, event_key, source, metadata_json)
            VALUES ($1,$2,$3,$4)
            ON CONFLICT (run_id, event_key) DO NOTHING
            RETURNING event_key
            """,
            resolved_run,
            event_key,
            source,
            json.dumps(metadata or {}, allow_nan=False, sort_keys=True),
        )
        if not inserted:
            return []

        mutations: list[StateMutation] = []
        for effect_index, effect in enumerate(state_effects):
            effect_type = str(effect.get("type") or "").strip().lower()
            key = normalize_state_key(effect.get("key"))
            if not key:
                raise FableStateError(f"Effect `{effect_type}` needs a story-state key.")
            row = await conn.fetchrow(
                """
                SELECT value_type, value_json, version
                FROM fable_run_state
                WHERE run_id=$1 AND state_key=$2
                FOR UPDATE
                """,
                resolved_run,
                key,
            )
            before = json.loads(str(row["value_json"])) if row else None
            deleted = effect_type == "state_delete"

            if effect_type == "state_set":
                value_type, after = coerce_state_value(
                    effect.get("value"), effect.get("value_type")
                )
            elif effect_type == "state_increment":
                delta = effect.get("delta", effect.get("value", 1))
                if isinstance(delta, bool) or not isinstance(delta, (int, float)):
                    raise FableStateError(f"State increment `{key}` needs a numeric delta.")
                if before is not None and (
                    isinstance(before, bool) or not isinstance(before, (int, float))
                ):
                    raise FableStateError(f"State `{key}` is not numeric and cannot be incremented.")
                after = (before or 0) + delta
                minimum = effect.get("minimum")
                maximum = effect.get("maximum")
                if minimum is not None:
                    if isinstance(minimum, bool) or not isinstance(minimum, (int, float)):
                        raise FableStateError(f"State increment `{key}` has an invalid minimum.")
                    after = max(minimum, after)
                if maximum is not None:
                    if isinstance(maximum, bool) or not isinstance(maximum, (int, float)):
                        raise FableStateError(f"State increment `{key}` has an invalid maximum.")
                    after = min(maximum, after)
                value_type, after = coerce_state_value(after)
            elif effect_type == "state_add":
                if before is not None and not isinstance(before, list):
                    raise FableStateError(f"State `{key}` is not a list and cannot receive entries.")
                current = list(before) if isinstance(before, list) else []
                value = copy.deepcopy(effect.get("value"))
                if value not in current:
                    current.append(value)
                value_type, after = coerce_state_value(current, "list")
            elif effect_type == "state_remove":
                if before is not None and not isinstance(before, list):
                    raise FableStateError(f"State `{key}` is not a list and cannot remove entries.")
                current = list(before) if isinstance(before, list) else []
                value = effect.get("value")
                after = [entry for entry in current if entry != value]
                value_type, after = coerce_state_value(after, "list")
            else:
                value_type, after = "null", None

            if deleted:
                await conn.execute(
                    "DELETE FROM fable_run_state WHERE run_id=$1 AND state_key=$2",
                    resolved_run,
                    key,
                )
            else:
                await conn.execute(
                    """
                    INSERT INTO fable_run_state (
                        run_id, state_key, value_type, value_json, version, updated_at
                    ) VALUES ($1,$2,$3,$4,1,NOW())
                    ON CONFLICT (run_id, state_key) DO UPDATE SET
                        value_type=EXCLUDED.value_type,
                        value_json=EXCLUDED.value_json,
                        version=fable_run_state.version + 1,
                        updated_at=NOW()
                    """,
                    resolved_run,
                    key,
                    value_type,
                    json.dumps(after, allow_nan=False, sort_keys=True),
                )
            await conn.execute(
                """
                INSERT INTO fable_state_events (
                    run_id, user_id, event_key, effect_index, state_key,
                    effect_type, before_json, after_json, source
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                """,
                resolved_run,
                user_id,
                event_key,
                effect_index,
                key,
                effect_type,
                json.dumps(before, allow_nan=False, sort_keys=True),
                None if deleted else json.dumps(after, allow_nan=False, sort_keys=True),
                source,
            )
            mutations.append(StateMutation(key=key, before=before, after=after, deleted=deleted))
        return mutations
