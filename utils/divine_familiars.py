from __future__ import annotations

import datetime
import random
import re
from typing import Any, Optional


DIVINE_SHARDS_PER_EGG = 20
DIVINE_EGG_HATCH_MINUTES = 2160  # 36 hours
DIVINE_SHARD_PITY_TIERS: tuple[tuple[int, int], ...] = (
    (60, 2),
    (80, 3),
    (100, 5),
)
DIVINE_SHARD_MAX_PITY_THRESHOLD = max(
    threshold for threshold, _ in DIVINE_SHARD_PITY_TIERS
)


# NOTE: Replace names/urls/stats with final design values as needed.
DIVINE_FAMILIARS: dict[str, dict[str, Any]] = {
    "astraea_familiar": {
        "name": "Sorinveil",
        "cosmetic_name": "Σόρινβεϊλ",
        "element": "Light",
        "url": "https://imgur.com/a/verPClU.gif",
        "base_hp": 25000,
        "base_attack": 3500,
        "base_defense": 4500,
        "god": "Astraea",
    },
    "sepulchure_familiar": {
        "name": "Vaion",
        "cosmetic_name": "Βαΐον",
        "element": "Dark",
        "url": "https://imgur.com/a/n71pS8z.gif",
        "base_hp": 15000,
        "base_attack": 7000,
        "base_defense": 2500,
        "god": "Sepulchure",
    },
    "drakath_familiar": {
        "name": "Astrephiel",
        "cosmetic_name": "Αστρεφιήλ",
        "element": "Corrupted",
        "url": "https://imgur.com/a/PiTd9KN.gif",
        "base_hp": 18000,
        "base_attack": 4000,
        "base_defense": 6000,
        "god": "Drakath",
    },
    "primordial_familiar": {
        "name": "Mayeia",
        "cosmetic_name": "Μαγεία",
        "element": "Fire",
        "url": "https://imgur.com/a/36oYG18.gif",
        "base_hp": 19000,
        "base_attack": 5000,
        "base_defense": 5000,
        "god": None,
    },
}


GOD_TO_FAMILIAR_KEY = {
    "Astraea": "astraea_familiar",
    "Sepulchure": "sepulchure_familiar",
    "Drakath": "drakath_familiar",
}


LEGACY_FAMILIAR_NAME_ALIASES: dict[str, str] = {
    "Astraea Familiar": "astraea_familiar",
    "Sepulchure Familiar": "sepulchure_familiar",
    "Drakath Familiar": "drakath_familiar",
    "Primordial Familiar": "primordial_familiar",
}


def format_familiar_display_name(
    familiar: dict[str, Any], *, include_cosmetic: bool = True
) -> str:
    name = str(familiar.get("name", "Unknown Familiar")).strip()
    if not include_cosmetic:
        return name
    cosmetic_name = str(familiar.get("cosmetic_name", "")).strip()
    if cosmetic_name:
        return f"{name} ({cosmetic_name})"
    return name


def get_familiar_display_name(
    familiar_key: str, *, include_cosmetic: bool = True
) -> str:
    familiar = DIVINE_FAMILIARS.get(familiar_key)
    if not familiar:
        return familiar_key
    return format_familiar_display_name(
        familiar, include_cosmetic=include_cosmetic
    )


DIVINE_FAMILIAR_NAMES = frozenset(
    str(cfg["name"]) for cfg in DIVINE_FAMILIARS.values()
) | frozenset(LEGACY_FAMILIAR_NAME_ALIASES.keys())


def _normalize_token(value: str) -> str:
    normalized = value.strip().lower()
    normalized = normalized.replace("-", " ").replace("_", " ")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _build_aliases() -> dict[str, str]:
    aliases: dict[str, str] = {}
    for key, familiar in DIVINE_FAMILIARS.items():
        name = str(familiar["name"])
        normalized_name = _normalize_token(name)
        compact_name = normalized_name.replace(" ", "")
        cosmetic_name = str(familiar.get("cosmetic_name", "")).strip()

        aliases[key] = key
        aliases[key.lower()] = key
        aliases[_normalize_token(key)] = key
        aliases[_normalize_token(key).replace(" ", "")] = key
        aliases[normalized_name] = key
        aliases[compact_name] = key
        if cosmetic_name:
            normalized_cosmetic = _normalize_token(cosmetic_name)
            aliases[normalized_cosmetic] = key
            aliases[normalized_cosmetic.replace(" ", "")] = key

    for legacy_name, key in LEGACY_FAMILIAR_NAME_ALIASES.items():
        normalized_legacy = _normalize_token(legacy_name)
        aliases[normalized_legacy] = key
        aliases[normalized_legacy.replace(" ", "")] = key

    return aliases


