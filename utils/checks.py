"""
The IdleRPG Discord Bot
Copyright (C) 2018-2021 Diniboy and Gelbpunkt
Copyright (C) 2024 Lunar (discord itslunar.)
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


import datetime

from typing import TYPE_CHECKING

import discord
import pytz

from discord.ext import commands

from classes.classes import GameClass, Ranger
from classes.classes import from_string
from classes.classes import from_string as class_from_string
from classes.classes import get_name
from classes.context import Context
from classes.enums import DonatorRank
from utils import random

if TYPE_CHECKING:
    from discord.ext.commands.core import _CheckDecorator

    from classes.bot import Bot


class NoCharacter(commands.CheckFailure):
    """Exception raised when a user has no character."""

    pass


class NeedsNoCharacter(commands.CheckFailure):
    """Exception raised when a command requires you to have no character."""

    pass


class NoGuild(commands.CheckFailure):
    """Exception raised when a user has no guild."""

    pass


class NeedsNoGuild(commands.CheckFailure):
    """Exception raised when a user needs to be in no guild."""

    pass


class NoGuildPermissions(commands.CheckFailure):
    """Exception raised when a user does not have permissions because he is missing roles."""

    pass


class NeedsNoGuildLeader(commands.CheckFailure):
    """Exception raised when a user is guild leader and can't use the command therefore."""

    pass


class NoAlliancePermissions(commands.CheckFailure):
    """Exception raised when a user does not have permissions in the alliance to use a command."""

    pass


class NoCityOwned(commands.CheckFailure):
    """Exception raised when an alliance does not control a city."""

    pass


class CityOwned(commands.CheckFailure):
    """Exception raised when an alliance controls a city."""

    pass


class WrongClass(commands.CheckFailure):
    """Exception raised when a user does not meet the class requirement."""

    pass


class NeedsNoAdventure(commands.CheckFailure):
    """Exception raised when a user needs to be on no adventure."""

    pass


class NeedsAdventure(commands.CheckFailure):
    """Exception raised when a user needs to be on an adventure."""

    pass


class NoPatron(commands.CheckFailure):
    """Exception raised when you need to donate to use a command."""

    def __init__(self, tier: DonatorRank) -> None:
        self.tier = tier


class NeedsGod(commands.CheckFailure):
    """Exception raised when you need to have a god to use a command."""

    pass


class PetGone(commands.CheckFailure):
    """Exception raised in case of the pet purgatory bug."""

    pass


class PetDied(commands.CheckFailure):
    """Exception raised when the pet died."""

    pass


class PetRanAway(commands.CheckFailure):
    """Exception raised when the pet ran away."""

    pass


class AlreadyRaiding(commands.CheckFailure):
    """Exception raised when a user tries starting a raid while another is ongoing."""

    pass


class NoOpenHelpRequest(commands.CheckFailure):
    """Exception raised when a user tries to edit/remove an open help request but none exists."""

    pass


class ImgurUploadError(commands.CheckFailure):
    """Exception raised when an Imgur upload failed to return a short URL."""

    pass


def has_char() -> "_CheckDecorator":
    """Checks for a user to have a character."""

    async def predicate(ctx: Context) -> bool:
        ctx.character_data = await ctx.bot.pool.fetchrow(
            'SELECT * FROM profile WHERE "user"=$1;', ctx.author.id
        )
        if ctx.character_data:
            return True
        raise NoCharacter()

    return commands.check(predicate)


def has_no_char() -> "_CheckDecorator":
    """Checks for a user to have no character."""

    async def predicate(ctx: Context) -> bool:
        if await ctx.bot.pool.fetchrow(
                'SELECT * FROM profile WHERE "user"=$1;', ctx.author.id
        ):
            raise NeedsNoCharacter()
        return True

    return commands.check(predicate)


def has_adventure() -> "_CheckDecorator":
    """Checks for a user to be on an adventure."""

    async def predicate(ctx: Context) -> bool:
        ctx.adventure_data = await ctx.bot.get_adventure(ctx.author)
        if ctx.adventure_data:
            return True
        raise NeedsAdventure()

    return commands.check(predicate)


