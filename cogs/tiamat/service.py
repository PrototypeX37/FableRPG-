from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass


GOLD_PER_CROWN = 1_000
LINK_CODE_TTL_MINUTES = 15
LINK_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def normalize_link_code(value: str) -> str:
    return "".join(character for character in str(value).upper() if character.isalnum())


def hash_link_code(value: str) -> str:
    normalized = normalize_link_code(value)
    return hashlib.sha256(normalized.encode("ascii")).hexdigest()


def generate_link_code() -> str:
    body = "".join(secrets.choice(LINK_ALPHABET) for _ in range(8))
    return f"TIAMAT-{body[:4]}-{body[4:]}"


@dataclass(frozen=True)
class ConversionResult:
    crowns_added: int
    crown_balance: int
    gold_balance: int
    request_id: uuid.UUID


async def issue_link_code(pool, profile_user: int) -> tuple[str, object | None]:
    """Return a fresh one-use code, or the existing linked RPG profile."""
    code = generate_link_code()
    digest = hash_link_code(code)
    async with pool.acquire() as connection:
        async with connection.transaction():
            linked = await connection.fetchrow(
                """
                SELECT game_username, display_name, crowns
                FROM tiamat_players
                WHERE profile_user=$1
                """,
                profile_user,
            )
            if linked:
                return "", linked
            exists = await connection.fetchval(
                'SELECT EXISTS(SELECT 1 FROM profile WHERE "user"=$1)', profile_user
            )
            if not exists:
                raise LookupError("A Fable profile is required before linking Tiamat.")
            await connection.execute(
                "DELETE FROM tiamat_link_codes WHERE expires_at <= NOW()"
            )
            await connection.execute(
                """
                INSERT INTO tiamat_link_codes (profile_user, code_hash, expires_at)
                VALUES ($1, $2, NOW() + ($3 * INTERVAL '1 minute'))
                ON CONFLICT (profile_user) DO UPDATE
                SET code_hash=EXCLUDED.code_hash,
                    expires_at=EXCLUDED.expires_at,
                    created_at=NOW()
                """,
                profile_user,
                digest,
                LINK_CODE_TTL_MINUTES,
            )
    return code, None


async def revoke_link_code(pool, profile_user: int, code: str) -> None:
    async with pool.acquire() as connection:
        await connection.execute(
            "DELETE FROM tiamat_link_codes WHERE profile_user=$1 AND code_hash=$2",
            profile_user,
            hash_link_code(code),
        )


async def fetch_rpg_profile(pool, profile_user: int):
    async with pool.acquire() as connection:
        return await connection.fetchrow(
            """
            SELECT p.money, t.game_username, t.display_name, t.crowns,
                   t.total_converted_gold, t.dragon_hue, t.last_map_id,
                   t.last_map_name,
                   t.actor_level, t.current_hp, t.max_hp, t.current_mp,
                   t.max_mp, t.experience, t.playtime_seconds,
                   t.battles, t.wins, t.escapes,
                   t.linked_at, t.last_seen_at
            FROM profile p
            LEFT JOIN tiamat_players t ON t.profile_user=p."user"
            WHERE p."user"=$1
            """,
            profile_user,
        )


async def convert_gold_to_crowns(
    pool, profile_user: int, crowns: int, *, request_id: uuid.UUID | None = None
) -> ConversionResult:
    if isinstance(crowns, bool) or not isinstance(crowns, int) or crowns <= 0:
        raise ValueError("Enter a positive whole number of crowns.")
    if crowns > 1_000_000:
        raise ValueError("A single conversion cannot exceed 1,000,000 crowns.")
    request_id = request_id or uuid.uuid4()
    gold_cost = crowns * GOLD_PER_CROWN
    async with pool.acquire() as connection:
        async with connection.transaction():
            linked = await connection.fetchrow(
                """
                SELECT crowns FROM tiamat_players
                WHERE profile_user=$1
                FOR UPDATE
                """,
                profile_user,
            )
            if not linked:
                raise LookupError("Link an RPG account before converting gold.")
            gold_balance = await connection.fetchval(
                'SELECT money FROM profile WHERE "user"=$1 FOR UPDATE', profile_user
            )
            if gold_balance is None or gold_balance < gold_cost:
                available = max(0, int(gold_balance or 0) // GOLD_PER_CROWN)
                raise ValueError(f"You can currently convert at most {available:,} crowns.")
            gold_balance = await connection.fetchval(
                'UPDATE profile SET money=money-$1 WHERE "user"=$2 RETURNING money',
                gold_cost,
                profile_user,
            )
            crown_balance = await connection.fetchval(
                """
                UPDATE tiamat_players
                SET crowns=crowns+$1,
                    total_converted_gold=total_converted_gold+$2
                WHERE profile_user=$3
                RETURNING crowns
                """,
                crowns,
                gold_cost,
                profile_user,
            )
            await connection.execute(
                """
                INSERT INTO tiamat_crown_ledger
                    (profile_user, kind, gold_delta, crown_delta,
                     crown_balance_after, request_id, metadata)
                VALUES ($1, 'fable_to_crowns', $2, $3, $4, $5,
                        jsonb_build_object(
                            'gold_per_crown', $6::BIGINT,
                            'source', 'discord'
                        ))
                """,
                profile_user,
                -gold_cost,
                crowns,
                crown_balance,
                request_id,
                GOLD_PER_CROWN,
            )
    return ConversionResult(crowns, crown_balance, gold_balance, request_id)