_FAMILIAR_ALIASES = _build_aliases()


def resolve_familiar_key(value: str) -> Optional[str]:
    if not value:
        return None
    normalized = _normalize_token(value)
    return _FAMILIAR_ALIASES.get(normalized) or _FAMILIAR_ALIASES.get(
        normalized.replace(" ", "")
    )


def familiar_key_for_god(god: Optional[str]) -> Optional[str]:
    if not god:
        return None
    return GOD_TO_FAMILIAR_KEY.get(str(god))


def choose_familiar_key(preferred_god: Optional[str] = None, bias: float = 0.65) -> str:
    # Legacy parameters kept for backwards compatibility.
    # Familiar selection is intentionally uniform (25% each for the 4 familiars).
    _ = preferred_god
    _ = bias
    return random.choice(list(DIVINE_FAMILIARS.keys()))


def get_divine_shard_pity_state(
    base_chance_pct: int,
    no_shard_wins: int,
    pity_tiers: tuple[tuple[int, int], ...] = DIVINE_SHARD_PITY_TIERS,
) -> dict[str, Any]:
    base_chance = max(0, min(100, int(base_chance_pct or 0)))
    miss_streak = max(0, int(no_shard_wins or 0))
    normalized_tiers = sorted(
        (max(0, int(threshold)), max(1, int(multiplier)))
        for threshold, multiplier in pity_tiers
    )

    pity_multiplier = 1
    active_threshold: Optional[int] = None
    next_threshold: Optional[int] = None
    next_multiplier: Optional[int] = None

    for threshold, multiplier in normalized_tiers:
        if miss_streak >= threshold:
            pity_multiplier = multiplier
            active_threshold = threshold
            continue
        if next_threshold is None:
            next_threshold = threshold
            next_multiplier = multiplier

    effective_chance_pct = min(100, int(base_chance * pity_multiplier))
    return {
        "base_chance_pct": base_chance,
        "effective_chance_pct": effective_chance_pct,
        "pity_multiplier": pity_multiplier,
        "pity_active": pity_multiplier > 1,
        "active_threshold": active_threshold,
        "next_threshold": next_threshold,
        "next_multiplier": next_multiplier,
        "max_pity_threshold": (
            max((threshold for threshold, _ in normalized_tiers), default=0)
        ),
    }


def _allocate_iv_points(total_points: int) -> tuple[int, int, int]:
    a = random.random()
    b = random.random()
    c = random.random()
    denom = a + b + c

    hp_iv = int(round(total_points * (a / denom)))
    attack_iv = int(round(total_points * (b / denom)))
    defense_iv = int(round(total_points * (c / denom)))

    diff = int(round(total_points)) - (hp_iv + attack_iv + defense_iv)
    if diff:
        if hp_iv >= attack_iv and hp_iv >= defense_iv:
            hp_iv += diff
        elif attack_iv >= defense_iv:
            attack_iv += diff
        else:
            defense_iv += diff
    return hp_iv, attack_iv, defense_iv


def roll_familiar_egg_stats(familiar_key: str) -> dict[str, Any]:
    familiar = DIVINE_FAMILIARS[familiar_key]

    iv_percent = random.uniform(30, 100)
    total_iv_points = int(round((iv_percent / 100.0) * 180))
    hp_iv, attack_iv, defense_iv = _allocate_iv_points(total_iv_points)

    hp = int(familiar["base_hp"]) + hp_iv
    attack = int(familiar["base_attack"]) + attack_iv
    defense = int(familiar["base_defense"]) + defense_iv

    return {
        "egg_type": familiar["name"],
        "element": familiar["element"],
        "url": familiar.get("url"),
        "iv_percent": iv_percent,
        "hp": hp,
        "attack": attack,
        "defense": defense,
        "hp_iv": hp_iv,
        "attack_iv": attack_iv,
        "defense_iv": defense_iv,
    }