def has_no_adventure() -> "_CheckDecorator":
    """Checks for a user to be on no adventure."""

    async def predicate(ctx: Context) -> bool:
        if not await ctx.bot.get_adventure(ctx.author):
            return True
        raise NeedsNoAdventure()

    return commands.check(predicate)


def has_no_guild() -> "_CheckDecorator":
    """Checks for a user to be in no guild."""

    async def predicate(ctx: Context) -> bool:
        if not hasattr(ctx, "character_data"):
            ctx.character_data = await ctx.bot.pool.fetchrow(
                'SELECT * FROM profile WHERE "user"=$1;', ctx.author.id
            )
        if not ctx.character_data["guild"]:
            return True
        raise NeedsNoGuild()

    return commands.check(predicate)


def has_guild() -> "_CheckDecorator":
    """Checks for a user to be in a guild."""

    async def predicate(ctx: Context) -> bool:
        if not hasattr(ctx, "character_data"):
            ctx.character_data = await ctx.bot.pool.fetchrow(
                'SELECT * FROM profile WHERE "user"=$1;', ctx.author.id
            )
        if ctx.character_data and ctx.character_data["guild"]:
            return True
        raise NoGuild()

    return commands.check(predicate)


def is_guild_officer() -> "_CheckDecorator":
    """Checks for a user to be guild officer or leader."""

    async def predicate(ctx: Context) -> bool:
        if not hasattr(ctx, "character_data"):
            ctx.character_data = await ctx.bot.pool.fetchrow(
                'SELECT * FROM profile WHERE "user"=$1;', ctx.author.id
            )
        if (
                ctx.character_data["guildrank"] == "Leader"
                or ctx.character_data["guildrank"] == "Officer"
        ):
            return True
        raise NoGuildPermissions()

    return commands.check(predicate)


def is_guild_leader() -> "_CheckDecorator":
    """Checks for a user to be guild leader."""

    async def predicate(ctx: Context) -> bool:
        if not hasattr(ctx, "character_data"):
            ctx.character_data = await ctx.bot.pool.fetchrow(
                'SELECT * FROM profile WHERE "user"=$1;', ctx.author.id
            )
        if ctx.character_data["guildrank"] == "Leader":
            return True
        raise NoGuildPermissions()

    return commands.check(predicate)


def is_no_guild_leader() -> "_CheckDecorator":
    """Checks for a user not to be guild leader."""

    async def predicate(ctx: Context) -> bool:
        if not hasattr(ctx, "character_data"):
            ctx.character_data = await ctx.bot.pool.fetchrow(
                'SELECT * FROM profile WHERE "user"=$1;', ctx.author.id
            )
        if ctx.character_data["guildrank"] != "Leader":
            return True
        raise NeedsNoGuildLeader()

    return commands.check(predicate)


def is_alliance_leader() -> "_CheckDecorator":
    """Checks for a user to be the leader of an alliance."""

    async def predicate(ctx: Context) -> bool:

        async with ctx.bot.pool.acquire() as conn:
            if not hasattr(ctx, "character_data"):
                ctx.character_data = await conn.fetchrow(
                    'SELECT * FROM profile WHERE "user"=$1;', ctx.author.id
                )
            leading_guild = await conn.fetchval(
                'SELECT alliance FROM guild WHERE "id"=$1;', ctx.character_data["guild"]
            )
        if (
                leading_guild == ctx.character_data["guild"]
                and ctx.character_data["guildrank"] == "Leader"
        ):
            return True
        raise NoAlliancePermissions()

    return commands.check(predicate)


def owns_city() -> "_CheckDecorator":
    """Checks whether an alliance owns a city."""

    async def predicate(ctx: Context) -> bool:
        async with ctx.bot.pool.acquire() as conn:
            if not hasattr(ctx, "character_data"):
                ctx.character_data = await conn.fetchrow(
                    'SELECT * FROM profile WHERE "user"=$1;', ctx.author.id
                )
            alliance = await conn.fetchval(
                'SELECT alliance FROM guild WHERE "id"=$1', ctx.character_data["guild"]
            )
            owned_city = await conn.fetchval(
                'SELECT name FROM city WHERE "owner"=$1', alliance
            )
            if not owned_city:
                raise NoCityOwned()
            ctx.city = owned_city
            return True

    return commands.check(predicate)


