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
        if not 0 <= stat <= 201:
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