async def ensure_divine_familiar_tables(conn) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS divine_familiar_shards (
            user_id BIGINT NOT NULL,
            familiar_key TEXT NOT NULL,
            shards INTEGER NOT NULL DEFAULT 0,
            updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
            PRIMARY KEY (user_id, familiar_key)
        );
        """
    )


async def get_divine_shard_counts(conn, user_id: int) -> dict[str, int]:
    await ensure_divine_familiar_tables(conn)
    rows = await conn.fetch(
        """
        SELECT familiar_key, shards
        FROM divine_familiar_shards
        WHERE user_id = $1;
        """,
        user_id,
    )
    counts = {key: 0 for key in DIVINE_FAMILIARS.keys()}
    for row in rows:
        key = str(row["familiar_key"])
        if key in counts:
            counts[key] = int(row["shards"] or 0)
    return counts


async def award_divine_shards(
    conn,
    user_id: int,
    familiar_key: str,
    amount: int = 1,
) -> int:
    await ensure_divine_familiar_tables(conn)
    key = resolve_familiar_key(familiar_key) or familiar_key
    if key not in DIVINE_FAMILIARS:
        raise ValueError(f"Unknown familiar key: {familiar_key}")
    amount = max(0, int(amount))
    if amount <= 0:
        current = await conn.fetchval(
            """
            SELECT shards
            FROM divine_familiar_shards
            WHERE user_id = $1 AND familiar_key = $2;
            """,
            user_id,
            key,
        )
        return int(current or 0)

    return int(
        await conn.fetchval(
            """
            INSERT INTO divine_familiar_shards (user_id, familiar_key, shards)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id, familiar_key)
            DO UPDATE
            SET shards = divine_familiar_shards.shards + EXCLUDED.shards,
                updated_at = NOW()
            RETURNING shards;
            """,
            user_id,
            key,
            amount,
        )
    )


async def craft_divine_egg(
    conn,
    user_id: int,
    familiar_key: str,
    shards_required: int = DIVINE_SHARDS_PER_EGG,
) -> dict[str, Any]:
    await ensure_divine_familiar_tables(conn)

    key = resolve_familiar_key(familiar_key) or familiar_key
    if key not in DIVINE_FAMILIARS:
        return {"ok": False, "error": "unknown_familiar", "remaining": 0}

    cost = max(1, int(shards_required))
    remaining = await conn.fetchval(
        """
        UPDATE divine_familiar_shards
        SET shards = shards - $1,
            updated_at = NOW()
        WHERE user_id = $2
          AND familiar_key = $3
          AND shards >= $1
        RETURNING shards;
        """,
        cost,
        user_id,
        key,
    )

    if remaining is None:
        current = await conn.fetchval(
            """
            SELECT shards
            FROM divine_familiar_shards
            WHERE user_id = $1 AND familiar_key = $2;
            """,
            user_id,
            key,
        )
        return {"ok": False, "error": "not_enough_shards", "remaining": int(current or 0)}

    stats = roll_familiar_egg_stats(key)
    hatch_time = datetime.datetime.utcnow() + datetime.timedelta(
        minutes=DIVINE_EGG_HATCH_MINUTES
    )

    egg_id = await conn.fetchval(
        """
        INSERT INTO monster_eggs (
            user_id,
            egg_type,
            hp,
            attack,
            defense,
            element,
            url,
            hatch_time,
            "IV",
            hatched
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, FALSE)
        RETURNING id;
        """,
        user_id,
        stats["egg_type"],
        stats["hp"],
        stats["attack"],
        stats["defense"],
        stats["element"],
        stats["url"],
        hatch_time,
        stats["iv_percent"],
    )

    return {
        "ok": True,
        "familiar_key": key,
        "familiar": DIVINE_FAMILIARS[key],
        "egg_id": int(egg_id),
        "remaining": int(remaining),
        "stats": stats,
    }