def owns_no_city() -> "_CheckDecorator":
    """Checks whether an alliance owns no city."""

    async def predicate(ctx: Context) -> bool:
        async with ctx.bot.pool.acquire() as conn:
            if not hasattr(ctx, "character_data"):
                ctx.character_data = await conn.fetchrow(
                    'SELECT * FROM profile WHERE "user"=$1;', ctx.author.id
                )
            alliance = await conn.fetchval(
                'SELECT alliance FROM guild WHERE "id"=$1', ctx.character_data["guild"]
            )
            owned_city = await conn.fetchval(
                'SELECT name FROM city WHERE "owner"=$1', alliance
            )
            if owned_city:
                raise CityOwned()
            return True

    return commands.check(predicate)


def is_class(class_: type[GameClass]) -> "_CheckDecorator":
    """Checks for a user to be in a class line."""

    async def predicate(ctx: Context) -> bool:
        if not hasattr(ctx, "character_data"):
            ctx.character_data = await ctx.bot.pool.fetchrow(
                'SELECT * FROM profile WHERE "user"=$1;', ctx.author.id
            )
        classes = [
            c for i in ctx.character_data["class"] if (c := class_from_string(i))
        ]
        any_in_line = any([c.in_class_line(class_) for c in classes])
        if class_ == Ranger and any_in_line:
            ctx.pet_data = await ctx.bot.pool.fetchrow(
                'SELECT * FROM pets WHERE "user"=$1;', ctx.author.id
            )
        if not any_in_line:
            raise WrongClass(get_name(class_))
        return True

    return commands.check(predicate)


def is_nothing(ctx: Context) -> bool:
    """Checks whether the player is still on an initial race with no CV chosen."""
    if ctx.character_data["race"] == "Human" and ctx.character_data["cv"] == -1:
        return True
    return False


def has_god() -> "_CheckDecorator":
    """Checks for a user to have a god."""

    async def predicate(ctx: Context) -> bool:
        if not hasattr(ctx, "character_data"):
            ctx.character_data = await ctx.bot.pool.fetchrow(
                'SELECT * FROM profile WHERE "user"=$1;', ctx.author.id
            )
        if ctx.character_data["god"]:
            return True
        raise NeedsGod()

    return commands.check(predicate)


def has_no_god(ctx: Context) -> bool:
    """Checks for a user to have no god."""
    if not ctx.character_data["god"]:
        return True
    return False


def update_pet() -> "_CheckDecorator":
    async def predicate(ctx: Context) -> bool:
        if not ctx.pet_data:
            raise PetGone()
        diff = (
                       (now := datetime.datetime.now(pytz.utc)) - ctx.pet_data["last_update"]
               ) // datetime.timedelta(hours=2)
        if diff >= 1:
            # Pets loose 2 food, 4 drinks, 1 joy and 1 love
            async with ctx.bot.pool.acquire() as conn:
                data = await conn.fetchrow(
                    'UPDATE pets SET "food"="food"-$1, "drink"="drink"-$2, "joy"=CASE'
                    ' WHEN "joy"-$3>=0 THEN "joy"-$3 ELSE 0 END, "love"=CASE WHEN'
                    ' "love"-$4>=0 THEN "love"-$4 ELSE 0 END, "last_update"=$5 WHERE'
                    ' "user"=$6 RETURNING *;',
                    diff * 2,
                    diff * 4,
                    diff,
                    diff,
                    now,
                    ctx.author.id,
                )
                ctx.pet_data = data
                classes = ctx.character_data["class"]
                for c_idx, class_ in enumerate(classes):
                    class_real = from_string(class_)
                    if class_real.get_class_line() == Ranger:
                        idx = c_idx
                        break
                if data["food"] < 0 or data["drink"] < 0:
                    classes[idx] = "No Class"
                    await conn.execute(
                        'DELETE FROM pets WHERE "user"=$1;', ctx.author.id
                    )
                    await conn.execute(
                        'UPDATE profile SET "class"=$1 WHERE "user"=$2;',
                        classes,
                        ctx.author.id,
                    )
                    raise PetDied()
                elif data["love"] < 75 and random.randint(0, 99) > data["love"]:
                    classes[idx] = "No Class"
                    await conn.execute(
                        'DELETE FROM pets WHERE "user"=$1;', ctx.author.id
                    )
                    await conn.execute(
                        'UPDATE profile SET "class"=$1 WHERE "user"=$2;',
                        classes,
                        ctx.author.id,
                    )
                    raise PetRanAway()
        return True

    return commands.check(predicate)


