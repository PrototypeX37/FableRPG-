"""
The IdleRPG Discord Bot
Copyright (C) 2018-2021 Diniboy and Gelbpunkt
Copyright (C) 2023-2024 Lunar (PrototypeX37)

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
import asyncio
import secrets
from asyncio import subprocess
from collections import defaultdict
import csv

import aiohttp
import discord
import discord
from discord.ext import commands, menus
from utils import misc as rpgtools

from discord import Object, HTTPException
from PIL import Image
import io
import aiohttp
from asyncpg.exceptions import UniqueViolationError
from discord.ext import commands
from discord.http import handle_message_parameters
import json

from classes.converters import CrateRarity, IntFromTo, IntGreaterThan, UserWithCharacter
from classes.items import ItemType
from cogs.shard_communication import user_on_cooldown as user_cooldown
from utils import random
from utils.checks import has_char, is_gm
from utils.i18n import _, locale_doc

import copy
import io
import re
import textwrap
import traceback

from contextlib import redirect_stdout

import discord

from discord.ext import commands

from utils.checks import has_char, is_gm, is_god
from classes.badges import Badge, BadgeConverter
from classes.bot import Bot
from classes.context import Context
from classes.converters import UserWithCharacter
from utils import shell
from utils.misc import random_token

CHANNEL_BLACKLIST = ['⟢super-secrets〡🤫', '⟢god-spammit〡💫', '⟢gm-logs〡📝', 'Accepted Suggestions']
CATEGORY_NAME = '╰• ☣ | ☣ FABLE RPG ☣ | ☣ •╯'


class GameMaster(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.top_auction = None
        self._last_result = None
        self.auction_entry = None
        self.patron_ids = self.load_patron_ids()

    @is_gm()
    @commands.command(brief=_("Publish an announcement"))
    @locale_doc
    async def publish(self, ctx, message: discord.Message):
        _("Publish a message from an announement channel")
        try:
            await message.publish()
            await ctx.send(_("Message has been published!"))
        except discord.Forbidden:
            await ctx.send(_("This message is not from an announcement channel!"))

    @is_gm()
    @commands.command(
        aliases=["cleanshop", "cshop"], hidden=True, brief=_("Clean up the shop")
    )
    @locale_doc
    async def clearshop(self, ctx):
        _(
            """Remove items from the shop that have been there for more than 14 days, returning them to the owners' inventories.

            Only Game Masters can use this command."""
        )
        async with self.bot.pool.acquire() as conn:
            timed_out = await conn.fetch(
                """DELETE FROM market WHERE "published" + '14 days'::interval < NOW() RETURNING *;""",
                timeout=600,
            )
            await conn.executemany(
                'INSERT INTO inventory ("item", "equipped") VALUES ($1, $2);',
                [(i["item"], False) for i in timed_out],
                timeout=600,
            )
        await ctx.send(
            _("Cleared {num} shop items which timed out.").format(num=len(timed_out))
        )

    @is_gm()
    @commands.command(
        hidden=True, brief=_("Clear donator cache for a user")
    )
    @locale_doc
    async def code(self, ctx, tier: int, userid):

        try:
            try:
                user = await self.bot.fetch_user(int(userid))
            except discord.errors.NotFound:
                await ctx.send("Invalid user ID. Please provide a valid Discord user ID.")
                return

            if tier < 1 or tier > 4:
                await ctx.send("Invalid tier. Please provide a validtier level.")
                return

            generated_code = '-'.join(
                ''.join(secrets.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789') for _ in range(5)) for _ in range(5))

            await self.bot.pool.execute(
                'INSERT INTO patreon_keys ("key", "tier", "discordid") VALUES ($1, $2, $3);', generated_code, str(tier),
                int(userid)
            )

            user_id = userid  # Replace with the specific user ID

            try:
                # Fetch the user from Discord's servers
                user = await self.bot.fetch_user(user_id)

                # Send a direct message to the user
                await user.send(
                    f'Thank you so much for your support! You can redeem your perks using $patreonredeem and the following code: {generated_code}')
                await ctx.send('Message sent.')
            except discord.NotFound:
                await ctx.send('User not found.')

            await ctx.send(f"Generated code: {generated_code}")
        except Exception as e:
            await ctx.send(e)

    @is_gm()
    @commands.command(
        hidden=True, aliases=["gmcdc"], brief=_("Clear donator cache for a user")
    )
    @locale_doc
    async def gmcleardonatorcache(self, ctx, *, other: discord.Member):
        _(
            """`<other>` - A server member

            Clears the cached donator rank for a user globally, allowing them to use the new commands after donating.

            Only Game Masters can use this command."""
        )
        await self.bot.clear_donator_cache(other)
        await ctx.send(_("Done"))

    @is_gm()
    @commands.command(hidden=True, brief=_("Bot-ban a user"))
    @locale_doc
    async def gmban(self, ctx, other: int | discord.User, *, reason: str = ""):
        _(
            """`<other>` - A discord User

            Bans a user from the bot, prohibiting them from using commands and reactions.

            Only Game Masters can use this command."""
        )
        id_ = other if isinstance(other, int) else other.id

        if id_ == 295173706496475136:
            await ctx.send("You're funny..")

        try:
            await self.bot.pool.execute(
                'INSERT INTO bans ("user_id", "reason") VALUES ($1, $2);', id_, reason
            )
            self.bot.bans.add(id_)
            await self.bot.reload_bans()

            await ctx.send(_("Banned: {other}").format(other=other))

            with handle_message_parameters(
                    content="**{gm}** banned **{other}**.\n\nReason: *{reason}*".format(
                        gm=ctx.author,
                        other=other,
                        reason=reason or f"<{ctx.message.jump_url}>",
                    )
            ) as params:
                await self.bot.http.send_message(
                    self.bot.config.game.gm_log_channel,
                    params=params,
                )
        except UniqueViolationError:
            await ctx.send(_("{other} is already banned.").format(other=other))

    @is_gm()
    @commands.command(hidden=True, brief=_("Bot-unban a user"))
    async def reloadbans(self, ctx):
        await self.bot.reload_bans()
        await ctx.send("Bans Reloaded")

    @is_gm()
    @commands.command(hidden=True)
    async def changetier(self, ctx, userID: int, tier: int):
        # Validate the tier input
        if tier not in [0, 1, 2, 3, 4]:
            await ctx.send("Invalid tier value. Please choose a tier between 0 and 4.")
            return

        # Update the tier using the PostgreSQL connection with placeholders
        async with self.bot.pool.acquire() as connection:
            await connection.execute(
                'UPDATE profile SET "tier" = $1 WHERE "user" = $2',
                tier, userID
            )

        await ctx.send(f"Tier for user ID {userID} has been updated to {tier}.")

    @is_gm()
    @commands.command(hidden=True, brief=_("Bot-unban a user"))
    @locale_doc
    async def gmunban(self, ctx, other: int | discord.User, *, reason: str = ""):
        _(
            """`<other>` - A discord User

            Unbans a user from the bot, allowing them to use commands and reactions again.

            Only Game Masters can use this command."""
        )
        id_ = other if isinstance(other, int) else other.id
        await self.bot.pool.execute('DELETE FROM bans WHERE "user_id"=$1;', id_)

        try:
            self.bot.bans.remove(id_)
            await self.bot.reload_bans()

            await ctx.send(_("Unbanned: {other}").format(other=other))

            with handle_message_parameters(
                    content="**{gm}** unbanned **{other}**.\n\nReason: *{reason}*".format(
                        gm=ctx.author,
                        other=other,
                        reason=reason or f"<{ctx.message.jump_url}>",
                    )
            ) as params:
                await self.bot.http.send_message(
                    self.bot.config.game.gm_log_channel,
                    params=params,
                )
        except KeyError:
            await ctx.send(_("{other} is not banned.").format(other=other))





    @is_gm()
    @has_char()
    @commands.command(hidden=True)
    async def gmtokens(self, ctx, member: UserWithCharacter, tokens: int, reason: str = None):
        # Fetch the current token value of the specified user from the database
        weapontoken_value = await self.bot.pool.fetchval(
            'SELECT weapontoken FROM profile WHERE "user"=$1;',
            member.id  # Use the specified member's Discord ID
        )

        # If the user doesn't have a token value yet, set it to 0
        if weapontoken_value is None:
            weapontoken_value = 0

        # Add the new tokens to the current value
        new_value = weapontoken_value + tokens

        # Update the database with the new token value
        await self.bot.pool.execute(
            'UPDATE profile SET weapontoken=$1 WHERE "user"=$2;',
            new_value, member.id
        )

        # Send a confirmation message to the context
        await ctx.send(f"{member.display_name} now has {new_value} weapon tokens!")

        with handle_message_parameters(
                content="**{gm}** gave **{tokens}** to **{member}**.\n\nReason: *{reason}*".format(
                    gm=ctx.author,
                    tokens=tokens,
                    member=member,
                    reason=reason or f"<{ctx.message.jump_url}>",
                )
        ) as params:
            await self.bot.http.send_message(
                self.bot.config.game.gm_log_channel,
                params=params,
            )

    @is_gm()
    @commands.command(hidden=True, brief=_("Create money"))
    @locale_doc
    async def gmgive(
            self,
            ctx,
            money: int,
            other: UserWithCharacter,
            *,
            reason: str = None,
    ):
        _(
            """`<money>` - the amount of money to generate for the user
            `<other>` - A discord User with a character
            `[reason]` - The reason this action was done, defaults to the command message link

            Gives a user money without subtracting it from the command author's balance.

            Only Game Masters can use this command."""
        )

        try:

            permissions = ctx.channel.permissions_for(ctx.guild.me)

            if permissions.read_messages and permissions.send_messages:
                await self.bot.pool.execute(
                    'UPDATE profile SET "money"="money"+$1 WHERE "user"=$2;', money, other.id
                )
                await ctx.send(
                    _(
                        "Successfully gave **${money}** without a loss for you to **{other}**."
                    ).format(money=money, other=other)
                )

                with handle_message_parameters(
                        content="**{gm}** gave **${money}** to **{other}**.\n\nReason: *{reason}*".format(
                            gm=ctx.author,
                            money=money,
                            other=other,
                            reason=reason or f"<{ctx.message.jump_url}>",
                        )
                ) as params:
                    await self.bot.http.send_message(
                        self.bot.config.game.gm_log_channel,
                        params=params,
                    )

        except Exception as e:
            await ctx.send(e)

    @commands.command(hidden=True, brief=_("Emergancy Shutdown"))
    async def shutdown(self, ctx):
        """Shuts down the bot"""
        # Check if the user invoking the command is the bot owner
        if ctx.author.id == 118234287425191938:
            await ctx.send("Shutting down... Bye!")
            await self.bot.close()  # Gracefully close the bot
        else:
            await ctx.send("You don't have permission to use this command.")

    @is_gm()
    @commands.command(hidden=True, brief=_("Create money"))
    @locale_doc
    async def gmgiveeggs(
            self,
            ctx,
            eggs: int,
            other: UserWithCharacter,
            *,
            reason: str = None,
    ):
        _(
            """`<money>` - the amount of money to generate for the user
            `<other>` - A discord User with a character
            `[reason]` - The reason this action was done, defaults to the command message link

            Gives a user money without subtracting it from the command author's balance.

            Only Game Masters can use this command."""
        )

        permissions = ctx.channel.permissions_for(ctx.guild.me)

        if permissions.read_messages and permissions.send_messages:
            await self.bot.pool.execute(
                'UPDATE profile SET "eastereggs"="eastereggs"+$1 WHERE "user"=$2;', eggs, other.id
            )
            await ctx.send(
                _(
                    "Successfully gave **{money} eggs** without a loss for you to **{other}**."
                ).format(money=eggs, other=other)
            )

            with handle_message_parameters(
                    content="**{gm}** gave **{money}** to **{other}**.\n\nReason: *{reason}*".format(
                        gm=ctx.author,
                        money=eggs,
                        other=other,
                        reason=reason or f"<{ctx.message.jump_url}>",
                    )
            ) as params:
                await self.bot.http.send_message(
                    self.bot.config.game.gm_log_channel,
                    params=params,
                )

    @is_gm()
    @commands.command(hidden=True, brief=_("Remove money"))
    @locale_doc
    async def gmremove(
            self,
            ctx,
            money: int,
            other: UserWithCharacter,
            *,
            reason: str = None,
    ):
        _(
            """`<money>` - the amount of money to remove from the user
            `<other>` - a discord User with character
            `[reason]` - The reason this action was done, defaults to the command message link

            Removes money from a user without adding it to the command author's balance.

            Only Game Masters can use this command."""
        )
        await self.bot.pool.execute(
            'UPDATE profile SET "money"="money"-$1 WHERE "user"=$2;', money, other.id
        )
        await ctx.send(
            _("Successfully removed **${money}** from **{other}**.").format(
                money=money, other=other
            )
        )

        with handle_message_parameters(
                content="**{gm}** removed **${money}** from **{other}**.\n\nReason: *{reason}*".format(
                    gm=ctx.author,
                    money=money,
                    other=other,
                    reason=reason or f"<{ctx.message.jump_url}>",
                )
        ) as params:
            await self.bot.http.send_message(
                self.bot.config.game.gm_log_channel,
                params=params,
            )

    @is_gm()
    @commands.command(hidden=True, brief=_("Delete a character"))
    @locale_doc
    async def gmdelete(self, ctx, other: UserWithCharacter, *, reason: str = None):
        _(
            """`<other>` - a discord User with character
            `[reason]` - The reason this action was done, defaults to the command message link

            Delete a user's profile. The user cannot be a Game Master.

            Only Game Masters can use this command."""
        )
        if other.id in ctx.bot.config.game.game_masters:  # preserve deletion of admins
            return await ctx.send(_("Very funny..."))
        async with self.bot.pool.acquire() as conn:
            g = await conn.fetchval(
                'DELETE FROM guild WHERE "leader"=$1 RETURNING id;', other.id
            )
            if g:
                await conn.execute(
                    'UPDATE profile SET "guildrank"=$1, "guild"=$2 WHERE "guild"=$3;',
                    "Member",
                    0,
                    g,
                )
                await conn.execute('UPDATE city SET "owner"=1 WHERE "owner"=$1;', g)
            partner = await conn.fetchval(
                'UPDATE profile SET "marriage"=$1 WHERE "marriage"=$2 RETURNING'
                ' "user";',
                0,
                other.id,
            )
            await conn.execute(
                'UPDATE children SET "mother"=$1, "father"=0 WHERE ("father"=$1 AND'
                ' "mother"=$2) OR ("father"=$2 AND "mother"=$1);',
                partner,
                other.id,
            )
            await self.bot.delete_profile(other.id, conn=conn)
        await ctx.send(_("Successfully deleted the character."))

        with handle_message_parameters(
                content="**{gm}** deleted **{other}**.\n\nReason: *{reason}*".format(
                    gm=ctx.author, other=other, reason=reason or f"<{ctx.message.jump_url}>"
                )
        ) as params:
            await self.bot.http.send_message(
                self.bot.config.game.gm_log_channel,
                params=params,
            )

    @is_gm()
    @commands.command(hidden=True, brief=_("Rename a character"))
    @locale_doc
    async def gmrename(self, ctx, target: UserWithCharacter, *, reason: str = None):
        _(
            """`<target>` - a discord User with character
            `[reason]` - The reason this action was done, defaults to the command message link

            Rename a user's profile. The user cannot be a Game Master.

            Only Game Masters can use this command."""
        )
        if target.id in ctx.bot.config.game.game_masters:  # preserve renaming of admins
            return await ctx.send(_("Very funny..."))

        await ctx.send(
            _("What shall the character's name be? (min. 3 letters, max. 20)")
        )

        def mycheck(amsg):
            return (
                    amsg.author == ctx.author
                    and amsg.channel == ctx.channel
                    and len(amsg.content) < 21
                    and len(amsg.content) > 2
            )

        try:
            name = await self.bot.wait_for("message", timeout=60, check=mycheck)
        except asyncio.TimeoutError:
            return await ctx.send(_("Timeout expired."))

        await self.bot.pool.execute(
            'UPDATE profile SET "name"=$1 WHERE "user"=$2;', name.content, target.id
        )
        await ctx.send(_("Renamed."))

        with handle_message_parameters(
                content="**{gm}** renamed **{target}** to **{name}**.\n\nReason: *{reason}*".format(
                    gm=ctx.author,
                    target=target,
                    name=name.content,
                    reason=reason or f"<{ctx.message.jump_url}>",
                )
        ) as params:
            await self.bot.http.send_message(
                self.bot.config.game.gm_log_channel,
                params=params,
            )

    @is_gm()
    @commands.command(hidden=True, brief=_("Create an item"))
    @locale_doc
    async def gmitem(
            self,
            ctx,
            stat: int,
            owner: UserWithCharacter,
            item_type: str.title,
            element: str,
            value: IntFromTo(0, 100000000),
            name: str,
            *,
            reason: str = None,
    ):
        _(
            """`<stat>` - the generated item's stat, must be between 0 and 100
            `<owner>` - a discord User with character
            `<item_type>` - the generated item's type, must be either Sword, Shield, Axe, Wand, Dagger, Knife, Spear, Bow, Hammer, Scythe or Mace
            `<element> - the element type
            `<value>` - the generated item's value, a whole number from 0 to 100,000,000
            `<name>` - the generated item's name, should be in double quotes if the name has multiple words
            `[reason]` - The reason this action was done, defaults to the command message link

            Generate a custom item for a user.

            Only Game Masters can use this command."""
        )
        item_type = ItemType.from_string(item_type)
        if item_type is None:
            return await ctx.send(_("Invalid item type."))
        if not -100 <= stat <= 201:
            return await ctx.send(_("Invalid stat."))
        try:
            hand = item_type.get_hand().value
            await self.bot.create_item(
                name=name,
                value=value,
                type_=item_type.value,
                damage=stat if item_type != ItemType.Shield else 0,
                armor=stat if item_type == ItemType.Shield else 0,
                hand=hand,
                owner=owner,
                element=element,
            )
        except Exception as e:
            await ctx.send(f"Error has occured {e}")

        message = "{gm} created a {item_type} with name {name} and stat {stat}.\n\nReason: *{reason}*".format(
            gm=ctx.author,
            item_type=item_type.value,
            name=name,
            stat=stat,
            reason=reason or f"<{ctx.message.jump_url}>",
        )

        await ctx.send(_("Done."))

        with handle_message_parameters(content=message) as params:
            await self.bot.http.send_message(
                self.bot.config.game.gm_log_channel, params=params
            )

        for user in self.bot.owner_ids:
            user = await self.bot.get_user_global(user)
            await user.send(message)

    @is_gm()
    @commands.command(hidden=True, brief=_("Create crates"))
    @locale_doc
    async def gmcrate(
            self,
            ctx,
            rarity: CrateRarity,
            amount: int,
            target: UserWithCharacter,
            *,
            reason: str = None,
    ):
        _(
            """`<rarity>` - the crates' rarity, can be common, uncommon, rare, magic or legendary
            `<amount>` - the amount of crates to generate for the given user, can be negative
            `<target>` - A discord User with character
            `[reason]` - The reason this action was done, defaults to the command message link

            Generate a set amount of crates of one rarity for a user.

            Only Game Masters can use this command."""
        )
        await self.bot.pool.execute(
            f'UPDATE profile SET "crates_{rarity}"="crates_{rarity}"+$1 WHERE'
            ' "user"=$2;',
            amount,
            target.id,
        )
        await ctx.send(
            _("Successfully gave **{amount}** {rarity} crates to **{target}**.").format(
                amount=amount, target=target, rarity=rarity
            )
        )

        with handle_message_parameters(
                content="**{gm}** gave **{amount}** {rarity} crates to **{target}**.\n\nReason: *{reason}*".format(
                    gm=ctx.author,
                    amount=amount,
                    rarity=rarity,
                    target=target,
                    reason=reason or f"<{ctx.message.jump_url}>",
                )
        ) as params:
            await self.bot.http.send_message(
                self.bot.config.game.gm_log_channel,
                params=params,
            )

    @is_gm()
    @commands.command(hidden=True, brief=_("Generate XP"))
    @locale_doc
    async def gmxp(
            self,
            ctx,
            target: UserWithCharacter,
            amount: int,
            *,
            reason: str = None,
    ):
        _(
            """`<target>` - A discord User with character
            `<amount>` - The amount of XP to generate, can be negative
            `[reason]` - The reason this action was done, defaults to the command message link

            Generates a set amount of XP for a user.

            Only Game Masters can use this command."""
        )
        await self.bot.pool.execute(
            'UPDATE profile SET "xp"="xp"+$1 WHERE "user"=$2;', amount, target.id
        )
        await ctx.send(
            _("Successfully gave **{amount}** XP to **{target}**.").format(
                amount=amount, target=target
            )
        )

        with handle_message_parameters(
                content="**{gm}** gave **{amount}** XP to **{target}**.\n\nReason: *{reason}*".format(
                    gm=ctx.author,
                    amount=amount,
                    target=target,
                    reason=reason or f"<{ctx.message.jump_url}>",
                )
        ) as params:
            await self.bot.http.send_message(
                self.bot.config.game.gm_log_channel,
                params=params,
            )

    import random
    @is_gm()
    @commands.command()
    async def gmiv(self, ctx, monster_id: int):
        """Generate IVs for an existing monster using its ID."""
        # Fetch the monster from the database
        try:
            await ctx.send("hi")
            async with self.bot.pool.acquire() as conn:
                monster = await conn.fetchrow(
                    """
                    SELECT * FROM monster_pets WHERE "id" = $1;
                    """,
                    monster_id
                )


            if not monster:
                await ctx.send(f"No monster found with ID {monster_id}.")
                return
            import random

            iv_percentage = random.uniform(10, 1000)

            if iv_percentage < 20:
                iv_percentage = random.uniform(90, 100)
            elif iv_percentage < 70:
                iv_percentage = random.uniform(80, 90)
            elif iv_percentage < 150:
                iv_percentage = random.uniform(70, 80)
            elif iv_percentage < 350:
                iv_percentage = random.uniform(60, 70)
            elif iv_percentage < 700:
                iv_percentage = random.uniform(50, 60)
            else:
                iv_percentage = random.uniform(30, 50)

            # Calculate total IV points (100% IV corresponds to 200 points)
            total_iv_points = (iv_percentage / 100) * 200

            def allocate_iv_points(total_points):
                # Generate three random numbers
                import random
                a = random.random()
                b = random.random()
                c = random.random()
                total = a + b + c
                # Normalize so that the sum is equal to total_points
                hp_iv = total_points * (a / total)
                attack_iv = total_points * (b / total)
                defense_iv = total_points * (c / total)
                # Round the IV points
                hp_iv = int(round(hp_iv))
                attack_iv = int(round(attack_iv))
                defense_iv = int(round(defense_iv))
                # Adjust if rounding errors cause total to deviate
                iv_sum = hp_iv + attack_iv + defense_iv
                if iv_sum != int(round(total_points)):
                    diff = int(round(total_points)) - iv_sum
                    # Adjust the largest IV by the difference
                    max_iv = max(hp_iv, attack_iv, defense_iv)
                    if hp_iv == max_iv:
                        hp_iv += diff
                    elif attack_iv == max_iv:
                        attack_iv += diff
                    else:
                        defense_iv += diff
                return hp_iv, attack_iv, defense_iv

            hp_iv, attack_iv, defense_iv = allocate_iv_points(total_iv_points)
        except Exception as e:
            await ctx.send(e)

        # Calculate the final stats
        base_hp = monster['hp']
        base_attack = monster['attack']
        base_defense = monster['defense']

        hp_total = base_hp + hp_iv
        attack_total = base_attack + attack_iv
        defense_total = base_defense + defense_iv



        # Update the monster's IVs and total stats in the database
        try:
            async with self.bot.pool.acquire() as conn:
                await conn.execute(
                    'UPDATE monster_pets SET "IV" = $1, hp = $2, attack = $3, defense = $4 WHERE "id" = $5;',
                    iv_percentage,
                    hp_total,
                    attack_total,
                    defense_total,
                    monster_id
                )


            await ctx.send(
                f"Monster with ID {monster_id} has been assigned an IV of {iv_percentage:.2f}% "
                f"(HP IV: {hp_iv}, Attack IV: {attack_iv}, Defense IV: {defense_iv}). "
                f"Total stats updated."
            )
        except Exception as e:
            await ctx.send(f"An error occurred: {e}")

    @is_gm()
    @commands.command()
    async def gmegg(self, ctx, member: discord.Member, *, monster_name: str):
        """Generate an egg for a user with a specified monster."""
        # Check if the monster exists
        monster = None

        monsters = {
            1: [
                {"name": "Sneevil", "hp": 100, "attack": 95, "defense": 100, "element": "Earth",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_Sneevil-removebg-preview.png"},
                {"name": "Slime", "hp": 120, "attack": 100, "defense": 105, "element": "Water",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_slime.png"},
                {"name": "Frogzard", "hp": 120, "attack": 90, "defense": 95, "element": "Nature",
                 "url": "https://static.wikia.nocookie.net/aqwikia/images/d/d6/Frogzard.png"},
                {"name": "Rat", "hp": 90, "attack": 100, "defense": 90, "element": "Dark",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_Rat-removebg-preview.png"},
                {"name": "Bat", "hp": 150, "attack": 95, "defense": 85, "element": "Wind",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_Bat-removebg-preview.png"},
                {"name": "Skeleton", "hp": 190, "attack": 105, "defense": 100, "element": "Dark",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_Skelly-removebg-preview.png"},
                {"name": "Imp", "hp": 180, "attack": 95, "defense": 85, "element": "Fire",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_zZquzlh-removebg-preview.png"},
                {"name": "Pixie", "hp": 100, "attack": 90, "defense": 80, "element": "Light",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_pixie-removebg-preview.png"},
                {"name": "Zombie", "hp": 170, "attack": 100, "defense": 95, "element": "Dark",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_zombie-removebg-preview.png"},
                {"name": "Spiderling", "hp": 220, "attack": 95, "defense": 90, "element": "Nature",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_spider-removebg-preview.png"},
                {"name": "Spiderling", "hp": 220, "attack": 95, "defense": 90, "element": "Nature",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_spider-removebg-preview.png"},
                {"name": "Moglin", "hp": 200, "attack": 90, "defense": 85, "element": "Light",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_Moglin.png"},
                {"name": "Red Ant", "hp": 140, "attack": 105, "defense": 100, "element": "Dark",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_redant-removebg-preview.png"},
                {"name": "Chickencow", "hp": 300, "attack": 150, "defense": 90, "element": "Nature",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_ChickenCow-removebg-preview.png"},
                {"name": "Tog", "hp": 380, "attack": 105, "defense": 95, "element": "Earth",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_Tog-removebg-preview.png"},
                {"name": "Lemurphant", "hp": 340, "attack": 95, "defense": 80, "element": "Nature",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_Lemurphant-removebg-preview.png"},
                {"name": "Fire Imp", "hp": 200, "attack": 100, "defense": 90, "element": "Fire",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_zZquzlh-removebg-preview.png"},
                {"name": "Zardman", "hp": 300, "attack": 95, "defense": 100, "element": "Earth",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_Zardman-removebg-preview.png"},
                {"name": "Wind Elemental", "hp": 165, "attack": 90, "defense": 85, "element": "Wind",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_WindElemental-removebg-preview.png"},
                {"name": "Dark Wolf", "hp": 200, "attack": 100, "defense": 90, "element": "Dark",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_DarkWolf-removebg-preview.png"},
                {"name": "Treeant", "hp": 205, "attack": 105, "defense": 95, "element": "Nature",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_Treeant-removebg-preview.png"},
            ],
            2: [
                {"name": "Cyclops Warlord", "hp": 230, "attack": 160, "defense": 155, "element": "Earth",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_CR-removebg-preview.png"},
                {"name": "Fishman Soldier", "hp": 200, "attack": 165, "defense": 160, "element": "Water",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_Fisherman-removebg-preview.png"},
                {"name": "Fire Elemental", "hp": 215, "attack": 150, "defense": 145, "element": "Fire",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_fire_elemental-removebg-preview.png"},
                {"name": "Vampire Bat", "hp": 200, "attack": 170, "defense": 160, "element": "Dark",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_viO2oSJ-removebg-preview.png"},
                {"name": "Blood Eagle", "hp": 195, "attack": 165, "defense": 150, "element": "Wind",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_BloodEagle-removebg-preview.png"},
                {"name": "Earth Elemental", "hp": 190, "attack": 175, "defense": 160, "element": "Earth",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_Earth_Elemental-removebg-preview.png"},
                {"name": "Fire Mage", "hp": 200, "attack": 160, "defense": 140, "element": "Fire",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_FireMage-removebg-preview.png"},
                {"name": "Dready Bear", "hp": 230, "attack": 155, "defense": 150, "element": "Dark",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_dreddy-removebg-preview.png"},
                {"name": "Undead Soldier", "hp": 280, "attack": 160, "defense": 155, "element": "Dark",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_UndeadSoldier-removebg-preview.png"},
                {"name": "Skeleton Warrior", "hp": 330, "attack": 155, "defense": 150, "element": "Dark",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_SkeelyWarrior-removebg-preview.png"},
                {"name": "Giant Spider", "hp": 350, "attack": 160, "defense": 145, "element": "Nature",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_DreadSpider-removebg-preview.png"},
                {"name": "Castle spider", "hp": 310, "attack": 170, "defense": 160, "element": "Nature",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_Castle-removebg-preview.png"},
                {"name": "ConRot", "hp": 210, "attack": 165, "defense": 155, "element": "Water",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_ConRot-removebg-preview.png"},
                {"name": "Horc Warrior", "hp": 270, "attack": 175, "defense": 170, "element": "Earth",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_HorcWarrior-removebg-preview.png"},
                {"name": "Shadow Hound", "hp": 300, "attack": 160, "defense": 150, "element": "Dark",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_Hound-removebg-preview.png"},
                {"name": "Fire Sprite", "hp": 290, "attack": 165, "defense": 155, "element": "Fire",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_FireSprite-removebg-preview.png"},
                {"name": "Rock Elemental", "hp": 300, "attack": 160, "defense": 165, "element": "Earth",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_Earth_Elemental-removebg-preview.png"},
                {"name": "Shadow Serpent", "hp": 335, "attack": 155, "defense": 150, "element": "Dark",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_ShadowSerpant-removebg-preview.png"},
                {"name": "Dark Elemental", "hp": 340, "attack": 165, "defense": 155, "element": "Dark",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_DarkEle-Photoroom.png"},
                {"name": "Forest Guardian", "hp": 500, "attack": 250, "defense": 250, "element": "Nature",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_ForestGuardian-removebg-preview.png"},
            ],
            3: [
                {"name": "Mana Golem", "hp": 200, "attack": 220, "defense": 210, "element": "Corrupted",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_managolum-removebg-preview.png"},
                {"name": "Karok the Fallen", "hp": 180, "attack": 215, "defense": 205, "element": "Ice",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_VIMs8un-removebg-preview.png"},
                {"name": "Water Draconian", "hp": 220, "attack": 225, "defense": 200, "element": "Water",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_waterdrag-removebg-preview.png"},
                {"name": "Shadow Creeper", "hp": 190, "attack": 220, "defense": 205, "element": "Dark",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_shadowcreep-removebg-preview.png"},
                {"name": "Wind Djinn", "hp": 210, "attack": 225, "defense": 215, "element": "Wind",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_djinn-removebg-preview.png"},
                {"name": "Autunm Fox", "hp": 205, "attack": 230, "defense": 220, "element": "Earth",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_Autumn_Fox-removebg-preview.png"},
                {"name": "Dark Draconian", "hp": 195, "attack": 220, "defense": 200, "element": "Dark",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_darkdom-removebg-preview.png"},
                {"name": "Light Elemental", "hp": 185, "attack": 215, "defense": 210, "element": "Light",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_LightELemental-removebg-preview.png"},
                {"name": "Undead Giant", "hp": 230, "attack": 220, "defense": 210, "element": "Dark",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_UndGiant-removebg-preview.png"},
                {"name": "Chaos Spider", "hp": 215, "attack": 215, "defense": 205, "element": "Corrupted",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_ChaosSpider-removebg-preview.png"},
                {"name": "Seed Spitter", "hp": 225, "attack": 220, "defense": 200, "element": "Nature",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_SeedSpitter-removebg-preview.png"},
                {"name": "Beach Werewolf", "hp": 240, "attack": 230, "defense": 220, "element": "Water",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_BeachWerewold-removebg-preview.png"},
                {"name": "Boss Dummy", "hp": 220, "attack": 225, "defense": 210, "element": "Nature",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_BossDummy-removebg-preview.png"},
                {"name": "Rock", "hp": 235, "attack": 225, "defense": 215, "element": "Earth",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_Rock-removebg-preview.png"},
                {"name": "Shadow Serpent", "hp": 200, "attack": 220, "defense": 205, "element": "Dark",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_ShadoeSerpant-removebg-preview.png"},
                {"name": "Flame Elemental", "hp": 210, "attack": 225, "defense": 210, "element": "Fire",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_FireElemental-removebg-preview.png"},
                {"name": "Bear", "hp": 225, "attack": 215, "defense": 220, "element": "Nature",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_Remove-bg.ai_1732611726453.png"},
                {"name": "Chair", "hp": 215, "attack": 210, "defense": 215, "element": "Nature",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_chair-removebg-preview.png"},
                {"name": "Chaos Serpant", "hp": 230, "attack": 220, "defense": 205, "element": "Corrupted",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_ChaosSerp-removebg-preview.png"},
                {"name": "Gorillaphant", "hp": 240, "attack": 225, "defense": 210, "element": "Nature",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_gorillaserpant-removebg-preview.png"},
            ],
            4: [
                {"name": "Hydra Head", "hp": 300, "attack": 280, "defense": 270, "element": "Water",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_hydra.png"},
                {"name": "Blessed Deer", "hp": 280, "attack": 275, "defense": 265, "element": "Light",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_BlessedDeer-removebg-preview.png"},
                {"name": "Chaos Sphinx", "hp": 320, "attack": 290, "defense": 275, "element": "Corrupted",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_ChaopsSpinx.png"},
                {"name": "Inferno Dracolion", "hp": 290, "attack": 285, "defense": 270, "element": "Dark",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_Remove-bg.ai_1732614284328.png"},
                {"name": "Wind Cyclone", "hp": 310, "attack": 290, "defense": 280, "element": "Wind",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_WindElemental-removebg-preview.png"},
                {"name": "Dwakel Blaster", "hp": 305, "attack": 295, "defense": 285, "element": "Electric",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_Bubble.png"},
                {"name": "Infernal Fiend", "hp": 295, "attack": 285, "defense": 270, "element": "Fire",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_Remove-bg.ai_1732614284328.png"},
                {"name": "Dark Mukai", "hp": 285, "attack": 275, "defense": 265, "element": "Dark",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_Remove-bg.ai_1732614826889.png"},
                {"name": "Undead Berserker", "hp": 330, "attack": 285, "defense": 275, "element": "Dark",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_Remove-bg.ai_1732614863579.png"},
                {"name": "Chaos Warrior", "hp": 315, "attack": 280, "defense": 270, "element": "Corrupted",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_ChaosWarrior-removebg-preview.png"},
                {"name": "Dire Wolf", "hp": 325, "attack": 285, "defense": 275, "element": "Nature",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_DireWolf-removebg-preview.png"},
                {"name": "Skye Warrior", "hp": 340, "attack": 295, "defense": 285, "element": "Corrupted",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_SkyeWarrior-removebg-preview.png"},
                {"name": "Death On Wings", "hp": 320, "attack": 290, "defense": 275, "element": "Dark",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_DeathonWings-removebg-preview.png"},
                {"name": "Chaorruption", "hp": 335, "attack": 295, "defense": 285, "element": "Corrupted",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_Chaorruption-removebg-preview.png"},
                {"name": "Shadow Beast", "hp": 300, "attack": 285, "defense": 270, "element": "Dark",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_ShadowBeast-removebg-preview.png"},
                {"name": "Hootbear", "hp": 310, "attack": 290, "defense": 275, "element": "Nature",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_HootBear-removebg-preview.png"},
                {"name": "Anxiety", "hp": 325, "attack": 280, "defense": 290, "element": "Dark",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_anxiety-removebg-preview.png"},
                {"name": "Twilly", "hp": 315, "attack": 275, "defense": 285, "element": "Nature",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_Twilly-removebg-preview.png"},
                {"name": "Black Cat", "hp": 330, "attack": 285, "defense": 270, "element": "Corrupted",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_QJsLMnk-removebg-preview.png"},
                {"name": "Forest Guardian", "hp": 340, "attack": 290, "defense": 275, "element": "Nature",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_ForestGuardian-removebg-preview.png"},
            ],
            5: [
                {"name": "Chaos Dragon", "hp": 400, "attack": 380, "defense": 370, "element": "Corrupted",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_ChaosDragon-removebg-preview.png"},
                {"name": "Wooden Door", "hp": 380, "attack": 375, "defense": 365, "element": "Earth",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_WoodenDoor-removebg-preview.png"},
                {"name": "Garvodeus", "hp": 420, "attack": 390, "defense": 375, "element": "Water",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_Garvodeus-removebg-preview.png"},
                {"name": "Shadow Lich", "hp": 390, "attack": 385, "defense": 370, "element": "Dark",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_ShadowLich-removebg-preview.png"},
                {"name": "Zorbak", "hp": 410, "attack": 390, "defense": 380, "element": "Dark",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_Zorbak-removebg-preview.png"},
                {"name": "Dwakel Rocketman", "hp": 405, "attack": 395, "defense": 385, "element": "Electric",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_DwarkalRock-removebg-preview.png"},
                {"name": "Kathool", "hp": 395, "attack": 385, "defense": 370, "element": "Water",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_Kathool-removebg-preview.png"},
                {"name": "Celestial Hound", "hp": 385, "attack": 375, "defense": 365, "element": "Light",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_CelestialHound-removebg-preview.png"},
                {"name": "Undead Raxgore", "hp": 430, "attack": 385, "defense": 375, "element": "Dark",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_Raxfore-removebg-preview_1.png"},
                {"name": "Droognax", "hp": 415, "attack": 380, "defense": 370, "element": "Dark",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_Droognax-removebg-preview.png"},
                {"name": "Corrupted Boar", "hp": 425, "attack": 385, "defense": 375, "element": "Corrupted",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_Corrupted_Bear-removebg-preview.png"},
                {"name": "Fressa", "hp": 440, "attack": 395, "defense": 385, "element": "Water",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_Fressa-removebg-preview.png"},
                {"name": "Grimskull", "hp": 420, "attack": 390, "defense": 375, "element": "Dark",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_Grimskull-removebg-preview.png"},
                {"name": "Chaotic Chicken", "hp": 435, "attack": 385, "defense": 380, "element": "Corrupted",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_ChaoticChicken-removebg-preview.png"},
                {"name": "Baelgar", "hp": 400, "attack": 385, "defense": 370, "element": "Dark",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_Baelgar-removebg-preview.png"},
                {"name": "Blood Dragon", "hp": 410, "attack": 390, "defense": 375, "element": "Fire",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_BloodDragon-removebg-preview.png"},
                {"name": "Avatar of Desolich", "hp": 425, "attack": 380, "defense": 390, "element": "Fire",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_Remove-bg.ai_1732696555786.png"},
                {"name": "Piggy Drake", "hp": 415, "attack": 375, "defense": 385, "element": "Wind",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_Remove-bg.ai_1732696596976.png"},
                {"name": "Chaos Alteon", "hp": 430, "attack": 385, "defense": 370, "element": "Corrupted",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_Chaos_Alteon-removebg-preview.png"},
                {"name": "Argo", "hp": 440, "attack": 380, "defense": 375, "element": "Dark",
                 "url": "https://storage.googleapis.com/fablerpg-f74c2.appspot.com/295173706496475136_Argo-removebg-preview.png"},
            ],
            6: [
                {"name": "Ultra Chaos Dragon", "hp": 500, "attack": 470, "defense": 460, "element": "Corrupted",
                 "url": ""},
                {"name": "Earth Titan Golem", "hp": 480, "attack": 465, "defense": 455, "element": "Earth", "url": ""},
                {"name": "Water Titan Kraken", "hp": 520, "attack": 475, "defense": 460, "element": "Water", "url": ""},
                {"name": "Shadow Lord Sepulchure", "hp": 490, "attack": 470, "defense": 455, "element": "Dark",
                 "url": ""},
                {"name": "Wind Elemental Titan", "hp": 510, "attack": 475, "defense": 465, "element": "Wind",
                 "url": ""},
                {"name": "Dwakel Mecha", "hp": 505, "attack": 480, "defense": 470, "element": "Electric", "url": ""},
                {"name": "Infernal Warlord", "hp": 495, "attack": 470, "defense": 455, "element": "Fire", "url": ""},
                {"name": "Divine Guardian", "hp": 485, "attack": 465, "defense": 455, "element": "Light", "url": ""},
                {"name": "Undead Legion Overlord", "hp": 530, "attack": 475, "defense": 460, "element": "Dark",
                 "url": ""},
                {"name": "Chaos Vordred", "hp": 515, "attack": 470, "defense": 455, "element": "Corrupted", "url": ""},
                {"name": "Dire Mammoth", "hp": 525, "attack": 475, "defense": 460, "element": "Nature", "url": ""},
                {"name": "Storm Titan Lord", "hp": 540, "attack": 480, "defense": 470, "element": "Electric",
                 "url": ""},
                {"name": "Leviathan", "hp": 520, "attack": 475, "defense": 460, "element": "Water", "url": ""},
                {"name": "Earth Elemental Lord", "hp": 535, "attack": 475, "defense": 465, "element": "Earth",
                 "url": ""},
                {"name": "Shadow Beast King", "hp": 500, "attack": 470, "defense": 455, "element": "Dark", "url": ""},
                {"name": "Blazing Inferno Dragon", "hp": 510, "attack": 475, "defense": 460, "element": "Fire",
                 "url": ""},
                {"name": "Obsidian Colossus", "hp": 525, "attack": 465, "defense": 475, "element": "Earth", "url": ""},
                {"name": "Tempest Dragon", "hp": 515, "attack": 460, "defense": 470, "element": "Wind", "url": ""},
                {"name": "Chaos Beast Kathool", "hp": 530, "attack": 475, "defense": 460, "element": "Corrupted",
                 "url": ""},
                {"name": "Great Treeant", "hp": 540, "attack": 470, "defense": 455, "element": "Nature", "url": ""},
            ],
            7: [
                {"name": "Ultra Chaos Vordred", "hp": 600, "attack": 570, "defense": 560, "element": "Corrupted",
                 "url": ""},
                {"name": "Earth Colossus", "hp": 580, "attack": 565, "defense": 555, "element": "Earth", "url": ""},
                {"name": "Water Titan Leviathan Prime", "hp": 620, "attack": 575, "defense": 560, "element": "Water",
                 "url": ""},
                {"name": "Shadow Lord Alteon", "hp": 590, "attack": 570, "defense": 555, "element": "Dark", "url": ""},
                {"name": "Wind Titan Zephyr", "hp": 610, "attack": 575, "defense": 565, "element": "Wind", "url": ""},
                {"name": "Dwakel Mecha Prime", "hp": 605, "attack": 580, "defense": 570, "element": "Electric",
                 "url": ""},
                {"name": "Infernal Dragon", "hp": 595, "attack": 570, "defense": 555, "element": "Fire", "url": ""},
                {"name": "Divine Light Elemental", "hp": 585, "attack": 565, "defense": 555, "element": "Light",
                 "url": ""},
                {"name": "Undead Legion Titan", "hp": 630, "attack": 575, "defense": 560, "element": "Dark", "url": ""},
                {"name": "Chaos Beast Escherion", "hp": 615, "attack": 570, "defense": 555, "element": "Corrupted",
                 "url": ""},
                {"name": "Dire Bear", "hp": 625, "attack": 575, "defense": 560, "element": "Nature", "url": ""},
                {"name": "Storm Emperor", "hp": 640, "attack": 580, "defense": 570, "element": "Electric", "url": ""},
                {"name": "Kraken", "hp": 620, "attack": 575, "defense": 560, "element": "Water", "url": ""},
                {"name": "Earth Elemental Prime", "hp": 635, "attack": 575, "defense": 565, "element": "Earth",
                 "url": ""},
                {"name": "Shadow King", "hp": 600, "attack": 570, "defense": 555, "element": "Dark", "url": ""},
                {"name": "Blazing Inferno Titan", "hp": 610, "attack": 575, "defense": 560, "element": "Fire",
                 "url": ""},
                {"name": "Obsidian Titan", "hp": 625, "attack": 565, "defense": 575, "element": "Earth", "url": ""},
                {"name": "Tempest Dragon Prime", "hp": 615, "attack": 560, "defense": 570, "element": "Wind",
                 "url": ""},
                {"name": "Chaos Beast Ledgermayne", "hp": 630, "attack": 575, "defense": 560, "element": "Corrupted",
                 "url": ""},
                {"name": "Ancient Treeant", "hp": 640, "attack": 570, "defense": 555, "element": "Nature", "url": ""},
            ],
            8: [
                {"name": "Ultra Chaos Beast", "hp": 700, "attack": 680, "defense": 670, "element": "Corrupted",
                 "url": ""},
                {"name": "Earth Colossus Prime", "hp": 680, "attack": 675, "defense": 665, "element": "Earth",
                 "url": ""},
                {"name": "Water Lord Leviathan", "hp": 720, "attack": 690, "defense": 675, "element": "Water",
                 "url": ""},
                {"name": "Shadow Dragon", "hp": 690, "attack": 680, "defense": 665, "element": "Dark", "url": ""},
                {"name": "Wind Titan Lord", "hp": 710, "attack": 685, "defense": 675, "element": "Wind", "url": ""},
                {"name": "Dwakel Ultimate Mecha", "hp": 705, "attack": 690, "defense": 680, "element": "Electric",
                 "url": ""},
                {"name": "Infernal Warlord Prime", "hp": 695, "attack": 680, "defense": 665, "element": "Fire",
                 "url": ""},
                {"name": "Divine Lightbringer", "hp": 685, "attack": 675, "defense": 665, "element": "Light",
                 "url": ""},
                {"name": "Undead Legion Overlord", "hp": 730, "attack": 680, "defense": 670, "element": "Dark",
                 "url": ""},
                {"name": "Chaos Beast Wolfwing", "hp": 715, "attack": 675, "defense": 665, "element": "Corrupted",
                 "url": ""},
                {"name": "Dire Lion", "hp": 725, "attack": 690, "defense": 675, "element": "Nature", "url": ""},
                {"name": "Storm King Prime", "hp": 740, "attack": 695, "defense": 685, "element": "Electric",
                 "url": ""},
                {"name": "Leviathan Prime", "hp": 720, "attack": 680, "defense": 670, "element": "Water", "url": ""},
                {"name": "Earth Elemental King", "hp": 735, "attack": 675, "defense": 680, "element": "Earth",
                 "url": ""},
                {"name": "Shadow Lord Prime", "hp": 700, "attack": 680, "defense": 665, "element": "Dark", "url": ""},
                {"name": "Blazing Inferno Dragon Prime", "hp": 710, "attack": 685, "defense": 670, "element": "Fire",
                 "url": ""},
                {"name": "Obsidian Colossus Prime", "hp": 725, "attack": 675, "defense": 680, "element": "Earth",
                 "url": ""},
                {"name": "Tempest Dragon Lord", "hp": 715, "attack": 670, "defense": 680, "element": "Wind", "url": ""},
                {"name": "Chaos Beast Kimberly", "hp": 730, "attack": 680, "defense": 665, "element": "Corrupted",
                 "url": ""},
                {"name": "Elder Treeant", "hp": 740, "attack": 675, "defense": 660, "element": "Nature", "url": ""},
            ],
            9: [
                {"name": "Ultra Kathool", "hp": 800, "attack": 780, "defense": 770, "element": "Corrupted", "url": ""},
                {"name": "Earth Titan Overlord", "hp": 780, "attack": 775, "defense": 765, "element": "Earth",
                 "url": ""},
                {"name": "Water Lord Leviathan Prime", "hp": 820, "attack": 790, "defense": 775, "element": "Water",
                 "url": ""},
                {"name": "Shadow Lord Alteon Prime", "hp": 790, "attack": 780, "defense": 765, "element": "Dark",
                 "url": ""},
                {"name": "Wind Titan Emperor", "hp": 810, "attack": 785, "defense": 775, "element": "Wind", "url": ""},
                {"name": "Dwakel Ultimate Mecha Prime", "hp": 805, "attack": 790, "defense": 780,
                 "element": "Electric", "url": ""},
                {"name": "Infernal Warlord Supreme", "hp": 795, "attack": 780, "defense": 765, "element": "Fire",
                 "url": ""},
                {"name": "Divine Light Guardian", "hp": 785, "attack": 775, "defense": 765, "element": "Light",
                 "url": ""},
                {"name": "Undead Legion DoomKnight", "hp": 830, "attack": 780, "defense": 770, "element": "Dark",
                 "url": ""},
                {"name": "Chaos Beast Tibicenas", "hp": 815, "attack": 775, "defense": 765, "element": "Corrupted",
                 "url": ""},
                {"name": "Dire Mammoth Prime", "hp": 825, "attack": 790, "defense": 775, "element": "Nature",
                 "url": ""},
                {"name": "Storm Emperor Prime", "hp": 840, "attack": 795, "defense": 785, "element": "Electric",
                 "url": ""},
                {"name": "Kraken Supreme", "hp": 820, "attack": 780, "defense": 770, "element": "Water", "url": ""},
                {"name": "Earth Elemental Overlord", "hp": 835, "attack": 775, "defense": 780, "element": "Earth",
                 "url": ""},
                {"name": "Shadow Dragon Prime", "hp": 800, "attack": 780, "defense": 765, "element": "Dark", "url": ""},
                {"name": "Blazing Inferno Titan Prime", "hp": 810, "attack": 785, "defense": 770, "element": "Fire",
                 "url": ""},
                {"name": "Obsidian Titan Supreme", "hp": 825, "attack": 775, "defense": 785, "element": "Earth",
                 "url": ""},
                {"name": "Tempest Dragon Emperor", "hp": 815, "attack": 770, "defense": 785, "element": "Wind",
                 "url": ""},
                {"name": "Chaos Beast Iadoa", "hp": 830, "attack": 780, "defense": 765, "element": "Corrupted",
                 "url": ""},
                {"name": "Ancient Guardian Treeant", "hp": 840, "attack": 775, "defense": 760, "element": "Nature",
                 "url": ""},
            ],
            10: [
                {"name": "Ultra Chaos Vordred", "hp": 1200, "attack": 600, "defense": 600, "element": "Corrupted",
                 "url": ""},
                {"name": "Shadow Guardian", "hp": 1180, "attack": 595, "defense": 600, "element": "Dark", "url": ""},
                {"name": "Ultra Kathool", "hp": 1250, "attack": 605, "defense": 595, "element": "Corrupted", "url": ""},
                {"name": "Elemental Dragon of Time", "hp": 1220, "attack": 600, "defense": 590, "element": "Electric",
                 "url": ""},
                {"name": "Celestial Dragon", "hp": 1240, "attack": 595, "defense": 595, "element": "Light", "url": ""},
                {"name": "Infernal Warlord Nulgath", "hp": 1230, "attack": 600, "defense": 585, "element": "Fire",
                 "url": ""},
                {"name": "Obsidian Colossus Supreme", "hp": 1260, "attack": 605, "defense": 580, "element": "Earth",
                 "url": ""},
                {"name": "Tempest Dragon King", "hp": 1210, "attack": 600, "defense": 600, "element": "Wind",
                 "url": ""},
                {"name": "Chaos Lord Xiang", "hp": 1250, "attack": 605, "defense": 575, "element": "Corrupted",
                 "url": ""},
                {"name": "Dark Spirit Orbs", "hp": 1190, "attack": 595, "defense": 605, "element": "Dark", "url": ""},
                {"name": "Electric Titan", "hp": 1230, "attack": 600, "defense": 590, "element": "Electric", "url": ""},
                {"name": "Light Elemental Lord", "hp": 1240, "attack": 595, "defense": 595, "element": "Light",
                 "url": ""},
                {"name": "Flame Dragon", "hp": 1220, "attack": 605, "defense": 585, "element": "Fire", "url": ""},
                {"name": "ShadowFlame Dragon", "hp": 1200, "attack": 600, "defense": 600, "element": "Dark", "url": ""},
                {"name": "Chaos Beast Mana Golem", "hp": 1250, "attack": 605, "defense": 575, "element": "Corrupted",
                 "url": ""},
                {"name": "Electric Phoenix", "hp": 1230, "attack": 600, "defense": 590, "element": "Electric",
                 "url": ""},
                {"name": "Light Bringer", "hp": 1240, "attack": 595, "defense": 595, "element": "Light", "url": ""},
                {"name": "Void Dragon", "hp": 1260, "attack": 605, "defense": 580, "element": "Corrupted", "url": ""},
                {"name": "Elemental Titan", "hp": 1210, "attack": 600, "defense": 600, "element": "Electric",
                 "url": ""},
                {"name": "Celestial Guardian Dragon", "hp": 1250, "attack": 605, "defense": 580, "element": "Light",
                 "url": ""},
            ],
            11: [
                {"name": "Drakath", "hp": 2500, "attack": 1022, "defense": 648, "element": "Corrupted", "url": ""},
                {"name": "Astraea", "hp": 3100, "attack": 723, "defense": 733, "element": "Light", "url": ""},
                {"name": "Sepulchure", "hp": 2310, "attack": 690, "defense": 866, "element": "Dark", "url": ""},
            ]
        }
        for level_monsters in monsters.values():
            for m in level_monsters:
                if m["name"].lower() == monster_name.lower():
                    monster = m
                    break
            if monster:
                break

        if not monster:
            await ctx.send(f"Monster '{monster_name}' not found.")
            return

        # Check the user's current pet and egg count
        async with self.bot.pool.acquire() as conn:
            pet_and_egg_count = await conn.fetchval(
                """
                SELECT COUNT(*) 
                FROM (
                    SELECT id FROM monster_pets WHERE user_id = $1
                    UNION ALL
                    SELECT id FROM monster_eggs WHERE user_id = $1 AND hatched = FALSE
                ) AS combined
                """,
                member.id
            )

        if pet_and_egg_count >= 5:
            await ctx.send(
                f"{member.display_name} cannot have more than 5 pets or eggs. Please release a pet or wait for an egg to hatch.")
            return

        import random

        # Generate a random IV percentage with weighted probabilities
        iv_percentage = random.uniform(10, 1000)

        if iv_percentage < 20:
            iv_percentage = random.uniform(90, 100)
        elif iv_percentage < 70:
            iv_percentage = random.uniform(80, 90)
        elif iv_percentage < 150:
            iv_percentage = random.uniform(70, 80)
        elif iv_percentage < 350:
            iv_percentage = random.uniform(60, 70)
        elif iv_percentage < 700:
            iv_percentage = random.uniform(50, 60)
        else:
            iv_percentage = random.uniform(30, 50)

        # Calculate total IV points (100% IV corresponds to 200 points)
        total_iv_points = (iv_percentage / 100) * 200

        def allocate_iv_points(total_points):
            # Generate three random numbers
            a = random.random()
            b = random.random()
            c = random.random()
            total = a + b + c
            # Normalize so that the sum is equal to total_points
            hp_iv = total_points * (a / total)
            attack_iv = total_points * (b / total)
            defense_iv = total_points * (c / total)
            # Round the IV points
            hp_iv = int(round(hp_iv))
            attack_iv = int(round(attack_iv))
            defense_iv = int(round(defense_iv))
            # Adjust if rounding errors cause total to deviate
            iv_sum = hp_iv + attack_iv + defense_iv
            if iv_sum != int(round(total_points)):
                diff = int(round(total_points)) - iv_sum
                # Adjust the largest IV by the difference
                max_iv = max(hp_iv, attack_iv, defense_iv)
                if hp_iv == max_iv:
                    hp_iv += diff
                elif attack_iv == max_iv:
                    attack_iv += diff
                else:
                    defense_iv += diff
            return hp_iv, attack_iv, defense_iv

        hp_iv, attack_iv, defense_iv = allocate_iv_points(total_iv_points)

        # Calculate the final stats
        hp = monster["hp"] + hp_iv
        attack = monster["attack"] + attack_iv
        defense = monster["defense"] + defense_iv

        import datetime

        # Set the egg hatch time to 90 days from now
        egg_hatch_time = datetime.datetime.utcnow() + datetime.timedelta(hours=36)
        try:
            async with self.bot.pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO monster_eggs (
                        user_id, egg_type, hp, attack, defense, element, url, hatch_time,
                        "IV", hp_iv, attack_iv, defense_iv
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12);
                    """,
                    member.id,
                    monster["name"],
                    hp,
                    attack,
                    defense,
                    monster["element"],
                    monster["url"],
                    egg_hatch_time,
                    iv_percentage,
                    hp_iv,
                    attack_iv,
                    defense_iv
                )

            await ctx.send(
                f"{member.mention} has received a **{monster['name']} Egg** with an IV of {iv_percentage:.2f}%! It will hatch in 36 hours."
            )
        except Exception as e:
            await ctx.send(f"An error occurred: {e}")

    @is_gm()
    @commands.command(hidden=True)
    async def processlevelup(self, ctx, target: discord.Member, xp: int, conn=None):
        try:
            if conn is None:
                conn = await self.bot.pool.acquire()
                local = True
            else:
                local = False
            reward_text = ""
            stat_point_received = False

            new_level = int(rpgtools.xptolevel(int(xp)))
            await ctx.send(new_level)
            if new_level % 2 == 0 and new_level > 0:
                await ctx.send("breaker")
                # Increment statpoints directly in the database and fetch the updated value
                update_query = 'UPDATE profile SET "statpoints" = "statpoints" + 1 WHERE "user" = $1 RETURNING "statpoints";'
                new_statpoints = await conn.fetchval(update_query, target.id)
                reward_text += f"You also received **1 stat point** (total: {new_statpoints}). "
                stat_point_received = True

            if (reward := random.choice(["crates", "money", "item"])) == "crates":
                if new_level < 6:
                    column = "crates_common"
                    amount = new_level
                    reward_text = f"**{amount}** {self.bot.cogs['Crates'].emotes.common}"
                elif new_level < 10:
                    column = "crates_uncommon"
                    amount = round(new_level / 2)
                    reward_text = f"**{amount}** {self.bot.cogs['Crates'].emotes.uncommon}"
                elif new_level < 18:
                    column = "crates_rare"
                    amount = 2
                    reward_text = f"**2** {self.bot.cogs['Crates'].emotes.rare}"
                elif new_level < 27:
                    column = "crates_rare"
                    amount = 3
                    reward_text = f"**3** {self.bot.cogs['Crates'].emotes.rare}"
                else:
                    column = "crates_magic"
                    amount = 1
                    reward_text = f"**1** {self.bot.cogs['Crates'].emotes.magic}"
                await self.bot.log_transaction(
                    ctx,
                    from_=0,
                    to=ctx.author.id,
                    subject="crates",
                    data={"Rarity": column.split("_")[1], "Amount": amount},
                )
                await self.bot.pool.execute(
                    f'UPDATE profile SET {column}={column}+$1 WHERE "user"=$2;',
                    amount,
                    target.id,
                )
            elif reward == "item":
                stat = min(round(new_level * 1.5), 75)
                item = await self.bot.create_random_item(
                    minstat=stat,
                    maxstat=stat,
                    minvalue=1000,
                    maxvalue=1000,
                    owner=target,
                    insert=False,
                    conn=conn,
                )

                item["name"] = _("Level {new_level} Memorial").format(new_level=new_level)
                reward_text = _("a special weapon")
                await self.bot.create_item(**item)
                await self.bot.log_transaction(
                    ctx,
                    from_=1,
                    to=target,
                    subject="Memorial Item",
                    data={"Name": item["name"], "Value": 1000},
                    conn=conn,
                )
            elif reward == "money":
                money = new_level * 1000
                await conn.execute(
                    'UPDATE profile SET "money"="money"+$1 WHERE "user"=$2;',
                    money,
                    target.id,
                )
                await self.bot.log_transaction(
                    ctx,
                    from_=1,
                    to=target,
                    subject="Level Up!",
                    data={"Gold": money},
                    conn=conn,
                )
                reward_text = f"**${money}**"
            old_level = new_level - 1
            additional = (
                _("You can now choose your second class using `{prefix}class`!").format(
                    prefix=ctx.clean_prefix
                )
                if old_level < 12 and new_level >= 12
                else ""
            )

            if local:
                await self.bot.pool.release(conn)

            await ctx.send(
                _(
                    "You reached a new level: **{new_level}** :star:! You received {reward} "
                    "as a reward :tada:! {additional}"
                ).format(new_level=new_level, reward=reward_text, additional=additional)
            )
        except Exception as e:
            import traceback
            error_message = f"Error occurred: {e}\n"
            error_message += traceback.format_exc()
            await ctx.send(error_message)
            print(error_message)


    @is_gm()
    @commands.command(hidden=True, brief=_("Wipe someone's donation perks."))
    @locale_doc
    async def gmwipeperks(self, ctx, target: UserWithCharacter, *, reason: str = None):
        _(
            """`<target>` - A discord User with character
            `[reason]` - The reason this action was done, defaults to the command message link

            Wipe a user's donation perks. This will:
              - set their background to the default
              - set both their classes to No Class
              - reverts all items to their original type and name
              - sets their guild's member limit to 50

            Only Game Masters can use this command."""
        )
        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                'UPDATE profile SET "background"=$1, "class"=$2 WHERE "user"=$3;',
                "0",
                ["No Class", "No Class"],
                target.id,
            )
            await conn.execute(
                'UPDATE allitems SET "name"=CASE WHEN "original_name" IS NULL THEN'
                ' "name" ELSE "original_name" END, "type"=CASE WHEN "original_type" IS'
                ' NULL THEN "type" ELSE "original_type" END WHERE "owner"=$1;',
                target.id,
            )
            await conn.execute(
                'UPDATE guild SET "memberlimit"=$1 WHERE "leader"=$2;', 50, target.id
            )

        await ctx.send(
            _(
                "Successfully reset {target}'s background, class, item names and guild"
                " member limit."
            ).format(target=target)
        )

        with handle_message_parameters(
                content="**{gm}** reset **{target}**'s donator perks.\n\nReason: *{reason}*".format(
                    gm=ctx.author,
                    target=target,
                    reason=reason or f"<{ctx.message.jump_url}>",
                )
        ) as params:
            await self.bot.http.send_message(
                self.bot.config.game.gm_log_channel,
                params=params,
            )

    @is_gm()
    @commands.command(hidden=True, brief=_("Reset someone's classes"))
    @locale_doc
    async def gmresetclass(self, ctx, target: UserWithCharacter, *, reason: str = None):
        _(
            """`<target>` - a discord User with character
            `[reason]` - The reason this action was done, defaults to the command message link

            Reset a user's classes to No Class. They can then choose their class again for free.

            Only Game Masters can use this command."""
        )
        await self.bot.pool.execute(
            """UPDATE profile SET "class"='{"No Class", "No Class"}' WHERE "user"=$1;""",
            target.id,
        )

        await ctx.send(_("Successfully reset {target}'s class.").format(target=target))

        with handle_message_parameters(
                content="**{gm}** reset **{target}**'s class.\n\nReason: *{reason}*".format(
                    gm=ctx.author,
                    target=target,
                    reason=reason or f"<{ctx.message.jump_url}>",
                )
        ) as params:
            await self.bot.http.send_message(
                self.bot.config.game.gm_log_channel,
                params=params,
            )

    async def fetch_image(self, url: str):
        """Fetches an image from a given URL."""
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                return await resp.read()

    async def fetch_avatar(self, user_id: int):
        """Fetches the avatar of a user given their ID."""
        user = await self.bot.fetch_user(user_id)
        avatar_url = str(user.avatar)  # Here's the change
        return await self.fetch_image(avatar_url)

    @commands.command(name='poop')
    async def poop(self, ctx, user: discord.Member = None, *, reason=None):
        """Bans a user from the server by their tag and sends their cropped avatar on an external image."""
        external_image_url = "https://i.ibb.co/T1ZW86R/ew-i-stepped-in-shit.png"  # replace with your PNG link

        if not user:
            await ctx.send("Please tag a valid user.")
            return

        if user.id == 295173706496475136:
            await ctx.send("What are you high?")
            return

        try:
            base_image_data = await self.fetch_image(external_image_url)
            avatar_data = await self.fetch_avatar(user.id)

            with io.BytesIO(base_image_data) as base_io, io.BytesIO(avatar_data) as avatar_io:
                base_image = Image.open(base_io).convert("RGBA")  # Convert base image to RGBA mode

                # Open the avatar, convert to RGBA, and resize
                avatar_image = Image.open(avatar_io).convert("RGBA")
                avatar_resized = avatar_image.resize((200, 200))  # Adjust size as needed

                # Rotate the avatar without any fillcolor
                avatar_resized = avatar_resized.rotate(35, expand=True)

                # Calculate the vertical shift - 10% of the avatar's height
                vertical_shift = int(avatar_resized.height * 0.20)
                x_center = (base_image.width - avatar_resized.width) // 2

                y_position_75_percent = int(base_image.height * 0.75)
                y_center = y_position_75_percent - (avatar_resized.height // 2)

                # Check if the avatar has an alpha channel (transparency) and use it as a mask if present
                mask = avatar_resized.split()[3] if avatar_resized.mode == 'RGBA' else None

                base_image.paste(avatar_resized, (x_center, y_center), mask)

                with io.BytesIO() as output:
                    base_image.save(output, format="PNG")
                    output.seek(0)
                    await ctx.send(file=discord.File(output, 'banned_avatar.png'))

            # user = Object(id=user_id)
            # await ctx.guild.ban(user, reason=reason)

            # await ctx.send(f'Trash taken out!')
            # await ctx.send(f'The trash known as <@{user_id}> was taken out in **__1 server(s)__** for the reason: {reason}')
        except HTTPException:
            await ctx.send(f'Failed to fetch user or image.')
        except Exception as e:
            await ctx.send(f'An error occurred: {e}')

    @commands.command(name='trash')
    async def ban_by_id(self, ctx, user: discord.Member = None, *, reason=None):
        """Bans a user from the server by their ID and sends their cropped avatar on an external image."""
        external_image_url = "https://i.ibb.co/PT7S74n/images-jpeg-111.png"  # replace with your PNG link

        if user.id == 295173706496475136:
            await ctx.send("What are you high?")
            return

        try:
            base_image_data = await self.fetch_image(external_image_url)
            avatar_data = await self.fetch_avatar(user.id)

            with io.BytesIO(base_image_data) as base_io, io.BytesIO(avatar_data) as avatar_io:
                base_image = Image.open(base_io).convert("RGBA")  # Convert base image to RGBA mode

                # Open the avatar, convert to RGBA, and resize
                avatar_image = Image.open(avatar_io).convert("RGBA")
                avatar_resized = avatar_image.resize((100, 100))  # Adjust size as needed

                # Rotate the avatar without any fillcolor
                avatar_resized = avatar_resized.rotate(35, expand=True)

                # Calculate the vertical shift - 10% of the avatar's height
                vertical_shift = int(avatar_resized.height * 0.20)

                x_center = (base_image.width - avatar_resized.width) // 2
                y_center = (base_image.height - avatar_resized.height) // 2 - vertical_shift

                # Check if the avatar has an alpha channel (transparency) and use it as a mask if present
                mask = avatar_resized.split()[3] if avatar_resized.mode == 'RGBA' else None

                base_image.paste(avatar_resized, (x_center, y_center), mask)

                with io.BytesIO() as output:
                    base_image.save(output, format="PNG")
                    output.seek(0)
                    await ctx.send(file=discord.File(output, 'banned_avatar.png'))

            # user = Object(id=user_id)
            # await ctx.guild.ban(user, reason=reason)

            await ctx.send(f'Trash taken out!')
            # await ctx.send(f'The trash known as <@{user_id}> was taken out in **__1 server(s)__** for the reason: {reason}')
        except HTTPException:
            await ctx.send(f'Failed to fetch user or image.')
        except Exception as e:
            await ctx.send(f'An error occurred: {e}')


    @is_gm()
    @user_cooldown(604800)  # 7 days
    @commands.command(hidden=True, brief=_("Sign an item"))
    @locale_doc
    async def gmsign(self, ctx, itemid: int, text: str, *, reason: str = None):
        _(
            """`<itemid>` - the item's ID to sign
            `<text>` - The signature to write, must be less than 50 characters combined with the Game Master's tag. This should be in double quotes if the text has multiple words.
            `[reason]` - The reason this action was done, defaults to the command message link

            Sign an item. The item's signature is visible in a user's inventory.

            Only Game Masters can use this command.
            (This command has a cooldown of 7 days.)"""
        )
        text = f"{text} (signed by {ctx.author})"
        if len(text) > 100:
            await self.bot.reset_cooldown(ctx)
            return await ctx.send(_("Text exceeds 50 characters."))
        await self.bot.pool.execute(
            'UPDATE allitems SET "signature"=$1 WHERE "id"=$2;', text, itemid
        )
        await ctx.send(_("Item successfully signed."))

        with handle_message_parameters(
                content="**{gm}** signed {itemid} with *{text}*.\n\nReason: *{reason}*".format(
                    gm=ctx.author,
                    itemid=itemid,
                    text=text,
                    reason=reason or f"<{ctx.message.jump_url}>",
                )
        ) as params:
            await self.bot.http.send_message(
                self.bot.config.game.gm_log_channel,
                params=params,
            )

    def load_patron_ids(self):
        try:
            with open("patron_ids.json", "r") as file:
                return json.load(file)
        except FileNotFoundError:
            return []

    def save_patron_ids(self):
        with open("patron_ids.json", "w") as file:
            json.dump(self.patron_ids, file)

    def add_patron(self, user_id: int):
        if user_id not in self.patron_ids:
            self.patron_ids.append(user_id)
            self.save_patron_ids()  # Save updated patron IDs
            return True
        else:
            return False

    def remove_patron(self, user_id: int):
        if user_id in self.patron_ids:
            self.patron_ids.remove(user_id)
            self.save_patron_ids()  # Save updated patron IDs
            return True
        else:
            return False

    @is_gm()
    @commands.command(hidden=True, brief=_("Add Patreon"))
    async def add_patron(self, ctx, user_id: int):
        """Add a patron by their user ID."""
        if user_id not in self.patron_ids:
            self.patron_ids.append(user_id)
            self.save_patron_ids()  # Use self to access the method
            await ctx.send(f"User with ID {user_id} has been added as a patron.")
        else:
            await ctx.send(f"User with ID {user_id} is already a patron.")

    @is_gm()
    @commands.command(hidden=True, brief=_("Remove Patreon"))
    async def remove_patron(self, ctx, user_id: int):
        """Remove a patron by their user ID."""
        if self.remove_patron(user_id):
            await ctx.send(f"User with ID {user_id} has been removed as a patron.")
        else:
            await ctx.send(f"User with ID {user_id} is not a patron.")

    @is_gm()
    @commands.command(hidden=True, brief=_("Start an auction"))
    @locale_doc
    async def gmauction(self, ctx, *, item: str):
        _(
            """`<item>` - a description of what is being auctioned

            Starts an auction on the support server. Users are able to bid. The auction timeframe extends by 30 minutes if users keep betting.
            The auction ends when no user bids in a 30 minute timeframe.

            The item is not given automatically and the needs to be given manually.

            Only Game Masters can use this command."""
        )
        if self.top_auction is not None:
            return await ctx.send(_("There's still an auction running."))
        try:
            channel = discord.utils.get(
                self.bot.get_guild(self.bot.config.game.support_server_id).channels,
                name="⟢auctions〡🧾",
            )
        except AttributeError:
            return await ctx.send(_("Auctions channel wasn't found."))
        role_id = 1146279043692503112  # Replace with the actual role ID
        role = discord.utils.get(ctx.guild.roles, id=role_id)
        await channel.send(
            f"{ctx.author.mention} started auction on **{item}**! Please use"
            f" `{ctx.clean_prefix}bid amount` to raise the bid from any channel. If no more bids are sent"
            f" within the 30 minute timeframe of the highest bid, the auction is over. {role.mention} "
        )
        self.top_auction = (ctx.author, 0)
        timer = 1800  # 30 minutes in seconds
        self.auction_entry = asyncio.Event()

        while True:
            await asyncio.sleep(timer)  # Wait for 30 minutes
            if not self.auction_entry.is_set():
                if self.top_auction:
                    winner, winning_bid = self.top_auction
                    channel = discord.utils.get(
                        self.bot.get_guild(self.bot.config.game.support_server_id).channels,
                        name="🧾auctions🧾",
                    )
                    await channel.send(
                        f"No more bids for **{item}**. Auction ended. **{winner.mention}** wins the auction with a bid of **${winning_bid}**!"
                    )
                else:
                    channel = discord.utils.get(
                        self.bot.get_guild(self.bot.config.game.support_server_id).channels,
                        name="🧾auctions🧾",
                    )
                    await channel.send(
                        f"No bids were made for **{item}**. Auction ended with no winner."
                    )
                self.top_auction = None
                self.auction_entry.clear()
                break  # End the auction

            self.auction_entry.clear()  # Clear the event for the next iteration

    @has_char()
    @commands.command(hidden=True, brief=_("Bid on an auction"))
    @locale_doc
    async def bid(self, ctx, amount: IntGreaterThan(0)):
        _(
            """`<amount>` - the amount of money to bid, must be higher than the current highest bid

            Bid on an ongoing auction.

            The amount is removed from you as soon as you bid and given back if someone outbids you. This is to prevent bidding impossibly high and then not paying up."""
        )
        if self.top_auction is None:
            return await ctx.send(_("No auction running."))

        if amount <= self.top_auction[1]:
            return await ctx.send(_("Bid too low."))

        if ctx.character_data["money"] < amount:
            return await ctx.send(_("You are too poor."))

        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                'UPDATE profile SET "money"="money"+$1 WHERE "user"=$2;',
                self.top_auction[1],
                self.top_auction[0].id,
            )
            await self.bot.log_transaction(
                ctx,
                from_=1,
                to=self.top_auction[0].id,
                subject="bid",
                data={"Gold": self.top_auction[1]},
                conn=conn,
            )
            self.top_auction = (ctx.author, amount)
            self.auction_entry.set()
            await conn.execute(
                'UPDATE profile SET "money"="money"-$1 WHERE "user"=$2;',
                amount,
                ctx.author.id,
            )
            await self.bot.log_transaction(
                ctx,
                from_=ctx.author.id,
                to=2,
                subject="bid",
                data={"Gold": amount},
                conn=conn,
            )
        await ctx.send(_("Bid submitted."))
        channel = discord.utils.get(
            self.bot.get_guild(self.bot.config.game.support_server_id).channels,
            name="🧾auctions🧾",
        )
        await channel.send(
            f"**{ctx.author.mention}** bids **${amount}**! Check above for what's being auctioned."
        )

    @is_gm()
    @commands.command(
        aliases=["gmcd", "gmsetcd"], hidden=True, brief=_("Set a cooldown")
    )
    @locale_doc
    async def gmsetcooldown(
            self,
            ctx,
            user: discord.User | int,
            command: str,
            *,
            reason: str = None,
    ):
        _(
            """`<user>` - A discord User or their User ID
            `<command>` - the command which the cooldown is being set for (subcommands in double quotes, i.e. "guild create")
            `[reason]` - The reason this action was done, defaults to the command message link

            Reset a cooldown for a user and commmand.

            Only Game Masters can use this command."""
        )
        if not isinstance(user, int):
            user_id = user.id
        else:
            user_id = user

        result = await self.bot.redis.execute_command("DEL", f"cd:{user_id}:{command}")

        if result == 1:
            await ctx.send(_("The cooldown has been updated!"))
            if ctx.author.id != 295173706496475136:
                with handle_message_parameters(
                        content="**{gm}** reset **{user}**'s cooldown for the {command} command.\n\nReason: *{reason}*".format(
                            gm=ctx.author,
                            user=user,
                            command=command,
                            reason=reason or f"<{ctx.message.jump_url}>",
                        )
                ) as params:
                    await self.bot.http.send_message(
                        self.bot.config.game.gm_log_channel,
                        params=params,
                    )
        else:
            await ctx.send(
                _(
                    "Cooldown setting unsuccessful (maybe you mistyped the command name"
                    " or there is no cooldown for the user?)."
                )
            )

    @is_gm()
    @commands.command(
        aliases=["gmml", "gmluck"],
        hidden=True,
        brief=_("Update the luck for all followers"),
    )
    @locale_doc
    async def gmmakeluck(self, ctx) -> None:
        _(
            """Sets the luck for all gods to a random value and give bonus luck to the top 25 followers.

            Only Game Masters can use this command."""
        )
        text_collection = ["**This week's luck has been decided:**\n"]
        all_ids = []
        async with self.bot.pool.acquire() as conn:
            for god in self.bot.config.gods:
                luck = (
                        random.randint(
                            god["boundary_low"] * 100, god["boundary_high"] * 100
                        )
                        / 100
                )
                ids = await conn.fetch(
                    'UPDATE profile SET "luck"=round($1, 2) WHERE "god"=$2 RETURNING'
                    ' "user";',
                    luck,
                    god["name"],
                )
                all_ids.extend([u["user"] for u in ids])
                top_followers = [
                    u["user"]
                    for u in await conn.fetch(
                        'SELECT "user" FROM profile WHERE "god"=$1 ORDER BY "favor"'
                        " DESC LIMIT 25;",
                        god["name"],
                    )
                ]
                await conn.execute(
                    'UPDATE profile SET "luck"=CASE WHEN "luck"+round($1, 2)>=2.0 THEN'
                    ' 2.0 ELSE "luck"+round($1, 2) END WHERE "user"=ANY($2);',
                    0.5,
                    top_followers[:5],
                )
                await conn.execute(
                    'UPDATE profile SET "luck"=CASE WHEN "luck"+round($1, 2)>=2.0 THEN'
                    ' 2.0 ELSE "luck"+round($1, 2) END WHERE "user"=ANY($2);',
                    0.4,
                    top_followers[5:10],
                )
                await conn.execute(
                    'UPDATE profile SET "luck"=CASE WHEN "luck"+round($1, 2)>=2.0 THEN'
                    ' 2.0 ELSE "luck"+round($1, 2) END WHERE "user"=ANY($2);',
                    0.3,
                    top_followers[10:15],
                )
                await conn.execute(
                    'UPDATE profile SET "luck"=CASE WHEN "luck"+round($1, 2)>=2.0 THEN'
                    ' 2.0 ELSE "luck"+round($1, 2) END WHERE "user"=ANY($2);',
                    0.2,
                    top_followers[15:20],
                )
                await conn.execute(
                    'UPDATE profile SET "luck"=CASE WHEN "luck"+round($1, 2)>=2.0 THEN'
                    ' 2.0 ELSE "luck"+round($1, 2) END WHERE "user"=ANY($2);',
                    0.1,
                    top_followers[20:25],
                )
                text_collection.append(f"{god['name']} set to {luck}.")
            await conn.execute('UPDATE profile SET "favor"=0 WHERE "god" IS NOT NULL;')
            text_collection.append("Godless set to 1.0")
            ids = await conn.fetch(
                'UPDATE profile SET "luck"=1.0 WHERE "god" IS NULL RETURNING "user";'
            )
            all_ids.extend([u["user"] for u in ids])
        await ctx.send("\n".join(text_collection))

        with handle_message_parameters(
                content=f"**{ctx.author}** updated the global luck"
        ) as params:
            await self.bot.http.send_message(
                self.bot.config.game.gm_log_channel,
                params=params,
            )

    def cleanup_code(self, content: str) -> str:
        """Automatically removes code blocks from the code."""
        if content.startswith("```") and content.endswith("```"):
            return "\n".join(content.split("\n")[1:-1])
        return content.strip("` \n")

    @is_gm()
    @commands.command(hidden=True, name="checkuserid")
    async def checkuserid(self, ctx, discordid):
        discord_id = discordid

        # SQL query to fetch the "user" column where discordtag = $1
        query = 'SELECT "user" FROM profile WHERE discordtag = $1'

        try:
            # Fetch data from the database
            async with self.bot.pool.acquire() as conn:
                rows = await conn.fetch(query, discord_id)

            if rows:
                users = [row["user"] for row in rows]

                # Send the users that match the discord ID
                await ctx.send(f"{', '.join(map(str, users))}")
            else:
                await ctx.send(f"No users found with Discord ID {discord_id}.")

        except Exception as e:
            await ctx.send(f"An error occurred: {e}")

    @is_gm()
    @commands.command(hidden=True, name="eval")
    async def _eval(self, ctx: Context, *, body: str) -> None:
        """Evaluates a code"""

        env = {
            "bot": self.bot,
            "ctx": ctx,
            "channel": ctx.channel,
            "author": ctx.author,
            "guild": ctx.guild,
            "message": ctx.message,
            "__last__": self._last_result,
        }

        env.update(globals())

        body = self.cleanup_code(body)
        stdout = io.StringIO()
        token = random_token(self.bot.user.id)

        to_compile = f'async def func():\n{textwrap.indent(body, "  ")}'

        try:
            exec(to_compile, env)
        except Exception as e:
            return await ctx.send(f"```py\n{e.__class__.__name__}: {e}\n```")

        func = env["func"]
        try:
            with redirect_stdout(stdout):
                ret = await func()
            if ret is not None:
                ret = str(ret).replace(self.bot.http.token, token)
        except Exception:
            value = stdout.getvalue()
            value = value.replace(self.bot.http.token, token)
            await ctx.send(f"```py\n{value}{traceback.format_exc()}\n```")
        else:
            value = stdout.getvalue()
            value = value.replace(self.bot.http.token, token)
            try:
                await ctx.message.add_reaction("blackcheck:441826948919066625")
            except discord.Forbidden:
                pass

            if ret is None:
                if value:
                    await ctx.send(f"```py\n{value}\n```")
            else:
                self._last_result = ret
                await ctx.send(f"```py\n{value}{ret}\n```")

    @is_gm()
    @commands.command(hidden=True)
    async def purge(self, ctx, amount: int):
        # Delete messages from the channel
        await ctx.channel.purge(limit=amount + 1)

    @is_gm()
    @commands.command(hidden=True)
    async def getusergod(self, ctx, god_name: str, get_names: bool = False):

        def split_message(message: str, max_length: int = 2000):
            """Splits a message into chunks that are less than max_length."""
            return [message[i:i + max_length] for i in range(0, len(message), max_length)]

        async def fetch_users_concurrently(user_ids, batch_size=5):
            """Fetch users concurrently in batches to avoid rate limits."""
            fetched_users = {}
            for i in range(0, len(user_ids), batch_size):
                batch = user_ids[i:i + batch_size]
                users = await asyncio.gather(*(self.bot.fetch_user(uid) for uid in batch))
                for uid, user in zip(batch, users):
                    fetched_users[uid] = user
            return fetched_users

        try:
            async with self.bot.pool.acquire() as conn:
                if god_name.lower() == "all":
                    query = '''
                        SELECT god, COUNT(*) AS count
                        FROM profile
                        GROUP BY god
                    '''
                    data = await conn.fetch(query)

                    if data:
                        if get_names:
                            user_ids = [row['user'] for row in data]
                            users_data = await fetch_users_concurrently(user_ids)

                            users = []
                            for row in data:
                                user = users_data.get(row['user'], None)
                                god = row['god'] if row['god'] is not None else 'Godless'
                                users.append(f"{god}: {user.name if user else 'Unknown User'}")

                            chunks = split_message("\n".join(users))
                            for chunk in chunks:
                                await ctx.send(chunk)
                        else:
                            god_counts = {row['god'] if row['god'] is not None else 'Godless': row['count'] for row in
                                          data}
                            message = "\n".join([f"{god}: {count} users" for god, count in god_counts.items()])
                            chunks = split_message(message)
                            for chunk in chunks:
                                await ctx.send(chunk)

                    else:
                        await ctx.send("No data found in the profile table")

                elif god_name.lower() == "none":
                    query = '''
                        SELECT "user"
                        FROM profile
                        WHERE god IS NULL
                    '''
                    data = await conn.fetch(query)

                    if data:
                        user_ids = [row['user'] for row in data]
                        users_data = await fetch_users_concurrently(user_ids)

                        users = [users_data.get(uid, 'Unknown User').name for uid in user_ids]

                        chunks = split_message("\n".join(users))
                        for chunk in chunks:
                            await ctx.send(chunk)
                    else:
                        await ctx.send("No godless users found")

                else:
                    if get_names:
                        query = '''
                            SELECT "user"
                            FROM profile
                            WHERE god = $1
                        '''
                        data = await conn.fetch(query, god_name)

                        if data:
                            user_ids = [row['user'] for row in data]
                            users_data = await fetch_users_concurrently(user_ids)

                            users = [users_data.get(uid, 'Unknown User').name for uid in user_ids]

                            chunks = split_message("\n".join(users))
                            for chunk in chunks:
                                await ctx.send(chunk)
                        else:
                            await ctx.send(f"No users found for {god_name}")
                    else:
                        query = '''
                            SELECT COUNT(*)
                            FROM profile
                            WHERE god = $1
                        '''
                        count = await conn.fetchval(query, god_name)
                        await ctx.send(f"{god_name} has {count} users")

        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")

    @is_gm()
    @commands.command(hidden=True)
    async def assign_roles(self, ctx):
        async with self.bot.pool.acquire() as conn:
            data = await conn.fetch("SELECT user FROM profile")

            role_id = 1146279043692503112

            for row in data:
                user_id = row['user']

                member = ctx.guild.get_member(user_id)
                role = ctx.guild.get_role(role_id)

                if member and role:
                    await member.add_roles(role)
                    await ctx.send(f"Assigned {role.name} role to {member.display_name}")

    @is_gm()
    @commands.command(hidden=True)
    async def fetch(self, ctx):
        async with self.bot.pool.acquire() as conn:
            async with conn.transaction():
                try:
                    rows = await conn.fetch('SELECT "user", discordtag FROM profile')
                    user_data = [(row['user'], row['discordtag']) for row in rows]

                    for i in range(0, len(user_data), 2):  # Fetch and update two users at a time
                        user_data_chunk = user_data[i:i + 2]  # Fetch two user data entries at a time

                        for user_id, current_tag in user_data_chunk:
                            try:
                                user = await self.bot.fetch_user(user_id)
                            except HTTPException as e:
                                await ctx.send(
                                    f"Rate limit exceeded. Waiting for retry... Retry after: {e.retry_after} seconds")
                                await asyncio.sleep(e.retry_after)  # Wait for the specified retry_after period
                                continue

                            username = user.name

                            if username == current_tag:
                                await ctx.send(f"No update needed for: {username} (ID: {user_id})")
                                continue

                            try:
                                result = await conn.execute('UPDATE profile SET discordtag = $1 WHERE "user" = $2',
                                                            username, user_id)
                                if result == "UPDATE 1":
                                    await ctx.send(f"Updated: {username} (ID: {user_id})")
                                else:
                                    await ctx.send(f"No rows updated for user ID: {user_id}")
                            except Exception as e:
                                await ctx.send(f"An error occurred during update: {e}")

                            await asyncio.sleep(1)  # Add a delay of 1 second between each update
                except Exception as e:
                    await ctx.send(f"An error occurred during transaction: {e}")

    @is_gm()
    @commands.command(hidden=True)
    async def evall(self, ctx: Context, *, code: str) -> None:
        """[Owner only] Evaluates python code on all processes."""

        data = await self.bot.cogs["Sharding"].handler(
            "evaluate", self.bot.shard_count, {"code": code}
        )
        filtered_data = {instance: data.count(instance) for instance in data}
        pretty_data = "".join(
            f"```py\n{count}x | {instance[6:]}"
            for instance, count in filtered_data.items()
        )
        if len(pretty_data) > 2000:
            pretty_data = pretty_data[:1997] + "..."
        await ctx.send(pretty_data)

    @is_god()
    @commands.command(hidden=True)
    async def assignroles(self, ctx):
        god_roles = {
            'Drakath': 1153880715419717672,
            'Sepulchure': 1153897989635571844,
            'Astraea': 1153887457775980566
        }

        try:
            async with self.bot.pool.acquire() as conn:
                query = '''
                    SELECT "user", god
                    FROM profile
                    WHERE god IS NOT NULL
                '''

                data = await conn.fetch(query)

                if data:
                    guild = ctx.guild
                    for row in data:
                        discord_user_id = int(row['user'])
                        god = row['god']

                        member = guild.get_member(discord_user_id)

                        if member:
                            if god in god_roles:
                                role_id = god_roles[god]
                                new_role = guild.get_role(role_id)

                                # Remove old god roles if they exist and don't match the new one
                                for god_name, god_role_id in god_roles.items():
                                    role = guild.get_role(god_role_id)
                                    if role in member.roles and role != new_role:
                                        await member.remove_roles(role)
                                        await ctx.send(
                                            f"Removed the role {role.name} from {member.display_name} (Profile ID: {discord_user_id}).")

                                # Assign the new god role if the member doesn't have it already
                                if new_role not in member.roles:
                                    try:
                                        await member.add_roles(new_role)
                                        await ctx.send(
                                            f"Assigned the role {new_role.name} to {member.display_name} (Profile ID: {discord_user_id}) for god {god}.")
                                    except discord.Forbidden:
                                        await ctx.send(
                                            f"Cannot assign the role {new_role.name} to {member.display_name} due to role hierarchy.")
                            else:
                                await ctx.send(
                                    f"Skipping {member.display_name} (Profile ID: {discord_user_id}) as their god '{god}' is not in the configured list.")
                    await ctx.send("Roles updated based on gods.")
                else:
                    await ctx.send("No data found in the profile table.")

        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")

    @is_god()
    @commands.command(hidden=True)
    async def assignrolesprimary(self, ctx):
        god_roles = {
            'Drakath': 1199302687083204649,
            'Sepulchure': 1199303145306726410,
            'Astraea': 1199303066227331163
        }

        try:
            async with self.bot.pool.acquire() as conn:
                query = '''
                        SELECT "user", god
                        FROM profile
                        WHERE god IS NOT NULL
                    '''

                data = await conn.fetch(query)

                if data:
                    guild = ctx.guild
                    for row in data:
                        discord_user_id = int(row['user'])
                        god = row['god']

                        member = guild.get_member(discord_user_id)

                        if member:
                            if god in god_roles:
                                role_id = god_roles[god]
                                new_role = guild.get_role(role_id)

                                # Remove old god roles if they exist and don't match the new one
                                for god_name, god_role_id in god_roles.items():
                                    role = guild.get_role(god_role_id)
                                    if role in member.roles and role != new_role:
                                        await member.remove_roles(role)
                                        await ctx.send(
                                            f"Removed the role {role.name} from {member.display_name} (Profile ID: {discord_user_id}).")

                                # Assign the new god role if the member doesn't have it already
                                if new_role not in member.roles:
                                    try:
                                        await member.add_roles(new_role)
                                        await ctx.send(
                                            f"Assigned the role {new_role.name} to {member.display_name} (Profile ID: {discord_user_id}) for god {god}.")
                                    except discord.Forbidden:
                                        await ctx.send(
                                            f"Cannot assign the role {new_role.name} to {member.display_name} due to role hierarchy.")
                            else:
                                await ctx.send(
                                    f"Skipping {member.display_name} (Profile ID: {discord_user_id}) as their god '{god}' is not in the configured list.")
                    await ctx.send("Roles updated based on gods.")
                else:
                    await ctx.send("No data found in the profile table.")

        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")

    @is_gm()
    @commands.command(hidden=True)
    async def bash(self, ctx: Context, *, command_to_run: str) -> None:
        """[Owner Only] Run shell commands."""
        await shell.run(command_to_run, ctx)

    @is_gm()
    @commands.command(hidden=True)
    async def killpalserver(self, ctx):
        process_name = 'PalServer-Linux-Test'
        await ctx.send("Killing Server..")
        try:
            # Find the process ID (PID) of the PalServer-Linux-Test process
            pid_command = f"pgrep -f {process_name}"
            pid_process = await asyncio.create_subprocess_shell(pid_command, stdout=asyncio.subprocess.PIPE,
                                                                stderr=asyncio.subprocess.PIPE)
            pid_result, _ = await pid_process.communicate()

            if pid_process.returncode == 0:
                # Process found, kill it
                pid = pid_result.decode().strip()
                kill_command = f"kill -9 {pid}"
                kill_process = await asyncio.create_subprocess_shell(kill_command, stdout=asyncio.subprocess.PIPE,
                                                                     stderr=asyncio.subprocess.PIPE)
                await kill_process.communicate()
                await ctx.send(f"Successfully killed the {process_name} process.")
            else:
                await ctx.send(f"{process_name} process not found.")

        except Exception as e:
            await ctx.send(f"An error occurred: {e}")

    async def run_palserver_async(self, ctx):
        script_path = '/home/lunar/palworld/PalServer.sh'

        try:
            process = await asyncio.create_subprocess_exec('sh', script_path, stdout=asyncio.subprocess.PIPE,
                                                           stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await process.communicate()

            # Check if there was an error
            if process.returncode != 0:
                await ctx.send(f"**Output:**\n```\n{stderr.decode()}\n```")
            else:
                await ctx.send(f"**Output:**\n```\nServer Starting...\n```")

        except Exception as e:
            await ctx.send(f"An error occurred: {e}")

    @is_gm()
    @commands.command(hidden=True)
    async def runpipeserver(self, ctx):
        await ctx.send(f"**Output:** IP KVM Started...")
        message = await ctx.send("Fetching Connection Data")
        num = random.randint(1, 4)
        for _ in range(num):
            await asyncio.sleep(1)  # Add a delay of 1 second between each cycle
            await message.edit(content="Connecting to pipeline server")
            await asyncio.sleep(0.5)  # Add a short delay before adding a dot
            await message.edit(content="Connecting to pipeline server.")
            await asyncio.sleep(0.5)
            await message.edit(content="Connecting to pipeline server..")
            await asyncio.sleep(0.5)
            await message.edit(content="Connecting to pipeline server...")

        error_message = """
        ```ERROR: CPU Fault Detected (Error Code: 00)

        Remote connection to the server failed due to a CPU fault.

        **Action Required:**
        Please contact your system administrator for assistance in diagnosing and resolving the issue.

        Error Code: 00```
        """

        await ctx.send(error_message)

    @is_gm()
    @commands.command(hidden=True)
    async def runpalserver(self, ctx):
        await ctx.send(f"**Output:** Server Sequence Started...")
        message = await ctx.send("Finding Connection Data")

        for _ in range(4):
            await asyncio.sleep(1)  # Add a delay of 1 second between each cycle
            await message.edit(content="Connecting to Remote Host")
            await asyncio.sleep(0.5)  # Add a short delay before adding a dot
            await message.edit(content="Connecting to Remote Host.")
            await asyncio.sleep(0.5)
            await message.edit(content="Connecting to Remote Host..")
            await asyncio.sleep(0.5)
            await message.edit(content="Connecting to Remote Host...")

        await ctx.send("Server online!")

        await self.run_palserver_async(ctx)

    @is_gm()
    @commands.command(hidden=True)
    async def runas(self, ctx, member_arg: str, *, command: str):
        gm_id = 295173706496475136  # GM's user ID
        og_author = ctx.author.mention
        allowed_channels = [1140210749868871772, 1149193023259951154, 1140211789573935164]

        # Check if the command is used by GM and in the allowed channels
        try:

            if command == str("eval"):
                return

            if command == str("evall"):
                return

            if member_arg == 295173706496475136:
                await ctx.send("You can't do this.")
                return

            try:
                member = await commands.MemberConverter().convert(ctx, member_arg)
            except commands.BadArgument:
                try:
                    member_id = int(member_arg)
                    member = await ctx.bot.fetch_user(member_id)
                except (ValueError, discord.NotFound):
                    await ctx.send("Member not found.")
                    return

            fake_msg = copy.copy(ctx.message)
            fake_msg._update(dict(channel=ctx.channel, content=ctx.clean_prefix + command))
            fake_msg.author = member

            new_ctx = await ctx.bot.get_context(fake_msg, cls=commands.Context)

            await ctx.bot.invoke(new_ctx)
            try:
                await ctx.message.delete()
            except Exception as e:

                return
        except Exception as e:
            await ctx.send(e)


    def replace_md(self, s):
        opening = True
        out = []
        for i in s:
            if i == "`":
                if opening is True:
                    opening = False
                    i = "<code>"
                else:
                    opening = True
                    i = "</code>"
            out.append(i)
        reg = re.compile(r'\[(.+)\]\(([^ ]+?)( "(.+)")?\)')
        text = "".join(out)
        text = re.sub(reg, r'<a href="\2">\1</a>', text)
        reg = re.compile(r"~~(.+)~~")
        text = re.sub(reg, r"<s>\1</s>", text)
        reg = re.compile(r"__(.+)__")
        text = re.sub(reg, r"<u>\1</u>", text)
        reg = re.compile(r"\*\*(.+)\*\*")
        text = re.sub(reg, r"<b>\1</b>", text)
        reg = re.compile(r"\*(.+)\*")
        text = re.sub(reg, r"<i>\1</i>", text)
        return text

    def make_signature(self, cmd):
        if cmd.aliases:
            prelude = cmd.qualified_name.replace(cmd.name, "").strip()
            if prelude:
                prelude = f"{prelude} "
            actual_names = cmd.aliases + [cmd.name]
            aliases = f"{prelude}[{'|'.join(actual_names)}]"
        else:
            aliases = cmd.qualified_name
        return f"${aliases} {cmd.signature}"

    def read_csv(self, filename):
        with open(filename, 'r', encoding='utf-8') as file:
            reader = csv.DictReader(file)
            data = [row for row in reader]
        return data

    # Function to process CSV data and calculate percentages
    def process_data(self, csv_data):
        questions = defaultdict(lambda: defaultdict(int))
        total_responses = defaultdict(int)

        # Count total responses and answer choices for each question
        for row in csv_data:
            for key, value in row.items():
                if key != 'Timestamp':  # Skip timestamp column
                    questions[key][value] += 1
                    total_responses[key] += 1

        # Calculate percentages for each answer choice
        for question, choices in questions.items():
            total_responses_for_question = total_responses[question]
            for choice, count in choices.items():
                questions[question][choice] = (count / total_responses_for_question) * 100

        return questions

    # Command to display processed CSV data
    @commands.command(hidden=True)
    async def view_results(self, ctx):
        # Read the CSV file
        try:
            csv_data = self.read_csv('results.csv')

            # Process the data
            processed_data = self.process_data(csv_data)

            # Format the data for display
            formatted_data = ""
            for question, choices in processed_data.items():
                formatted_data += f"**{question}**:\n"
                for choice, percentage in choices.items():
                    formatted_data += f"{choice}: {percentage:.2f}%\n"
                formatted_data += "\n"
            chunks = [formatted_data[i:i + 2000] for i in range(0, len(formatted_data), 2000)]

            # Send each chunk as a separate message
            for chunk in chunks:
                await ctx.send(chunk)
        except Exception as e:
            await ctx.send(e)

    @is_gm()
    @commands.command(hidden=True)
    async def makehtml(self, ctx: Context) -> None:
        """Generates HTML for commands page."""
        with open("assets/html/commands.html") as f:
            base = f.read()
        with open("assets/html/cog.html") as f:
            cog = f.read()
        with open("assets/html/command.html") as f:
            command = f.read()

        html = ""

        for cog_name, cog_ in self.bot.cogs.items():
            if cog_name in ("GameMaster", "Owner", "Custom"):
                continue
            commands = {c for c in list(cog_.walk_commands()) if not c.hidden}
            if len(commands) > 0:
                html += cog.format(name=cog_name)
                for cmd in commands:
                    html += command.format(
                        name=cmd.qualified_name,
                        usage=self.make_signature(cmd)
                        .replace("<", "&lt;")
                        .replace(">", "&gt;"),
                        checks=f"<b>Checks: {checks}</b>"
                        if (
                            checks := ", ".join(
                                [
                                    (
                                        "cooldown"
                                        if "cooldown" in name
                                        else (
                                            "has_character"
                                            if name == "has_char"
                                            else name
                                        )
                                    )
                                    for c in cmd.checks
                                    if (
                                           name := re.search(
                                               r"<function ([^.]+)\.", repr(c)
                                           ).group(1)
                                       )
                                       != "update_pet"
                                ]
                            )
                        )
                        else "",
                        description=self.replace_md(
                            (cmd.help or "No Description Set")
                            .format(prefix="$")
                            .replace("<", "&lt;")
                            .replace(">", "&gt;")
                        ).replace("\n", "<br>"),
                    )

        html = base.format(content=html)
        await ctx.send(
            file=discord.File(filename="commands.html", fp=io.StringIO(html))
        )

    # Replace 'Your Category Name' with the name of the category you want

    @is_gm()
    @commands.command(hidden=True)
    async def gmjail(self, ctx: Context, member: discord.Member):
        if ctx.guild.id != 969741725931298857:
            return
        try:
            # Get the category by name
            target_category = discord.utils.get(ctx.guild.categories, name=CATEGORY_NAME)
            if not target_category:
                await ctx.send(f"Category '{CATEGORY_NAME}' not found!")
                return

            # Get the 'jail' channel
            jail_channel = discord.utils.get(ctx.guild.text_channels, name='⟢jail〡🚔')
            if not jail_channel:
                await ctx.send("Jail channel not found!")
                return

            # Loop through all text channels within the target category
            for channel in target_category.text_channels:
                try:
                    # Check if the channel is in the blacklist
                    if channel.name not in CHANNEL_BLACKLIST:
                        # Deny the member's permission to read messages in the channel
                        await channel.set_permissions(member, read_messages=False)
                except discord.Forbidden:
                    await ctx.send(f"Permission denied in channel: {channel.name}")

            # Allow the member to read messages in the jail channel
            await jail_channel.set_permissions(member, read_messages=True)

            await ctx.send(f"{member.mention} has been jailed!")

        except Exception as e:
            await ctx.send(f"An error occurred: {e}")

    @is_gm()
    @commands.command()
    async def gmunjail(self, ctx: Context, member: discord.Member):
        if ctx.guild.id != 969741725931298857:
            return
        try:
            SPECIAL_USER_ID = 295173706496475136
            special_permissions = None

            # Check if the user has a special ID
            if member.id == SPECIAL_USER_ID:
                special_permissions = discord.PermissionOverwrite(manage_channels=True, read_messages=True,
                                                                  send_messages=True, manage_roles=True)

            # Get the category by name
            target_category = discord.utils.get(ctx.guild.categories, name=CATEGORY_NAME)
            if not target_category:
                await ctx.send(f"Category '{CATEGORY_NAME}' not found!")
                return

            # Get the 'jail' channel
            jail_channel = discord.utils.get(ctx.guild.text_channels, name='⟢jail〡🚔')
            if not jail_channel:
                await ctx.send("Jail channel not found!")
                return

            # Loop through all text channels within the target category
            for channel in target_category.text_channels:
                # Check if the channel is in the blacklist
                if channel.name not in CHANNEL_BLACKLIST:
                    if special_permissions:
                        # Give the special permissions to the special user
                        await channel.set_permissions(member, overwrite=special_permissions)
                    else:
                        # Restore the member's permission to read messages in the channel
                        await channel.set_permissions(member, overwrite=None)

            if special_permissions:
                # Grant the special user the special permissions in the jail channel
                await jail_channel.set_permissions(member, overwrite=special_permissions)
            else:
                # Deny the member's permission to read messages in the jail channel
                await jail_channel.set_permissions(member, read_messages=False)

            await ctx.send(f"{member.mention} has been released from jail!")

        except Exception as e:
            await ctx.send(f"An error occurred: {e}")

    @is_gm()
    @commands.group(hidden=True, invoke_without_command=True)
    async def badges(self, ctx: Context, user: UserWithCharacter) -> None:
        badges = Badge.from_db(ctx.user_data["badges"])

        if badges:
            await ctx.send(badges.to_pretty())
        else:
            await ctx.send("User has no badges")

    @is_gm()
    @badges.command(hidden=True, name="add")
    async def badges_add(
            self, ctx: Context, user: UserWithCharacter, badge: BadgeConverter
    ) -> None:
        badges = Badge.from_db(ctx.user_data["badges"])
        badges |= badge

        await self.bot.pool.execute(
            'UPDATE profile SET "badges"=$1 WHERE "user"=$2;', badges.to_db(), user.id
        )

        await ctx.send("Done")

    @is_gm()
    @badges.command(hidden=True, name="rem", aliases=["remove", "delete", "del"])
    async def badges_rem(
            self, ctx: Context, user: UserWithCharacter, badge: BadgeConverter
    ) -> None:
        badges = Badge.from_db(ctx.user_data["badges"])
        badges ^= badge

        await self.bot.pool.execute(
            'UPDATE profile SET "badges"=$1 WHERE "user"=$2;', badges.to_db(), user.id
        )

        await ctx.send("Done")

    @is_gm()
    @commands.command(name="viewtransactions")
    async def view_transactions(self, ctx, user_id1: discord.User, user_id2: discord.User = None,
                                start_date_str: str = None, end_date_str: str = None, page: int = 1):
        try:
            # Convert start and end date strings to datetime objects
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d") if start_date_str else None
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d") if end_date_str else None

            async with self.bot.pool.acquire() as connection:
                # Build the query based on the provided date range and user IDs
                query = """
                    SELECT * 
                    FROM transactions 
                    WHERE ("from" = $1 AND "to" = $2) OR ("from" = $2 AND "to" = $1)
                """

                # Add conditions for the date range if provided
                if start_date:
                    query += " AND timestamp >= $3"
                if end_date:
                    query += " AND timestamp <= $4"

                query += " ORDER BY timestamp DESC"

                # Execute the query
                if user_id2:
                    if start_date and end_date:
                        transactions = await connection.fetch(query, user_id1.id, user_id2.id, start_date, end_date)
                    elif start_date:
                        transactions = await connection.fetch(query, user_id1.id, user_id2.id, start_date)
                    elif end_date:
                        transactions = await connection.fetch(query, user_id1.id, user_id2.id, end_date)
                    else:
                        transactions = await connection.fetch(query, user_id1.id, user_id2.id)
                else:
                    # If user_id2 is not specified, fetch all transactions involving user_id1
                    all_transactions_query = """
                        SELECT * 
                        FROM transactions 
                        WHERE "from" = $1 OR "to" = $1
                        ORDER BY timestamp DESC
                    """
                    if start_date and end_date:
                        transactions = await connection.fetch(all_transactions_query, user_id1.id, start_date, end_date)
                    elif start_date:
                        transactions = await connection.fetch(all_transactions_query, user_id1.id, start_date)
                    elif end_date:
                        transactions = await connection.fetch(all_transactions_query, user_id1.id, end_date)
                    else:
                        transactions = await connection.fetch(all_transactions_query, user_id1.id)

            if not transactions:
                return await ctx.send("No transactions found.")

            paginator = menus.MenuPages(
                source=TransactionPaginator(transactions, per_page=5),
                clear_reactions_after=True,
                delete_message_after=True
            )

            await paginator.start(ctx)
        except Exception as e:
            await ctx.send(f"An error occurred: {e}")
            # Handle the exception here or re-raise it

    @commands.command(name="viewtransactions2")
    async def view_transactions_2(self, ctx, user_id1: discord.User, subject: str = "all",
                                  user_id2: discord.User = None,
                                  page: int = 1):
        valid_subjects = [
            "gambling BJ", "Pet Item Fetch", "Active Battle Bet", "guild invest", "Family Event",
            "daily", "Level Up!", "shop buy", "guild pay", "item", "Pet Purchase", "exchange", "item = OFFER",
            "vote", "crates", "shop buy - bot give", "Tournament Prize", "gambling BJ-Insurance",
            "Battle Bet", "spoil", "FamilyEvent Crate", "FamilyEvent Money", "RaidBattle Bet",
            "Raid Stats Upgrade DEF", "crate open item", "raid bid winner", "gambling roulette",
            "crates offercrate", "Starting out", "money", "class change", "give money", "gambling coinflip",
            "adventure", "Raid Stats Upgrade ATK", "AA Reward", "bid", "crates trade", "steal",
            "Raid Stats Upgrade HEALTH", "Torunament Winner", "buy boosters", "merch", "offer",
            "alliance", "sacrifice", "gambling", "Memorial Item", "shop"
        ]

        try:
            async with self.bot.pool.acquire() as connection:
                # Check if the provided subject is valid
                if subject.lower() != "all" and subject not in valid_subjects:
                    valid_subjects_str = "\n".join(valid_subjects)
                    return await ctx.send(
                        f"Invalid subject. Here is the list of valid subjects:\n\n```{valid_subjects_str}```")

                # Build the query based on the provided user IDs and subject
                query = """
                    SELECT * 
                    FROM transactions 
                    WHERE (("from" = $1 AND "to" = $2) OR ("from" = $2 AND "to" = $1))
                """

                # Add condition for the subject if provided and not "all"
                if subject.lower() != "all":
                    query += " AND subject = $3"

                query += " ORDER BY timestamp DESC"

                # Execute the query
                if user_id2:
                    transactions = await connection.fetch(query, user_id1.id, user_id2.id, subject)
                else:
                    # If user_id2 is not specified, fetch all transactions involving user_id1
                    all_transactions_query = """
                        SELECT * 
                        FROM transactions 
                        WHERE ("from" = $1 OR "to" = $1)
                    """

                    # Add condition for the subject if provided and not "all"
                    if subject.lower() != "all":
                        all_transactions_query += " AND subject = $2"

                    all_transactions_query += " ORDER BY timestamp DESC"

                    transactions = await connection.fetch(all_transactions_query, user_id1.id, subject)

            if not transactions:
                return await ctx.send("No transactions found.")

            paginator = menus.MenuPages(
                source=TransactionPaginator(transactions, per_page=5),
                clear_reactions_after=True,
                delete_message_after=True
            )

            await paginator.start(ctx)
        except Exception as e:
            await ctx.send(f"An error occurred: {e}")
            # Handle the exception here or re-raise it


from datetime import datetime

import re


class TransactionPaginator(menus.ListPageSource):
    def __init__(self, transactions, per_page=5, *args, **kwargs):
        super().__init__(transactions, per_page=per_page, *args, **kwargs)

    async def format_page(self, menu, entries):
        offset = (menu.current_page * self.per_page) + 1
        embed = discord.Embed(title="Transaction History", color=discord.Color.blurple())

        for transaction in entries:
            from_member = None
            to_member = None

            # Check if 'from' is a valid Discord ID
            if isinstance(transaction['from'], int):
                from_member = discord.utils.get(menu.bot.users, id=transaction['from'])

            # Check if 'to' is a valid Discord ID
            if isinstance(transaction['to'], int):
                to_member = discord.utils.get(menu.bot.users, id=transaction['to'])

            from_display = f"{from_member.name}#{from_member.discriminator}" if from_member else str(
                transaction['from'])
            to_display = f"{to_member.name}#{to_member.discriminator}" if to_member else str(transaction['to'])

            # Extract information from 'info' field
            info_display = transaction.get('info', '')
            user_id_matches = re.findall(r'\b(\d{17,21})\b', info_display)

            for user_id_match in user_id_matches:
                # If a potential Discord user ID is found, try to get the corresponding user
                user_id = int(user_id_match)
                user = discord.utils.get(menu.bot.users, id=user_id)
                info_display = info_display.replace(user_id_match,
                                                    f"{user.name}#{user.discriminator}" if user else user_id_match)

            formatted_timestamp = transaction.get('timestamp', '')  # Adjust the column name as per your database

            if isinstance(formatted_timestamp, datetime):
                formatted_timestamp = formatted_timestamp.strftime("%Y-%m-%d %H:%M:%S %Z")
            elif formatted_timestamp:
                formatted_timestamp = datetime.strptime(formatted_timestamp, "%Y-%m-%d %H:%M:%S.%f%z").strftime(
                    "%Y-%m-%d %H:%M:%S %Z"
                )

            embed.add_field(
                name=f"Transaction #{offset}",
                value=f"From: {from_display}\nTo: {to_display}\nSubject: {transaction['subject']}",
                inline=False
            )

            embed.add_field(
                name="Info",
                value=info_display,
                inline=False
            )

            if 'data' in transaction and transaction['data']:
                embed.add_field(
                    name="Data",
                    value=transaction['data'],
                    inline=False
                )

            embed.add_field(
                name="Timestamp",
                value=formatted_timestamp,
                inline=False
            )

            embed.add_field(name='\u200b', value='\u200b', inline=False)  # Add an empty field as a separator
            offset += 1

        embed.set_footer(text=f"Page {menu.current_page + 1}/{self.get_max_pages()}")
        return embed


async def setup(bot):
    await bot.add_cog(GameMaster(bot))