def is_god() -> "_CheckDecorator":
    """Checks for a user to be a god."""

    def predicate(ctx: Context) -> bool:
        return ctx.author.id in ctx.bot.gods

    return commands.check(predicate)


# TODO: Pass context here and assign there?


async def has_guild_(bot: "Bot", userid: int) -> bool:
    return bool(
        await bot.pool.fetchval('SELECT guild FROM profile WHERE "user"=$1;', userid)
    )


async def is_member_of_author_guild(ctx: Context, userid: int) -> bool:
    async with ctx.bot.pool.acquire() as conn:
        user_1 = await conn.fetchval(
            'SELECT guild FROM profile WHERE "user"=$1;', ctx.author.id
        )
        user_2 = await conn.fetchval(
            'SELECT guild FROM profile WHERE "user"=$1;', userid
        )
    return user_1 == user_2


async def user_has_char(bot: "Bot", userid: int) -> bool:
    return bool(
        await bot.pool.fetchrow('SELECT guild FROM profile WHERE "user"=$1;', userid)
    )


async def has_money(bot: "Bot", userid: int, money: int, conn=None) -> bool:
    if conn is None:
        conn = await bot.pool.acquire()
        local = True
    else:
        local = False
    res = (
              bal := await conn.fetchval('SELECT money FROM profile WHERE "user"=$1;', userid)
          ) is not None and bal >= money
    if local:
        await bot.pool.release(conn)
    return res


async def guild_has_money(bot: "Bot", guildid: int, money: int) -> bool:
    res = await bot.pool.fetchval('SELECT money FROM guild WHERE "id"=$1;', guildid)
    return res >= money


def is_gm() -> "_CheckDecorator":
    async def predicate(ctx: Context) -> bool:
        user_id = int(ctx.author.id)

        owner_ids = getattr(ctx.bot, "owner_ids", None) or set()
        try:
            if user_id in {int(x) for x in owner_ids}:
                return True
        except (TypeError, ValueError):
            pass

        try:
            if await ctx.bot.is_owner(ctx.author):
                return True
        except Exception:
            pass

        gm_ids = getattr(ctx.bot.config.game, "game_masters", []) or []
        try:
            return user_id in {int(x) for x in gm_ids}
        except (TypeError, ValueError):
            return False

    return commands.check(predicate)


def is_patron(role: str = "basic") -> "_CheckDecorator":
    async def predicate(ctx: Context) -> bool:
        required_rank = _parse_donator_rank(role)
        if await user_is_patron(ctx.bot, ctx.author, role):
            return True
        raise NoPatron(required_rank)

    return commands.check(predicate)


_PROFILE_TIER_TO_DONATOR_RANK: dict[int, DonatorRank] = {
    1: DonatorRank.basic,
    2: DonatorRank.bronze,
    3: DonatorRank.silver,
    4: DonatorRank.gold,
    5: DonatorRank.emerald,
    6: DonatorRank.ruby,
    7: DonatorRank.diamond,
}

_DEFAULT_PATREON_GUILD_ID = 1323388333589528638
_DEFAULT_ROLE_TO_RANK: dict[int, DonatorRank] = {
    1411756981274017912: DonatorRank.basic,   # Mortal (tier 1)
    1411757068364546139: DonatorRank.bronze,  # Demi-God (tier 2)
    1411757100136140913: DonatorRank.gold,    # Olympian (tier 4)
    1411757151306645706: DonatorRank.gold,    # Titan (tier 4 + gift tier 1)
    1411757168356491508: DonatorRank.gold,    # Primordial Fate (tier 4 + gift tier 2)
}


def _parse_donator_rank(role: str) -> DonatorRank:
    tier_name = str(role).strip().lower()
    try:
        return getattr(DonatorRank, tier_name)
    except AttributeError as exc:
        raise ValueError(f"Unknown donator tier: {role!r}") from exc


def _rank_from_profile_tier(tier: int | None) -> DonatorRank | None:
    if tier is None:
        return None
    return _PROFILE_TIER_TO_DONATOR_RANK.get(int(tier))


def _normalize_rank(value: object | None) -> DonatorRank | None:
    if isinstance(value, DonatorRank):
        return value
    if isinstance(value, bool):
        # bool is an int subtype; do not interpret it as a tier.
        return None
    if isinstance(value, int):
        return _rank_from_profile_tier(value)
    if isinstance(value, str):
        try:
            return _parse_donator_rank(value)
        except ValueError:
            return None
    return None


def _max_rank(current: object | None, candidate: object | None) -> DonatorRank | None:
    current_rank = _normalize_rank(current)
    candidate_rank = _normalize_rank(candidate)
    if candidate_rank is None:
        return current_rank
    if current_rank is None or candidate_rank > current_rank:
        return candidate_rank
    return current_rank


def _set_rank_if_higher(
    role_rank_map: dict[int, DonatorRank], role_id: int, rank: DonatorRank
) -> None:
    existing = role_rank_map.get(role_id)
    if existing is None or rank > existing:
        role_rank_map[role_id] = rank


def _add_default_role_rank_mapping(role_rank_map: dict[int, DonatorRank]) -> None:
    for role_id, rank in _DEFAULT_ROLE_TO_RANK.items():
        _set_rank_if_higher(role_rank_map, int(role_id), rank)


def _build_role_rank_mapping(bot: "Bot") -> dict[int, DonatorRank]:
    role_rank_map: dict[int, DonatorRank] = {}
    _add_default_role_rank_mapping(role_rank_map)

    # Primary source: configured donator role mappings.
    for configured_role in getattr(bot.config.external, "donator_roles", []) or []:
        try:
            role_id = int(configured_role.id)
            rank = _parse_donator_rank(configured_role.tier)
        except (TypeError, ValueError):
            continue
        _set_rank_if_higher(role_rank_map, role_id, rank)

    # Fallback source: runtime mappings from PatreonStuff (role_id -> numeric tier).
    patreon_stuff = bot.get_cog("PatreonStuff")
    if patreon_stuff is not None:
        for role_id, tier in getattr(patreon_stuff, "ROLE_TIER_MAPPING", {}).items():
            try:
                rank = _rank_from_profile_tier(int(tier))
                if rank is None:
                    continue
                role_id_int = int(role_id)
            except (TypeError, ValueError):
                continue
            _set_rank_if_higher(role_rank_map, role_id_int, rank)

    return role_rank_map


def _support_guild_candidates(bot: "Bot") -> set[int]:
    guild_ids: set[int] = {_DEFAULT_PATREON_GUILD_ID}

    support_server_id = getattr(bot.config.game, "support_server_id", None)
    if support_server_id:
        try:
            guild_ids.add(int(support_server_id))
        except (TypeError, ValueError):
            pass

    patreon_core = bot.get_cog("PatreonCore")
    if patreon_core is not None:
        core_guild_id = getattr(patreon_core, "guild_id", None)
        if core_guild_id:
            try:
                guild_ids.add(int(core_guild_id))
            except (TypeError, ValueError):
                pass

    return guild_ids


def _rank_from_role_ids(role_ids: list[int], role_rank_map: dict[int, DonatorRank]) -> DonatorRank | None:
    rank: DonatorRank | None = None
    for role_id in role_ids:
        rank = _max_rank(rank, role_rank_map.get(role_id))
    return rank


def _rank_from_member_object(member: discord.Member, role_rank_map: dict[int, DonatorRank]) -> DonatorRank | None:
    member_role_ids = [int(member_role.id) for member_role in member.roles]
    rank = _rank_from_role_ids(member_role_ids, role_rank_map)
    return rank


async def _rank_from_member_api(bot: "Bot", guild_id: int, user_id: int, role_rank_map: dict[int, DonatorRank]) -> DonatorRank | None:
    try:
        member = await bot.http.get_member(int(guild_id), int(user_id))
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return None

    parsed_role_ids: list[int] = []
    for role_id in member.get("roles", []):
        try:
            parsed_role_ids.append(int(role_id))
        except (TypeError, ValueError):
            continue
    return _rank_from_role_ids(parsed_role_ids, role_rank_map)


async def user_is_patron(bot: "Bot", user: discord.User, role: str = "basic") -> bool:
    required_rank = _parse_donator_rank(role)
    best_rank: DonatorRank | None = None
    api_mode_active = False

    # Source 1: live PatreonCore cache (API-driven when enabled).
    patreon_core = bot.get_cog("PatreonCore")
    if patreon_core is not None:
        try:
            cached_tier = int(patreon_core.get_cached_tier_for_user(user.id))
            best_rank = _max_rank(best_rank, _rank_from_profile_tier(cached_tier))
            api_mode_active = (
                not getattr(patreon_core, "role_driven_sync", True)
                and bool(getattr(patreon_core, "patrons_data", {}))
            )
        except Exception:
            pass

    # Source 2: existing configured donator logic.
    if not api_mode_active:
        try:
            best_rank = _max_rank(best_rank, await bot.get_donator_rank(user.id))
        except Exception:
            pass

    # Source 3: Discord role fallback (supports Patreon-bot-managed roles).
    if not api_mode_active:
        role_rank_map = _build_role_rank_mapping(bot)
        if role_rank_map:
            if isinstance(user, discord.Member):
                best_rank = _max_rank(best_rank, _rank_from_member_object(user, role_rank_map))

            guild_ids = _support_guild_candidates(bot)

            if isinstance(user, discord.Member):
                guild_ids.add(int(user.guild.id))

            for guild_id in guild_ids:
                best_rank = _max_rank(
                    best_rank,
                    await _rank_from_member_api(bot, guild_id, user.id, role_rank_map),
                )

    # Source 4: profile tier written by Patreon sync jobs.
    try:
        tier = await bot.pool.fetchval('SELECT "tier" FROM profile WHERE "user"=$1;', user.id)
    except Exception:
        tier = None
    best_rank = _max_rank(best_rank, _rank_from_profile_tier(tier))

    return best_rank is not None and best_rank >= required_rank


def is_patreon(min_tier: int = 1) -> "_CheckDecorator":
    """Compatibility alias for cogs using the old name."""
    try:
        tier = int(min_tier)
    except (TypeError, ValueError):
        tier = 1

    if tier < 1:
        tier = 1

    required_rank = _PROFILE_TIER_TO_DONATOR_RANK.get(
        tier, _PROFILE_TIER_TO_DONATOR_RANK[max(_PROFILE_TIER_TO_DONATOR_RANK)]
    )
    return is_patron(required_rank.name)


def is_supporter() -> "_CheckDecorator":
    async def predicate(ctx: Context) -> bool:
        try:
            member = await ctx.bot.http.get_member(
                ctx.bot.config.game.support_server_id, ctx.author.id
            )
        except discord.NotFound:
            return False
        member_roles = [int(i) for i in member.get("roles", [])]
        return ctx.bot.config.game.support_team_role in member_roles

    return commands.check(predicate)


def has_open_help_request() -> "_CheckDecorator":
    async def predicate(ctx: Context) -> bool:
        response = await ctx.bot.redis.execute_command("GET", f"helpme:{ctx.guild.id}")
        if not response:
            raise NoOpenHelpRequest()
        ctx.helpme = response.decode()
        return True

    return commands.check(predicate)
