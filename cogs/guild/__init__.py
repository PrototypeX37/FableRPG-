"""
The IdleRPG Discord Bot
Copyright (C) 2018-2021 Diniboy and Gelbpunkt
Copyright (C) 2023-2024 Lunar (PrototypeX37)
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
import asyncio

from contextlib import suppress
from datetime import timedelta, datetime
from typing import Union

import discord
from discord import Embed

from discord.enums import ButtonStyle
from discord.ext import commands
from discord.http import handle_message_parameters
from discord.ui.button import Button

from classes.converters import (
    ImageFormat,
    ImageUrl,
    IntGreaterThan,
    MemberWithCharacter,
    UserWithCharacter,
)
from cogs.shard_communication import guild_on_cooldown as guild_cooldown
from cogs.shard_communication import user_on_cooldown as user_cooldown
from utils import misc as rpgtools
from utils import random
from utils.checks import (
    has_char,
    has_guild,
    has_guild_,
    has_money,
    has_no_guild,
    is_guild_leader,
    is_guild_officer,
    is_no_guild_leader,
    user_is_patron,
    is_gm,
)
from utils.i18n import _, locale_doc
from utils.joins import JoinView
from utils.markdown import escape_markdown


class Guild(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @has_char()
    @commands.group(invoke_without_command=True, brief=_("Interact with your guild."))
    @locale_doc
    async def guild(self, ctx):
        _(
            """Interact with your guild. If no subcommand is given, this will show your guild.

            Guilds are groups of players, they have a guild bank where money can be kept safe from thieves and the guild's members can go on adventures to earn extra rewards.
            Players cannot join guilds by themselves, they must be invited by the guild leader or one of its officers."""
        )
        if not ctx.character_data["guild"]:
            return await ctx.send(_("You are not in a guild yet."))
        await self.get_guild_info(ctx, guild_id=ctx.character_data["guild"])

    async def get_guild_info(
        self, ctx: commands.Context, *, guild_id: int = None, name: str = None
    ):
        async with self.bot.pool.acquire() as conn:
            if name is not None:
                guild = await conn.fetchrow(
                    'SELECT * FROM guild WHERE "name"=$1;', name
                )
            elif guild_id is not None:
                guild = await conn.fetchrow(
                    'SELECT * FROM guild WHERE "id"=$1;', guild_id
                )
            else:
                raise ValueError("Either guild_id or name must be given")
            if not guild:
                return await ctx.send(_("No guild found."))

            membercount = await conn.fetchval(
                'SELECT count(*) FROM profile WHERE "guild"=$1;', guild["id"]
            )
        text = _("Members")
        embed = discord.Embed(title=guild["name"], description=guild["description"])
        embed.add_field(
            name=_("Current Member Count"),
            value=f"{membercount}/{guild['memberlimit']} {text}",
        )
        leader = await rpgtools.lookup(self.bot, guild["leader"])
        embed.add_field(name=_("Leader"), value=leader)
        embed.add_field(
            name="Guild Bank",
            value=f"**${guild['money']}** / **${guild['banklimit']}**",
        )
        url = await ImageUrl(ImageFormat.all_static).convert(
            ctx, guild["icon"], silent=True
        )
        if url:
            embed.set_thumbnail(url=str(url))
        embed.set_footer(text=_("Guild ID: {id}").format(id=guild["id"]))
        if guild["badge"]:
            embed.set_image(url=guild["badge"])
        await ctx.send(embed=embed)

    @guild.command(brief=_("Show a specific guild"))
    @locale_doc
    async def info(self, ctx, *, by: MemberWithCharacter | str):
        _(
            """`<by>` - The guild's name (format `guild:name`, i.e. `guild:Adrian's Refuge`), its ID (format `id:number`, i.e. `id:5003`), or a person in the guild.

            Show a specific guild's info. You can look up guilds by its name, its ID, or a player in that guild."""
        )
        kwargs = {}
        if isinstance(by, str):
            if by.lower().startswith("guild:"):
                kwargs.update(name=by[6:])
            elif by.lower().startswith("id:"):
                kwargs.update(guild_id=int(by[3:]))
            else:
                kwargs.update(name=by)
        else:
            guild_id = await self.bot.pool.fetchval(
                'SELECT guild FROM profile WHERE "user"=$1;', by.id
            )
            if not guild_id:
                return await ctx.send(
                    _("**{user}** does not have a guild.").format(user=by.name)
                )
            kwargs.update(guild_id=guild_id)
        await self.get_guild_info(ctx, **kwargs)

    @guild.command(brief=_("Show the best guilds by GvG wins"))
    @locale_doc
    async def ladder(self, ctx):
        _(
            """Shows the top 10 guilds ordered by Guild vs Guild wins.

            To get more GvG wins, the guild leader or its officers can use `{prefix}guild battle`."""
        )
        guilds = await self.bot.pool.fetch(
            "SELECT * FROM guild ORDER BY wins DESC LIMIT 10;"
        )
        result = ""
        for idx, guild in enumerate(guilds):
            leader = await rpgtools.lookup(self.bot, guild["leader"])
            text = _("a guild by {leader} with **{wins}** GvG Wins").format(
                leader=escape_markdown(leader), wins=guild["wins"]
            )
            result = f"{result}{idx + 1}. {guild['name']}, {text}\n"
        await ctx.send(
            embed=discord.Embed(
                title=_("The Best GvG Guilds"), description=result, colour=0xE7CA01
            )
        )

    @has_guild()
    @guild.command(brief=_("Show a list of your guild members."))
    @locale_doc
    async def members(self, ctx):
        _(
            """Show a list of your guild members. If a user's name cannot be found for whatever reason, their user ID is displayed.

            This command can take a minute to load, depending on the amount of members in your guild. Please be patient.

            Only players who are part of a guild can use this command."""
        )
        members = await self.bot.pool.fetch(
            'SELECT "user", "guildrank" FROM profile WHERE "guild"=$1;',
            ctx.character_data["guild"],
        )
        members_fmt = []
        for m in members:
            u = str(
                await self.bot.get_user_global(m["user"])
                or _("Unknown User (ID {id})").format(id=m["user"])
            )
            members_fmt.append(f"{escape_markdown(u)} ({m['guildrank']})")
        await self.bot.paginator.Paginator(
            entries=members_fmt, title=_("Your guild mates")
        ).paginate(ctx)

    @has_char()
    @is_guild_leader()
    @guild.command(brief=_("Change your guild's badge"))
    @locale_doc
    async def badge(self, ctx, number: IntGreaterThan(0)):
        _(
            """`<number>` - The number of the guild badge to use, ranging from 1 to the amount of available badges

            Change your guild's badge, it will display in `{prefix}guild info`.

            Only guild leaders can use this command."""
        )
        async with self.bot.pool.acquire() as conn:
            bgs, channel = await conn.fetchval(
                'SELECT (badges, channel) FROM guild WHERE "leader"=$1;', ctx.author.id
            )
            if not bgs:
                return await ctx.send(_("Your guild has no badges yet."))
            try:
                bg = bgs[number - 1]
            except IndexError:
                return await ctx.send(
                    _(
                        "The badge number {number} is not valid, your guild only has"
                        " {amount} available."
                    ).format(amount=len(bgs), number=number)
                )
            await conn.execute(
                'UPDATE guild SET badge=$1 WHERE "leader"=$2;', bg, ctx.author.id
            )
        if channel:
            with suppress(discord.Forbidden, discord.HTTPException):
                with handle_message_parameters(
                    content=f"**{ctx.author}** changed the guild badge."
                ) as params:
                    await self.bot.http.send_message(channel, params=params)
        await ctx.send(_("Badge updated!"))

    @has_char()
    @has_no_guild()
    @user_cooldown(600)
    @guild.command(brief=_("Create a guild"))
    @locale_doc
    async def create(self, ctx):
        _(
            """Create a guild for $10,000.

            Creating a guild has no level requirements, as long as you have $10,000, you can create a guild.
            To create a guild, you will need the following:
              - A name with 20 characters or less
              - An image URL with 60 characters or less to your guild's icon
              - $10,000

            Having trouble finding a short image URL? Check [this tutorial](https://wiki.idlerpg.xyz/index.php?title=Tutorial:_Short_Image_URLs)

            The bot will ask for these separately. When you enter the guild's name or URL, don't include `{prefix}`.

            Only players who are not already in a guild can use this command.
            (This command has a cooldown of 10 minuets.)"""
        )

        def mycheck(amsg):
            return amsg.author == ctx.author

        await ctx.send(
            _("Enter a name for your guild. Maximum length is 20 characters.")
        )
        try:
            name = await self.bot.wait_for("message", timeout=60, check=mycheck)
        except asyncio.TimeoutError:
            await self.bot.reset_cooldown(ctx)
            return await ctx.send(_("Cancelled guild creation."))
        name = name.content
        if len(name) > 20:
            return await ctx.send(_("Guild names musn't exceed 20 characters."))
        await ctx.send(
            _("Send a link to the guild's icon. Maximum length is 60 characters.")
        )
        try:
            urlmsg = await self.bot.wait_for("message", timeout=60, check=mycheck)
        except asyncio.TimeoutError:
            await self.bot.reset_cooldown(ctx)
            return await ctx.send(_("Cancelled guild creation."))
        url = urlmsg.content
        if (urllength := len(url)) == 0:
            if not urlmsg.attachments:
                #  no idea how this would happen but eh
                await self.bot.reset_cooldown(ctx)
                return await ctx.send(_("Cancelled guild creation."))
            file_url = await ImageUrl(ImageFormat.all_static).convert(
                ctx, urlmsg.attachments[0].url
            )
            await ctx.send(
                _("No image URL found in your message, using image attachment...")
            )
            icon_url = await self.bot.cogs["Miscellaneous"].get_imgur_url(file_url)
        elif urllength > 200:
            url = await ImageUrl(ImageFormat.all_static).convert(ctx, url)
            await ctx.send(_("Image URL too long, shortening..."))
            icon_url = await self.bot.cogs["Miscellaneous"].get_imgur_url(url)
        else:
            icon_url = await ImageUrl(ImageFormat.all_static).convert(ctx, url)
        if await user_is_patron(self.bot, ctx.author):
            memberlimit = 100
        else:
            memberlimit = 50

        if not await ctx.confirm(
            _("Are you sure? React to create a guild for **$10000**")
        ):
            return
        async with self.bot.pool.acquire() as conn:
            if not await has_money(self.bot, ctx.author.id, 10000, conn=conn):
                return await ctx.send(
                    _("A guild creation costs **$10000**, you are too poor.")
                )
            if await conn.fetchrow('SELECT * FROM guild WHERE "name"=$1;', name):
                return await ctx.send(_("The guild name is taken."))
            guild = await conn.fetchrow(
                "INSERT INTO guild (name, memberlimit, leader, icon) VALUES ($1, $2,"
                " $3, $4) RETURNING *;",
                name,
                memberlimit,
                ctx.author.id,
                icon_url,
            )
            await conn.execute(
                'UPDATE profile SET "guild"=$1, "guildrank"=$2, "money"="money"-$3'
                ' WHERE "user"=$4;',
                guild["id"],
                "Leader",
                10000,
                ctx.author.id,
            )
        await ctx.send(
            _(
                "Successfully added your guild **{name}** with a member limit of"
                " **{memberlimit}**.\n\nTip: You can use `{prefix}guild channel` in a"
                " server where you are the admin to set up the guild logging channel."
            ).format(name=name, memberlimit=memberlimit, prefix=ctx.clean_prefix)
        )

    @is_guild_leader()
    @guild.command(brief=_("Renames your guild"))
    @locale_doc
    async def rename(self, ctx, *, new_name: str):
        _(
            """`<new_name>` - The new name for the guild.

            This renames your guild to something else.

            The name may not exceed 20 characters.

            Only guild leaders can use this command."""
        )
        if len(new_name) > 20:
            return await ctx.send(_("Guild names musn't exceed 20 characters."))
        await self.bot.pool.execute(
            'UPDATE guild SET "name"=$1 WHERE "leader"=$2;', new_name, ctx.author.id
        )
        await ctx.send(
            _("Successfully renamed your guild to {new_name}").format(new_name=new_name)
        )

    @is_guild_leader()
    @guild.command(brief=_("Give your guild to someone else"))
    @locale_doc
    async def transfer(self, ctx, member: MemberWithCharacter):
        _(
            """`<member>` - A discord User with a character, must be a member of your guild.

            Transfer your guild to someone else. This person will be the new guild leader, while you will become a regular member.

            If the user you transfer the guild to is a patron, the guild's member limit will be set to 100, otherwise it will be set to 50.

            Only guild leaders can use this command."""
        )
        if (
            member == ctx.author
            or ctx.character_data["guild"] != ctx.user_data["guild"]
        ):
            return await ctx.send(_("Not a member of your guild."))
        if not await ctx.confirm(
            _("Are you sure to transfer guild ownership to {user}?").format(
                user=member.mention
            )
        ):
            return
        m = 100 if await user_is_patron(self.bot, member) else 50
        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                'UPDATE profile SET "guildrank"=$1 WHERE "user"=$2;',
                "Member",
                ctx.author.id,
            )
            await conn.execute(
                'UPDATE profile SET "guildrank"=$1 WHERE "user"=$2;',
                "Leader",
                member.id,
            )
            name, channel = await conn.fetchval(
                'UPDATE guild SET "leader"=$1, "banklimit"="upgrade"*250000,'
                ' "memberlimit"=$2 WHERE "id"=$3 RETURNING ("name", "channel");',
                member.id,
                m,
                ctx.character_data["guild"],
            )
        if channel:
            with suppress(discord.Forbidden, discord.HTTPException):
                with handle_message_parameters(
                    content=f"Ownership changed from **{ctx.author}** to **{member}**"
                ) as params:
                    await self.bot.http.send_message(channel, params=params)
        await ctx.send(_("{user} now leads {guild}.").format(user=member, guild=name))

    @is_guild_leader()
    @guild.command(brief=_("Promote a guild member to officer."))
    @locale_doc
    async def promote(self, ctx, member: MemberWithCharacter):
        _(
            """`<member>` - A discord User with a character, must be a member of your guild

            Promote a member of your guild to the officer rank. This allows them to use certain guild commands. Officers can:
              - Invite new members
              - Kick members from the guild (cannot kick officers)
              - Take money out of the guild bank
              - Distribute money from the guild bank
              - Start battles with other guilds
              - Start and finish guild adventures

            Officers cannot be kicked from your guild and must be demoted first.
            Only promote members you trust. You can demote officers using `{prefix}guild demote`.

            Only guild leaders can use this command."""
        )
        if member == ctx.author:
            return await ctx.send(_("Very funny..."))
        if ctx.character_data["guild"] != ctx.user_data["guild"]:
            return await ctx.send(_("Target is not a member of your guild."))
        if ctx.user_data["guildrank"] == "Officer":
            return await ctx.send(_("This user is already an officer of your guild."))
        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                'UPDATE profile SET "guildrank"=$1 WHERE "user"=$2;',
                "Officer",
                member.id,
            )
            channel = await conn.fetchval(
                'SELECT "channel" FROM guild WHERE "id"=$1;',
                ctx.character_data["guild"],
            )
        if channel:
            with suppress(discord.Forbidden, discord.HTTPException):
                with handle_message_parameters(
                    content=f"**{ctx.author}** promoted **{member}** to the rank of Officer."
                ) as params:
                    await self.bot.http.send_message(
                        channel,
                        params=params,
                    )
        await ctx.send(
            _("Done! {member} has been promoted to the rank of `Officer`.").format(
                member=member
            )
        )

    @is_guild_leader()
    @guild.command(brief=_("Demote a guild officer to member."))
    @locale_doc
    async def demote(self, ctx, member: UserWithCharacter):
        _(
            """`<member>` - A discord User with a character, must be an officer of your guild

            Demotes an officer of your guild to member rank. The user will lose their guild officer permissions immediately.

            Only guild leaders can use this command."""
        )
        if member == ctx.author:
            return await ctx.send(_("Very funny..."))
        if ctx.character_data["guild"] != ctx.user_data["guild"]:
            return await ctx.send(_("Target is not a member of your guild."))
        if ctx.user_data["guildrank"] != "Officer":
            return await ctx.send(_("This user can't be demoted any further."))
        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                'UPDATE profile SET "guildrank"=$1 WHERE "user"=$2;',
                "Member",
                member.id,
            )
            channel = await conn.fetchval(
                'SELECT "channel" FROM guild WHERE "id"=$1;',
                ctx.character_data["guild"],
            )
        if channel:
            with suppress(discord.Forbidden, discord.HTTPException):
                with handle_message_parameters(
                    content=f"**{ctx.author}** demoted **{member}** to the rank of Member."
                ) as params:
                    await self.bot.http.send_message(channel, params=params)
        await ctx.send(
            _("Done! {member} has been demoted to the rank of `Member`.").format(
                member=member
            )
        )

    @is_guild_officer()
    @guild.command(brief=_("Invite new members to your guild."))
    @locale_doc
    async def invite(self, ctx, newmember: MemberWithCharacter):
        _(
            """`<member>` - A discord user with a character who is not yet in a guild.

            Invites a new member to your guild.
            If your guild is in an alliance which owns a city, the new member will have its bonuses applied immediately.

            Only guild leaders and officers can use this command."""
        )
        if newmember == ctx.me:
            return await ctx.send(
                _("...me? I'm flattered, but I can't accept this invitation...")
            )
        if ctx.user_data["guild"]:
            return await ctx.send(_("That member already has a guild."))
        async with self.bot.pool.acquire() as conn:
            id_ = await conn.fetchval(
                'SELECT guild FROM profile WHERE "user"=$1;', ctx.author.id
            )
            membercount = await conn.fetchval(
                'SELECT COUNT(*) FROM profile WHERE "guild"=$1;', id_
            )
            limit, name, channel = await conn.fetchval(
                'SELECT (memberlimit, name, channel) FROM guild WHERE "id"=$1;', id_
            )
        if membercount >= limit:
            return await ctx.send(
                _("Your guild is already at the maximum member count.")
            )

        if not await ctx.confirm(
            _(
                "{newmember}, {author} invites you to join **{name}**. React to join"
                " the guild."
            ).format(newmember=newmember.mention, author=ctx.author.mention, name=name),
            user=newmember,
        ):
            return
        if await has_guild_(self.bot, newmember.id):
            return await ctx.send(_("That member already has a guild."))
        await self.bot.pool.execute(
            'UPDATE profile SET "guild"=$1 WHERE "user"=$2;', id_, newmember.id
        )
        if channel:
            with suppress(discord.Forbidden, discord.HTTPException):
                with handle_message_parameters(
                    content=f"**{ctx.author}** invited **{newmember}** to the guild"
                ) as params:
                    await self.bot.http.send_message(channel, params=params)
        await ctx.send(
            _("{newmember} is now a member of **{name}**. Welcome!").format(
                newmember=newmember.mention, name=name
            )
        )

    @has_guild()
    @is_no_guild_leader()
    @guild.command(brief=_("Leave your guild"))
    @locale_doc
    async def leave(self, ctx):
        _(
            """Leave your current guild

            If your guild was in an alliance which owned a city, you will have its bonuses removed immediately.

            Only players who are in a guild, beside guild leaders, can use this command."""
        )
        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                'UPDATE profile SET "guild"=$1, "guildrank"=$2 WHERE "user"=$3;',
                0,
                "Member",
                ctx.author.id,
            )
            channel = await conn.fetchval(
                'SELECT "channel" FROM guild WHERE "id"=$1;',
                ctx.character_data["guild"],
            )

        if channel:
            with suppress(discord.Forbidden, discord.HTTPException):
                with handle_message_parameters(
                    content=f"**{ctx.author}** left the guild."
                ) as params:
                    await self.bot.http.send_message(channel, params=params)
        await ctx.send(_("You left your guild."))

    @is_guild_officer()
    @guild.command(brief=_("Kick a member from your guild."))
    @locale_doc
    async def kick(self, ctx, member: MemberWithCharacter | int):
        _(
            """`<member>` - A discord User with a character, must be a member of your guild

            Kicks a member from your guild. Officers cannot be kicked.
            If your guild is in an alliance which owns a city, the member will have its bonuses removed immediately.

            If the member shares no server with you, you may use their [User ID](https://support.discord.com/hc/en-us/articles/206346498-Where-can-I-find-my-User-Server-Message-ID-) as the member p>

            Only guild leaders and officers can use this command."""
        )
        if not hasattr(ctx, "user_data"):
            ctx.user_data = await self.bot.pool.fetchrow(
                'SELECT * FROM profile WHERE "user"=$1;', member
            )
        else:
            member = member.id

        if ctx.user_data["guild"] != ctx.character_data["guild"]:
            return await ctx.send(_("Not your guild mate."))
        if ctx.user_data["guildrank"] != "Member":
            return await ctx.send(_("You can only kick members."))
        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                'UPDATE profile SET "guild"=0, "guildrank"=$1 WHERE "user"=$2;',
                "Member",
                member,
            )
            channel = await conn.fetchval(
                'SELECT channel FROM guild WHERE "id"=$1;', ctx.character_data["guild"]
            )
        if channel:
            with suppress(discord.Forbidden, discord.HTTPException):
                with handle_message_parameters(
                    content=f"**{ctx.author}** kicked user with ID **{member}**"
                ) as params:
                    await self.bot.http.send_message(channel, params=params)
        await ctx.send(_("The person has been kicked!"))

    @is_guild_leader()
    @guild.command(brief=_("Delete your guild"))
    @locale_doc
    async def delete(self, ctx):
        _(
            """Delete your guild.

            If you would just like to leave the guild, consider transferring it to someone, then leaving normally.

            If your guild was in an alliance which owned a city, all members will lose its bonuses immediately.

            Only guild leaders can use this command."""
        )
        try:
            async with self.bot.pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(
                        'UPDATE city SET "owner"=1 WHERE "owner"=$1;',
                        ctx.character_data["guild"],
                    )
                    channel = await conn.fetchval(
                        'DELETE FROM guild WHERE "leader"=$1 RETURNING "channel";',
                        ctx.author.id,
                    )
                    await conn.execute(
                        'UPDATE profile SET "guild"=$1, "guildrank"=$2 WHERE "guild"=$3;',
                        0,
                        "Member",
                        ctx.character_data["guild"],
                    )
            if channel:
                with suppress(discord.Forbidden, discord.HTTPException):
                    with handle_message_parameters(
                        content=f"Guild deleted by **{ctx.author}**"
                    ) as params:
                        await self.bot.http.send_message(channel, params=params)
            await ctx.send(_("Successfully deleted your guild."))
        except Exception as e:
            await ctx.send(e)

    @is_guild_leader()
    @guild.command(brief=_("Change your guild's icon"))
    @locale_doc
    async def icon(self, ctx, url: ImageUrl(ImageFormat.all_static) = ""):
        _(
            """`[url]` - The image URL to use as the icon

            Change your guild's icon. The URL cannot exceed 60 characters.
            ⚠ This can be seen by anyone, do not use NSFW/innapropriate images. GIFs are not supported.

            Having trouble finding short image URLs? Follow [this tutorial](https://wiki.idlerpg.xyz/index.php?title=Tutorial:_Short_Image_URLs) or just attach the image you want to use (png, jpg a>

            Only guild leaders can use this command."""
        )
        if (urllength := len(url)) == 0:
            if not ctx.message.attachments:
                current_icon = await self.bot.pool.fetchval(
                    'SELECT icon FROM guild WHERE "id"=$1;', ctx.character_data["guild"]
                )
                return await ctx.send(
                    _("Your current guild icon is: {url}").format(url=current_icon)
                )
            file_url = await ImageUrl(ImageFormat.all_static).convert(
                ctx, ctx.message.attachments[0].url
            )
            await ctx.send(
                _("No image URL found in your message, using image attachment...")
            )
            icon_url = await self.bot.cogs["Miscellaneous"].get_imgur_url(file_url)
        elif urllength > 200:
            await ctx.send(_("Image URL too long, shortening..."))
            icon_url = await self.bot.cogs["Miscellaneous"].get_imgur_url(url)
        else:
            icon_url = url
        channel = await self.bot.pool.fetchval(
            'UPDATE guild SET "icon"=$1 WHERE "id"=$2 RETURNING "channel";',
            icon_url,
            ctx.character_data["guild"],
        )
        if channel:
            with suppress(discord.Forbidden, discord.HTTPException):
                with handle_message_parameters(
                    content=f"**{ctx.author}** changed the guild icon"
                ) as params:
                    await self.bot.http.send_message(channel, params=params)
        await ctx.send(_("Successfully updated the guild icon."))

    @is_guild_leader()
    @guild.command(brief=_("Change your guild description."))
    @locale_doc
    async def description(self, ctx, *, text: str):
        _(
            """`<text>` - The text to use as the description. Cannot exceed 200 characters.

            Change the description of your guild.
            ⚠ This can be seen by everyone, do not use NSFW/inappropriate text.

            Only guild leaders can use this command."""
        )
        if len(text) > 200:
            return await ctx.send(_("The text may be up to 200 characters only."))
        channel = await self.bot.pool.fetchval(
            'UPDATE guild SET "description"=$1 WHERE "leader"=$2 RETURNING "channel";',
            text,
            ctx.author.id,
        )
        if channel:
            with suppress(discord.Forbidden, discord.HTTPException):
                with handle_message_parameters(
                    content=f"**{ctx.author}** changed the description"
                ) as params:
                    await self.bot.http.send_message(channel, params=params)
        await ctx.send(_("Updated!"))

    @commands.has_permissions(administrator=True)
    @is_guild_leader()
    @guild.command(brief=_("Set/update the guild update channel."))
    @locale_doc
    async def channel(self, ctx, channel: discord.TextChannel = None):
        _(
            """`[channel]` - The channel to send guild logs to, defaults to the channel the command is used in

            Set or update the guild update channel. Relevant guild events will be sent here.
            The channel the command is used in will become the guild log channel, `{prefix}guild channel #channel-name` will not work.

            The following will be logged:
              - Guild badge updated
              - Guild transferred
              - Guild promotions
              - Guild demotions
              - New member joins
              - Members leaving the guild
              - Member kicks
              - Guild deletion
              - Guild icon changes
              - Guild description changes
              - Money invests
              - Money payouts
              - Money distributions
              - Guild bank upgrades
              - Guild adventures (start and end)

            Only guild leaders can use this command."""
        )
        channel = channel or ctx.channel
        if not await ctx.confirm(
            _("{channel} will become the channel for all logs. Are you sure?").format(
                channel=channel.mention
            )
        ):
            return
        if not channel.permissions_for(ctx.me).send_messages:
            return await ctx.send(
                _(
                    "I cannot send messages there! This channel cannot be the guild log"
                    " channel."
                )
            )
        await self.bot.pool.execute(
            'UPDATE guild SET "channel"=$1 WHERE "leader"=$2;',
            channel.id,
            ctx.author.id,
        )
        await ctx.send(
            _("**Guild logs will go to {channel} ** ✅").format(channel=channel.mention)
        )

    @has_guild()
    @guild.command(brief=_("Show the richest guild members"))
    @locale_doc
    async def richest(self, ctx):
        _(
            """Displays the top 10 richest guild members of your guild.

            Only players in a guild can use this command."""
        )
        await ctx.typing()
        async with self.bot.pool.acquire() as conn:
            guild = await conn.fetchrow(
                'SELECT * FROM guild WHERE "id"=$1;', ctx.character_data["guild"]
            )
            players = await conn.fetch(
                'SELECT "user", "name", "money" from profile WHERE "guild"=$1 ORDER BY'
                ' "money" DESC LIMIT 10;',
                guild["id"],
            )
        result = ""
        for idx, profile in enumerate(players):
            charname = await rpgtools.lookup(self.bot, profile["user"])
            text = _("a character by {charname} with **${money}**").format(
                charname=escape_markdown(charname), money=profile["money"]
            )
            result = f"{result}{idx + 1}. {escape_markdown(profile['name'])}, {text}\n"
        await ctx.send(
            embed=discord.Embed(
                title=_("The Richest Players of {guild}").format(guild=guild["name"]),
                description=result,
                colour=0xE7CA01,
            )
        )

    @has_guild()
    @guild.command(
        aliases=["high", "top"], brief=_("Show the best guild members by XP")
    )
    @locale_doc
    async def best(self, ctx):
        _(
            """Displays the top 10 best guild members of your guild ordered by XP.

            Only players in a guild can use this command."""
        )
        await ctx.typing()
        async with self.bot.pool.acquire() as conn:
            guild = await conn.fetchrow(
                'SELECT * FROM guild WHERE "id"=$1;', ctx.character_data["guild"]
            )
            players = await conn.fetch(
                'SELECT "user", "name", "xp" FROM profile WHERE "guild"=$1 ORDER BY'
                ' "xp" DESC LIMIT 10;',
                guild["id"],
            )
        result = ""
        for idx, profile in enumerate(players):
            charname = await rpgtools.lookup(self.bot, profile[0])
            text = _(
                "{name}, a character by {charname} with Level **{level}** (**{xp}** XP)"
            ).format(
                charname=escape_markdown(charname),
                name=escape_markdown(profile["name"]),
                level=rpgtools.xptolevel(profile["xp"]),
                xp=profile["xp"],
            )
            result = f"{result}{idx + 1}. {text}\n"
        await ctx.send(
            embed=discord.Embed(
                title=_("The Best Players of {name}").format(name=guild["name"]),
                description=result,
                colour=0xE7CA01,
            )
        )

    @has_guild()
    @guild.command(brief=_("Add money to your guild bank"))
    @locale_doc
    async def invest(self, ctx, amount):
        _(
            """`<amount>` - A whole number greater than 0

            Invest money into your guild bank, keeping it safe from thieves.

            Only guild officers can take money out of the guild bank.

            The money in the guild bank can be used to upgrade the bank or upgrade buildings/build defenses in your alliance, if it owns a city."""
        )

        if amount == "all":
            amount = int(ctx.character_data["money"])
        else:
            try:
                amount = int(amount)
            except Exception as e:
                return await ctx.send("You used a malformed argument!")
        if amount < 1:
            await ctx.send("The supplied number must be greater than 0.")
            return

        if ctx.character_data["money"] < amount:
            return await ctx.send(_("You're too poor."))
        async with self.bot.pool.acquire() as conn:
            g = await conn.fetchrow(
                'SELECT * FROM guild WHERE "id"=$1;', ctx.character_data["guild"]
            )
            if g["banklimit"] < g["money"] + amount:
                return await ctx.send(_("The bank would be full."))
            profile_money = await conn.fetchval(
                'UPDATE profile SET "money"="money"-$1 WHERE "user"=$2 RETURNING'
                " money;",
                amount,
                ctx.author.id,
            )
            guild_money = await conn.fetchval(
                'UPDATE guild SET money=money+$1 WHERE "id"=$2 RETURNING money;',
                amount,
                g["id"],
            )
            await self.bot.log_transaction(
                ctx,
                from_=ctx.author,
                to=0,
                subject="guild invest",
                data={"Gold": amount, "guild": ctx.character_data["guild"]},
                conn=conn,
            )
        if g["channel"]:
            with suppress(discord.Forbidden, discord.HTTPException):
                with handle_message_parameters(
                    content=f"**{ctx.author}** invested **${amount}**"
                ) as params:
                    await self.bot.http.send_message(g["channel"], params=params)
        await ctx.send(
            _(
                "Done! Now you have `${profile_money}` and the guild has"
                " `${guild_money}`."
            ).format(profile_money=profile_money, guild_money=guild_money)
        )

    @is_guild_officer()
    @guild.command(brief=_("Take money out of the guild bank"))
    @locale_doc
    async def pay(self, ctx, amount, member: MemberWithCharacter):
        _(
            """`<amount>` - The amount of money to take out of the bank, must be greater than 0 and smaller or equal the amount your guild has
            `<member>` - A discord User with a character.

            Take money out of the guild bank and give it to a user. The user does not have to be a member of your guild.

            Only guild leaders and officers can use this command."""
        )

        async with self.bot.pool.acquire() as conn:
            guild = await conn.fetchrow(
                'SELECT * FROM guild WHERE "id"=$1;', ctx.character_data["guild"]
            )

        if amount == "all":
            amount = int(guild["money"])
        else:
            try:
                amount = int(amount)
            except Exception as e:
                return await ctx.send("You used a malformed argument!")
        if amount < 1:
            await ctx.send("The supplied number must be greater than 0.")
            return

        if member == ctx.me:
            return await ctx.send(
                _("For me? I'm flattered, but I can't accept this...")
            )
        async with self.bot.pool.acquire() as conn:
            guild = await conn.fetchrow(
                'SELECT * FROM guild WHERE "id"=$1;', ctx.character_data["guild"]
            )
            if guild["money"] < amount:
                return await ctx.send(_("Your guild is too poor."))
            await conn.execute(
                'UPDATE guild SET "money"="money"-$1 WHERE "id"=$2;',
                amount,
                guild["id"],
            )
            await conn.execute(
                'UPDATE profile SET "money"="money"+$1 WHERE "user"=$2;',
                amount,
                member.id,
            )
            await self.bot.log_transaction(
                ctx,
                from_=0,
                to=member,
                subject="guild pay",
                data={"Gold": amount},
                conn=conn,
            )
        if guild["channel"]:
            with suppress(discord.Forbidden, discord.HTTPException):
                with handle_message_parameters(
                    content=f"**{ctx.author}** paid **${amount}** to **{member}**"
                ) as params:
                    await self.bot.http.send_message(guild["channel"], params=params)
        await ctx.send(
            _(
                "Successfully gave **${amount}** from your guild bank to {member}."
            ).format(amount=amount, member=member.mention)
        )

    @is_guild_officer()
    @guild.command(
        aliases=["dis", "distrib"], brief=_("Pay out money to multiple members")
    )
    @locale_doc
    async def distribute(
        self, ctx, amount: IntGreaterThan(0), *members: MemberWithCharacter
    ):
        _(
            """`<amount>` - The amount of money to take out all together, must be greater than 0
            `<members...>` - The discord users to give money to, can be multiple, separated by space.

            Distribute some money to multiple members. This will divide by the amount of players before distributing.
            For example, distributing $500 to 5 members will give everyone of them $100.

            Members that are mentioned multiple times will receive multiple payouts.

            In case of a decimal result the bot will round down, i.e. $7 distributed to 3 members will give everyone $2.

            Only guild leaders and officers can use this command."""
        )
        members = list(members)
        if ctx.me in members:
            members.remove(ctx.me)
        if not members:
            return await ctx.send(_("You can't distribute money to nobody."))

        # int() rounds down as to not go over the money limit
        # we need to update the amount after rounding down too to avoid losing money
        for_each = int(amount / len(members))
        amount = for_each * len(members)

        members_dupes = {i: members.count(i) for i in members}
        amounts = {for_each * i: [] for i in members_dupes.values()}
        for member in members_dupes.items():
            amounts[for_each * member[1]].append(member[0].id)
        # a bit ugly, but we get a dict {amount: [list of players]}

        async with self.bot.pool.acquire() as conn:
            guild = await conn.fetchrow(
                'SELECT * FROM guild WHERE "id"=$1;', ctx.character_data["guild"]
            )
            if guild["money"] < amount:
                return await ctx.send(_("Your guild is too poor."))

            await conn.execute(
                'UPDATE guild SET "money"="money"-$1 WHERE "id"=$2;',
                amount,
                ctx.character_data["guild"],
            )
            await conn.executemany(
                'UPDATE profile SET "money"="money"+$1 WHERE "user"=ANY($2);',
                amounts.items(),
            )

        nice_members = rpgtools.nice_join([str(member) for member in members])
        if guild["channel"]:
            with suppress(discord.Forbidden, discord.HTTPException):
                with handle_message_parameters(
                    content=f"**{ctx.author}** paid **${amount}** (${for_each} each) to **{nice_members}**"
                ) as params:
                    await self.bot.http.send_message(guild["channel"], params=params)
        await ctx.send(
            _(
                "Distributed **${money}** (${small_money} for each) to {members}."
            ).format(money=amount, small_money=for_each, members=nice_members)
        )

    @is_guild_leader()
    @guild_cooldown(60)
    @guild.command(brief=_("Upgrade your guild bank"))
    @locale_doc
    async def upgrade(self, ctx):
        _(
            """Upgrade your guild's bank, adding space for $250,000 each time.

            Guilds can be upgraded 9 times which sets them to a maximum base of $2,500,000.

            The price to upgrade the guild bank is always half of the current bank limit.

            If your guild was previously boosted by `updateguild` (Silver=2×, Gold=5×),
            this upgrade will keep that multiplier. Only guild leaders can use this command.
            """
        )
        async with self.bot.pool.acquire() as conn:
            guild = await conn.fetchrow(
                'SELECT * FROM guild WHERE "id"=$1;', ctx.character_data["guild"]
            )

            current_limit = guild["banklimit"]  # e.g. 500000, 1000000, etc.
            current_upgrades = guild["upgrade"]  # how many 250k base upgrades
            guild_money = guild["money"]

            # If we've done 10 base upgrades, that's the normal max (2,500,000 base).
            # *But* you might allow patrons to exceed it via updateguild if you wish.
            if current_upgrades >= 10:
                return await ctx.send(
                    _("Your guild already reached the maximum base upgrade.")
                )

            # 1) Figure out the old "base limit" before this upgrade
            #    i.e. upgrade * 250,000. If we had 2 base upgrades => 2*250k=500k base
            old_base = current_upgrades * 250_000
            if old_base == 0:
                # If guild["upgrade"] == 0, then the old base limit is 0, so ratio = 1 by default
                # (meaning no multiplier)
                old_multiplier = 1
            else:
                # If the user previously used `updateguild`, current_limit might be base * 2 or base * 5
                # We'll detect that factor by integer division
                old_multiplier = current_limit // old_base

                # Ensure at least 1
                if old_multiplier < 1:
                    old_multiplier = 1

            # 2) Compute the NEW base limit for the next upgrade
            new_base = (current_upgrades + 1) * 250_000

            # 3) Our final new limit should keep the old multiplier if it existed
            new_final_limit = new_base * old_multiplier

            # 4) The cost is always half of the current limit (which may be boosted)
            cost = current_limit // 2
            if guild_money < cost:
                return await ctx.send(
                    _("Your guild only has **${money}**, but needs **${cost}** to upgrade.").format(
                        money=guild_money,
                        cost=cost
                    )
                )

           # Confirm with the user
            confirm_text = _(
                "Upgrading will increase your limit to **${new_final}**)"
                " at the cost of **${cost}**. Proceed?"
            ).format(new_base=new_base, new_final=new_final_limit, cost=cost)

            if not await ctx.confirm(confirm_text):
                return

            guild_data = await conn.fetchrow(
                'SELECT * FROM guild WHERE "id"=$1;',
                ctx.character_data["guild"]
            )
            if guild_data["money"] < cost:
                return await ctx.send(
                    _(
                        "Looks like the guild's money changed while you were confirming. "
                        "Your guild now has only **${money}** and cannot afford the upgrade."
                    ).format(money=guild_data["money"])
                )

            # 5) Deduct the cost, increment upgrade, and set new banklimit
            await conn.execute(
                """
                UPDATE guild
                SET "banklimit"=$1,
                    "money"="money"-$2,
                    "upgrade"="upgrade"+1
                WHERE "id"=$3;
                """,
                new_final_limit,
                cost,
                guild["id"],
            )

        # 6) Optionally announce in the guild channel
        channel_id = guild["channel"]
        if channel_id:
            from contextlib import suppress
            with suppress(discord.Forbidden, discord.HTTPException):
                msg = f"**{ctx.author}** upgraded the guild bank to **${new_final_limit}**."
                await self.bot.http.send_message(channel_id, content=msg)

        # 7) Show the final new limit to the user
        await ctx.send(
            _("Your new guild bank limit is now **${limit}**.").format(limit=new_final_limit)
        )

    @is_guild_officer()
    @guild_cooldown(1800)
    @guild.command(brief=_("Battle another guild"))
    @locale_doc
    async def battle(
        self,
        ctx,
        enemy: MemberWithCharacter,
        amount: IntGreaterThan(-1),
        fightercount: IntGreaterThan(1),
    ):
        _(
            """`<enemy>` - A guild officer or leader
            `<amount>` - The amount of money to battle for, must be 0 or above
            `<fightercount>` - The amount of fighters to take into the battle

            Fight against another guild, the winning guild will be awarded one GvG win.

            While the battle is preparing, both players, you and the other player, will be asked to nominate guild members for the battle.
            You can do this by writing `battle nominate @person` (not including `{prefix}`) until you hit the fightercount.

            After the preparation is over, battles will be randomly matched between the two guilds.
            These battles function exactly the same as regular battles, see `{prefix}help battle` for more details.

            Each fight will give the guild who the winner is from one point. The guild with the most points in the end will win the guild battle.
            In case of a tie, nobody gets the money or guild win. The money will be taken from the guild bank.

            Only guild leaders and officers can use this command.
            (This command has a guild cooldown of 30 minutes.)"""
        )
        if enemy == ctx.author:
            return await ctx.send(_("Poor kiddo having no friendos."))
        guild1 = ctx.character_data["guild"]
        guild2 = ctx.user_data["guild"]
        if guild1 == 0 or guild2 == 0:
            return await ctx.send(_("One of you both doesn't have a guild."))
        if guild1 == guild2:
            return await ctx.send(
                _("Battling your own guild? :face_with_raised_eyebrow:")
            )
        if (
            ctx.character_data["guildrank"] == "Member"
            or ctx.user_data["guildrank"] == "Member"
        ):
            return await ctx.send(_("One of you both isn't an officer of their guild."))
        async with self.bot.pool.acquire() as conn:
            guild1 = await conn.fetchrow('SELECT * FROM guild WHERE "id"=$1;', guild1)
            guild2 = await conn.fetchrow('SELECT * FROM guild WHERE "id"=$1;', guild2)
            if guild1["money"] < amount or guild2["money"] < amount:
                return await ctx.send(_("One of the guilds can't pay the price."))
            size1 = await conn.fetchval(
                'SELECT count("user") FROM profile WHERE "guild"=$1;', guild1["id"]
            )
            size2 = await conn.fetchval(
                'SELECT count("user") FROM profile WHERE "guild"=$1;', guild2["id"]
            )
        if size1 < fightercount or size2 < fightercount:
            return await ctx.send(_("One of the guilds is too small."))

        if not await ctx.confirm(
            f"{enemy.mention}, {ctx.author.mention} invites you to fight in a guild"
            " battle. React to join the battle. You got **1 Minute to accept**.",
            timeout=60,
            user=enemy,
        ):
            return await ctx.send(
                _("{enemy} didn't want to join your battle, {author}.").format(
                    enemy=enemy.mention, author=ctx.author.mention
                )
            )

        await ctx.send(
            _(
                "{enemy} accepted the challenge by {author}. Please now nominate"
                " members, {author}. Use `battle nominate @user` to add someone to your"
                " team."
            ).format(enemy=enemy.mention, author=ctx.author.mention)
        )
        team1 = []
        team2 = []
        converter = commands.UserConverter()

        async def guildcheck(already, guildid, user):
            try:
                member = await converter.convert(ctx, user)
            except commands.errors.BadArgument:
                return False
            guild = await self.bot.pool.fetchval(
                'SELECT guild FROM profile WHERE "user"=$1;', member.id
            )
            if guild != guildid:
                await ctx.send(_("That person isn't in your guild."))
                return False
            if member in already:
                return False
            return member

        def simple1(msg):
            return msg.author == ctx.author and msg.content.startswith(
                "battle nominate"
            )

        def simple2(msg):
            return msg.author == enemy and msg.content.startswith("battle nominate")

        while len(team1) != fightercount:
            try:
                res = await self.bot.wait_for("message", timeout=90, check=simple1)
                try:
                    guild1check = await guildcheck(
                        team1, guild1["id"], res.content.split()[-1]
                    )
                    if guild1check:
                        team1.append(guild1check)
                        await ctx.send(
                            _("{user} has been added to your team, {author}.").format(
                                user=guild1check, author=ctx.author.mention
                            )
                        )
                    else:
                        await ctx.send(_("User not found."))
                except AttributeError:
                    await ctx.send(_("Error when adding this user, please try again"))
                continue
            except asyncio.TimeoutError:
                await self.bot.reset_guild_cooldown(ctx)
                return await ctx.send(
                    _("Took to long to add members. Fight cancelled.")
                )
        await ctx.send(
            _(
                "Please now nominate members, {enemy}. Use `battle nominate @user` to"
                " add someone to your team."
            ).format(enemy=enemy.mention)
        )
        while len(team2) != fightercount:
            try:
                res = await self.bot.wait_for("message", timeout=90, check=simple2)
                guild2check = await guildcheck(
                    team2, guild2["id"], res.content.split()[-1]
                )
                if guild2check:
                    team2.append(guild2check)
                    await ctx.send(
                        _("{user} has been added to your team, {enemy}.").format(
                            user=guild2check, enemy=enemy.mention
                        )
                    )
                else:
                    await ctx.send(_("User not found."))
                    continue
            except asyncio.TimeoutError:
                await self.bot.reset_guild_cooldown(ctx)
                return await ctx.send(
                    _("Took to long to add members. Fight cancelled.")
                )

        await self.bot.public_log(
            f"Guild **{guild1['name']}** challenges Guild **{guild2['name']}** to a"
            f" battle for a prize of **${amount}**.\n **{fightercount}** players"
            " entered."
        )

        msg = await ctx.send(_("Fight started!\nGenerating battles..."))
        await asyncio.sleep(3)
        await msg.edit(content=_("Fight started!\nGenerating battles... Done."))
        wins1 = 0
        wins2 = 0
        for idx, user in enumerate(team1):
            user2 = team2[idx]
            msg = await ctx.send(
                _(
                    "Guild Battle Fight **{num}** of **{total}**.\n**{user}** vs"
                    " **{user2}**!\nBattle running..."
                ).format(num=idx + 1, total=len(team1), user=user, user2=user2)
            )
            val1 = sum(await self.bot.get_damage_armor_for(user)) + random.randint(1, 7)
            val2 = sum(await self.bot.get_damage_armor_for(user2)) + random.randint(
                1, 7
            )
            if val1 > val2:
                winner = user
                wins1 += 1
            elif val2 > val1:
                winner = user2
                wins2 += 1
            else:
                winner = random.choice([user, user2])
                if winner == user:
                    wins1 += 1
                else:
                    wins2 += 1
            await asyncio.sleep(5)
            await ctx.send(
                _(
                    "Winner of **{user}** vs **{user2}** is **{winner}**! Current"
                    " points: **{wins1}** to **{wins2}**."
                ).format(
                    user=user, user2=user2, winner=winner, wins1=wins1, wins2=wins2
                )
            )
        async with self.bot.pool.acquire() as conn:
            money1, bank1 = await conn.fetchval(
                'SELECT ("money", "banklimit") FROM guild WHERE "id"=$1;', guild1["id"]
            )
            money2, bank2 = await conn.fetchval(
                'SELECT ("money", "banklimit") FROM guild WHERE "id"=$1;', guild2["id"]
            )
            if money1 < amount or money2 < amount:
                return await ctx.send(_("Some guild spent the money??? Bad looser!"))
            if wins1 > wins2:
                if money1 + amount <= bank1:
                    await conn.execute(
                        'UPDATE guild SET "money"="money"+$1 WHERE "id"=$2;',
                        amount,
                        guild1["id"],
                    )
                else:
                    await conn.execute(
                        'UPDATE profile SET "money"="money"+$1 WHERE "user"=$2;',
                        amount,
                        ctx.author.id,
                    )
                await conn.execute(
                    'UPDATE guild SET "money"="money"-$1 WHERE "id"=$2;',
                    amount,
                    guild2["id"],
                )
                await conn.execute(
                    'UPDATE guild SET "wins"="wins"+1 WHERE "id"=$1;', guild1["id"]
                )
                await ctx.send(
                    _("{guild} won the battle! Congratulations!").format(
                        guild=guild1["name"]
                    )
                )
                await self.bot.public_log(
                    f"**{guild1['name']}** won against **{guild2['name']}**."
                )
            elif wins2 > wins1:
                if money2 + amount <= bank2:
                    await conn.execute(
                        'UPDATE guild SET "money"="money"+$1 WHERE "id"=$2;',
                        amount,
                        guild2["id"],
                    )
                else:
                    await conn.execute(
                        'UPDATE profile SET "money"="money"+$1 WHERE "user"=$2;',
                        amount,
                        enemy.id,
                    )
                await conn.execute(
                    'UPDATE guild SET "money"="money"-$1 WHERE "id"=$2;',
                    amount,
                    guild1["id"],
                )
                await conn.execute(
                    'UPDATE guild SET "wins"="wins"+1 WHERE "id"=$1;', guild2["id"]
                )
                await ctx.send(
                    _("{guild} won the battle! Congratulations!").format(
                        guild=guild2["name"]
                    )
                )
                await self.bot.public_log(
                    f"**{guild2['name']}** won against **{guild1['name']}**."
                )
            else:
                await ctx.send(_("It's a tie!"))
                await self.bot.public_log(
                    f"**{guild1['name']}** and **{guild2['name']}** tied."
                )

    @has_char()
    @is_gm()
    @guild.command()
    async def adventurereset(self, ctx, profile: Union[discord.Member, int] = None):
        try:
            # If no profile provided, use the command author
            if profile is None:
                profile_id = ctx.author.id
            elif isinstance(profile, discord.Member):
                profile_id = profile.id
            else:
                profile_id = profile
            
            # Query the database to get the guild for this profile
            async with self.bot.pool.acquire() as conn:
                guild_id = await conn.fetchval(
                    "SELECT guild FROM profile WHERE user = $1", 
                    profile_id
                )
            
            if guild_id is None:
                await ctx.send(f"No profile found for user ID {profile_id}.")
                return
            
            # Get all cooldown keys for this guild
            keys_to_delete = await self.bot.redis.keys(f"guildcd:{guild_id}:*")
            
            # Delete each matching key
            if keys_to_delete:
                await self.bot.redis.delete(*keys_to_delete)
                await ctx.send(f"All cooldown entries for guild ID {guild_id} have been deleted.")
            else:
                await ctx.send(f"No cooldown entries found for guild ID {guild_id}.")
                
        except Exception as e:
            await ctx.send(f"An error occurred: {e}")

    @is_guild_officer()
    @guild_cooldown(86400)
    @guild.command(brief=_("Start a guild adventure"))
    @locale_doc
    async def adventure(self, ctx, timer: int = 600):
        _(
            """Start a guild adventure. Guild adventures can happen alongside regular adventures.

            On guild adventures, you can gain additional gold for your guild bank.
            When using this command, the bot will send a link used to join the adventure. Each member of the guild can join, at least 3 are required.
            Ten minutes after the link was sent, the users who joined will be gathered.

            The guild adventure's difficulty will depend solely on the users' levels, their equipped items and race/class bonuses are not considered.
            The adventure's length depends on the difficulty, +1 difficulty means +30 minutes time.

            Only guild leaders and officers can use this command.
            (This command has a guild cooldown of 1 hour.)"""
        )
        try:
            if timer > 86400:
                return await ctx.send("Timer cannot exceed 1 day")
            # Check if the guild is already on an adventure
            if await self.bot.get_guild_adventure(ctx.character_data["guild"]):
                await self.bot.reset_guild_cooldown(ctx)
                return await ctx.send(
                    _(
                        "Your guild is already on an adventure! Use `{prefix}guild status`"
                        " to view how long it still lasts."
                    ).format(prefix=ctx.clean_prefix)
                )

            # Fetch guild information
            guild = await self.bot.pool.fetchrow(
                'SELECT * FROM guild WHERE "id"=$1;', ctx.character_data["guild"]
            )

            # Create a view for joining the adventure
            view = JoinView(
                Button(style=ButtonStyle.primary, label=_("Join the adventure!")),
                message=_("You joined the adventure."),
                timeout=timer,
            )

            # Convert seconds to hours, minutes, seconds
            hours, remainder = divmod(timer, 3600)
            minutes, seconds = divmod(remainder, 60)
            
            # Create human-readable time string
            time_parts = []
            if hours > 0:
                time_parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
            if minutes > 0 or hours > 0:  # Show minutes if there are any, or if we're showing hours
                time_parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
            if seconds > 0 and hours == 0:  # Only show seconds if we're not showing hours
                time_parts.append(f"{seconds} second{'s' if seconds != 1 else ''}")
                
            time_str = " ".join(time_parts)

            # Send the join message
            await ctx.send(
                _(
                    "{author} seeks a guild adventure for **{guild}**! Click the button to"
                    " join! Unlimited players can join in the next {time}. The minimum"
                    " of players required is 3."
                ).format(author=ctx.author.mention, guild=guild["name"], time=time_str),
                view=view,
            )

            # Calculate difficulty based on the command invoker's XP
            difficulty = int(rpgtools.xptolevel(ctx.character_data["xp"]))

            # Wait for 10 minutes to gather participants
            await asyncio.sleep(timer)

            # Stop the view to prevent further interactions
            view.stop()

            joined = []

            command_user = self.bot.get_user(ctx.author.id) or await self.bot.fetch_user(ctx.author.id)
            joined.append(command_user)

            async with self.bot.pool.acquire() as conn:
                for u in view.joined:
                    user = await conn.fetchrow(
                        'SELECT * FROM profile WHERE "user"=$1;', u.id
                    )
                    if user and user["guild"] == guild["id"]:
                        difficulty += int(rpgtools.xptolevel(user["xp"]))
                        joined.append(u)

            # Update the guild's advmembers with only valid members
            async with self.bot.pool.acquire() as conn:
                user_ids = [u.id for u in joined]
                await conn.execute(
                    'UPDATE guild SET advmembers=$1 WHERE "id"=$2;',
                    user_ids, guild["id"]
                )

            # Check if enough players joined
            if len(joined) < 3:
                await self.bot.reset_guild_cooldown(ctx)
                return await ctx.send(
                    _("You didn't get enough other players for the guild adventure.")
                )

            adventure_types = [
                {
                    'name': 'Slay the Drakon',
                    'description': 'Your guild marches under omen and torchlight to hunt a serpent-beast sent by bitter gods to scour the countryside.',
                    'events': [
                        'A pack of blood-eyed wolves, touched by Ares, rushes the road and is driven back.',
                        'A scout finds a bronze votive in a ruined shrine—still warm with old offerings.',
                        'Bandits claiming “tribute to a false king” ambush the rear, but your formation holds.',
                        'A wandering priest blesses your weapons with oil and ash from a sacred hearth.',
                        'The drakon coils from the mist, and the earth shakes as battle begins.',
                        'By the fire, veterans trade tales of heroes and warn against hubris.',
                        'You find a village scorched—stone melted like wax by monstrous breath.',
                        'A peddler sells bitter draughts and bandages, swearing by Hermes it’s “a fair price.”',
                        'A river blocks the path until an ancient turtle rises, bearing you across in silence.',
                        'Someone reads carved sigils foretelling: “Only the steady hand survives the final strike.”',
                        'A thunderstorm forces shelter in a cave painted with archaic hunts.',
                        'You free a captive noble who vows a reward—and a favor in the Assembly.',
                        'A bridge collapses; your engineers lash pine and rope into a passable span.',
                        'A rival band of would-be heroes appears, chasing the same glory.',
                        'An old hermit mutters: “Don’t kill it for pride. Kill it for balance.”',
                        'Tracks of scorched grass lead straight toward a lair that reeks of brimstone.',
                        'You navigate a forest like a labyrinth, where every path returns you to your own footprints.',
                        'Mischief-spirits weave illusions—laughter in the dark, footsteps where none walk.',
                        'One member’s courage steadies the line when fear threatens to break it.',
                        'You discover eggs hidden beneath stone—proof the beast fights to protect its brood.',
                    ],
                },
                {
                    'name': 'Vault of King Midas',
                    'description': 'Your guild seeks a sealed treasury said to be cursed by old gold and guarded by those who died clutching it.',
                    'events': [
                        'A sea squall batters your ship; supplies spill into the foam like spilled coin.',
                        'You recover a wax-sealed map marked with temple-stars and forbidden coves.',
                        'A leviathan shadows the hull until harpoons and hymn drive it off.',
                        'The vault is found—guarded by the restless dead, chained to their greed.',
                        'You return with relics and a warning: “Never count blessings too loudly.”',
                        'A talking raven follows you, repeating a single word: “Measure.”',
                        'A near-mutiny erupts, quenched by oath and threat of divine punishment.',
                        'Treacherous reefs force you to steer by torch-signal and prayer.',
                        'An island clan offers shelter—if you swear not to steal from their sacred grove.',
                        'Riddles carved into marble unlock a door that opens like a sigh.',
                        'A gilded idol brings misfortune until cast into the sea with a curse.',
                        'A rival crew races you under starlight, oars cutting water like blades.',
                        'A member falls overboard; a dolphin surfaces beneath them like a guide.',
                        'Merfolk demand tribute for passage: salt, silver, and a true name whispered.',
                        'An old sea chart reveals hazards that “move” when watched.',
                        'Ghost ships drift beside you at dawn, then vanish with the mist.',
                        'A volcanic rumble forces flight—ash falls like black snow.',
                        'A feast is held after victory; wine tastes sweeter when fear fades.',
                        'You patch torn sails and splintered mast with rope and stubbornness.',
                        'A white fog steals direction until you navigate by the constellations alone.',
                    ],
                },
                {
                    'name': 'Free the Oracle',
                    'description': 'Your guild is sworn to rescue an oracle taken by cultists and imprisoned within a fortress-temple of night.',
                    'events': [
                        'You slip into the sanctuary under moonlight, moving between columns like shadows.',
                        'Old traps bite at ankles and pride—your rogue disarms them with steady hands.',
                        'A guard nearly raises the alarm, but a thrown pebble and a whisper saves you.',
                        'You find a hidden stair descending behind a false fresco of smiling gods.',
                        'A chained seer gives a prophecy for a price: “Speak no names inside.”',
                        'A priest-sorcerer confronts you; the duel cracks mosaics and faith alike.',
                        'A riddle blocks your path—answered with truth, it opens like a mouth.',
                        'You steal robes and walk the halls as “devout,” heads bowed and blades hidden.',
                        'An ally within leaves chalk marks—an old hero’s sign.',
                        'You free the oracle and flee through catacombs that smell of myrrh and bones.',
                        'A betrayal stings—someone sells your route for silver.',
                        'Pursuers flood the tunnels; you scatter them with smoke and collapsing stone.',
                        'Shadow-creatures gather where torches fail; you push through with song and steel.',
                        'You seize documents exposing a conspiracy among nobles and priests.',
                        'A guardian beast blocks the exit; you outwit it with offerings and distance.',
                        'A stolen sky-chariot (or rope-lift) becomes your escape into the night wind.',
                        'An ancient charm grants a brief veil of invisibility—just long enough.',
                        'You set snares behind you: caltrops, oil, and collapsing beams.',
                        'A rooftop leap over a courtyard saves the group by a breath.',
                        'Back in the polis, you are hailed as heroes—and watched by jealous eyes.',
                    ],
                },
                {
                    'name': 'Quest for the Relic of Nike',
                    'description': 'Your guild follows divine signs into strange border-realms to recover a relic that grants victory—but tests worth.',
                    'events': [
                        'A shimmering threshold opens into an endless sky where islands drift like thoughts.',
                        'Gravity shifts; each step feels negotiated with the world.',
                        'Elemental daimones demand tribute before allowing safe passage.',
                        'A member receives prophetic flashes: outcomes branching like olive limbs.',
                        'A puzzle of rotating constellations alters reality around you.',
                        'You battle beings of pure radiance that strike like lightning in silence.',
                        'Time stutters; the same minute repeats until you act differently.',
                        'You find the relic—then face a choice between power and wisdom.',
                        'A guardian demands trials of courage, restraint, and mercy.',
                        'Illusions tempt you with glory: crowns, cheers, and bloodless triumph.',
                        'An astral storm threatens to scatter you across worlds like ash in wind.',
                        'You learn an ancient secret: victory without virtue is a debt.',
                        'A paradox forces you to face versions of yourselves who chose differently.',
                        'A blessing strengthens your resolve—your hearts beat as one.',
                        'Philosophical questions block the path; honest answers open it.',
                        'A being of Chaos smiles and says: “Prove you can lose.”',
                        'The realm fractures; you sprint for the threshold as it collapses.',
                        'You forge an alliance with star-born spirits who speak in chords.',
                        'You witness the birth of a star, and feel small—in a good way.',
                        'You return to find time moved oddly: a night for you, days for others.',
                    ],
                },
                {
                    'name': 'Katabasis: Ruins Below',
                    'description': 'Your guild descends beneath the city into archaic ruins, seeking lost knowledge and a name forgotten by mortals.',
                    'events': [
                        'Inscriptions guide you deeper—warnings disguised as prayers.',
                        'A cave-in forces a detour through narrow tunnels that breathe cold air.',
                        'Subterranean beasts attack—pale, blind, and furious at torchlight.',
                        'You discover a hidden library of clay tablets sealed in pitch.',
                        'Traps test wit and agility: swinging blades, collapsing floors, poisoned darts.',
                        'A buried civilization offers trade—salt, stories, and safe passage.',
                        'A cursed relic warps sound; whispers crawl into your thoughts.',
                        'An underground lake holds a slumbering leviathan—crossed in silence.',
                        'A centuries-old mystery resolves when a statue’s eyes finally align.',
                        'A maze confuses direction; chalk marks vanish as if licked clean.',
                        'Evidence of an advanced society appears—gears, lenses, and star-maps.',
                        'Magical darkness swallows flame; you navigate by touch and courage.',
                        'A ritual at a sealed door demands blood, incense, and humility.',
                        'A moral dilemma: keep forbidden knowledge, or seal it for the world’s safety.',
                        'An earthquake shakes the halls—dust falls like snow from the ceiling.',
                        'A vein of precious ore tempts greed; you take only what you need.',
                        'Shadowy figures pursue you—silent sandals on stone.',
                        'You find the resting place of a legendary hero, name scratched away.',
                        'Ancient guardians challenge your right to be there with impossible riddles.',
                        'You emerge with tablets, artifacts, and a wisdom that weighs in your hands.',
                    ],
                },
                {
                    'name': 'Defend the Polis',
                    'description': 'Your guild leads the defense of a city-state against an invading host—steel, omen, and strategy.',
                    'events': [
                        'You strengthen walls and gates, stacking stone and resolve.',
                        'A spy is captured and yields intelligence under oath to Hestia’s flame.',
                        'You train militia—farmers learning spear-work in a single night.',
                        'A speech lifts morale; fear becomes anger, anger becomes courage.',
                        'You repel the first assault; the enemy learns your name.',
                        'You sabotage siege engines with oil, rope, and daring.',
                        'A duel between champions turns the tide of a skirmish.',
                        'A temporary ceasefire is negotiated to retrieve the wounded.',
                        'A traitor is uncovered among your ranks—shame burns hotter than fire.',
                        'Reinforcements arrive at dawn like a blessing made flesh.',
                        'You outmaneuver the host with false retreats and hidden trenches.',
                        'A night raid wrecks the enemy’s supplies and confidence.',
                        'You escort civilians to safety through backstreets and secret doors.',
                        'A mystical barrier rises briefly—bought with prayer and sacrifice.',
                        'You capture the commander; the army falters without its head.',
                        'You intercept messages—plans scribbled in haste and arrogance.',
                        'Weather turns against the invaders: mud, wind, and broken banners.',
                        'You uncover a deeper plot—this war is a distraction for theft elsewhere.',
                        'Victory is won; songs are written before the blood dries.',
                        'A treaty is sealed—fragile as pottery, but real for now.',
                    ],
                },
                {
                    'name': 'The Grove of Artemis',
                    'description': 'Your guild enters a blighted sacred grove to lift a curse and restore the balance of beast and bow.',
                    'events': [
                        'Unnatural fog clings to your ankles, swallowing sound.',
                        'Whispers in the leaves test sanity—promises, threats, old regrets.',
                        'A witch-priestess offers help, but her price is truth.',
                        'An enchanted clearing grants respite; wounds close faster here.',
                        'Corrupted wildlife attacks—deer with black eyes, boars too bold.',
                        'You break a curse binding a trapped nymph to a stone.',
                        'A hidden spring heals you, tasting of moonlight.',
                        'A puzzle of living trees blocks the path; the forest rearranges itself.',
                        'You confront the source: a relic nailed to an altar like a wound.',
                        'A cleansing ritual begins; smoke rises in shapes of running stags.',
                        'Illusions lure you off-trail; you resist by holding hands and names.',
                        'Rare herbs are gathered—useful for healing, dangerous in greed.',
                        'An ancient altar hums with dark power; you choose what to destroy.',
                        'A member is briefly possessed; you bring them back with song and fire.',
                        'You learn the grove once sheltered a village—now only roots remember.',
                        'Forest guardians appear—silent hunters who judge your intent.',
                        'You set protective wards; the night feels less hungry.',
                        'The curse is tied to a stolen relic; returning it shifts the air.',
                        'A moral choice: punish the thief, or restore them with mercy.',
                        'As the curse breaks, birds return—first one note, then a choir.',
                    ],
                },
                {
                    'name': 'Voyage of the Winged Ship',
                    'description': 'Your guild boards a blessed sky-vessel to chart floating isles and storm-temples above the world.',
                    'events': [
                        'Sky raiders swing aboard on ropes; you cut them loose into clouds.',
                        'A mechanical failure forces frantic repairs mid-flight.',
                        'A floating island holds ruins etched with star-math and prayers.',
                        'A flock of hostile sky-beasts attacks, shrieking like bronze.',
                        'You rescue travelers stranded on a cloudbank that’s slowly dissolving.',
                        'A storm of raw magic spins the ship; compasses lie.',
                        'A celebration on deck restores morale—laughter fights the thin air.',
                        'You find a lost city above the clouds, silent and pristine.',
                        'Sky nomads trade wind-silk and bottled thunder for coin and stories.',
                        'You avoid a colossal flying beast by cutting engines and holding breath.',
                        'A temple defies gravity; steps lead sideways, then up into nowhere.',
                        'Time distorts at altitude; you lose an hour and gain a scar.',
                        'You collect rare airborne flora that glows like blue fire.',
                        'A captain’s log reveals betrayal, love, and a final warning.',
                        'A sky-kraken rises from the storm; you survive by inches.',
                        'You harness wind-currents to outrun danger.',
                        'A rival ship is in distress; you choose whether to help or pass.',
                        'You map uncharted routes—new lines drawn across the heavens.',
                        'You establish a skyport for future expeditions, marked by a torch.',
                        'You return with treasures and tales that sound like lies—until proven.',
                    ],
                },
                {
                    'name': 'Games of Ares',
                    'description': 'Your guild enters the grand games to earn glory, coin, and the favor of watching gods.',
                    'events': [
                        'Archery contests begin; arrows hum like bees in summer.',
                        'A grand melee erupts; shields clash in a storm of bronze.',
                        'A timed puzzle tests wits under pressure and jeering crowds.',
                        'You form alliances—temporary, useful, and dangerous.',
                        'A sabotage attempt is uncovered; honor demands response.',
                        'A moral trial appears: win unfairly, or lose cleanly.',
                        'Magical duels crack the arena stones with controlled lightning.',
                        'The crowd roars at exceptional skill; fame tastes like iron.',
                        'An obstacle course punishes arrogance and rewards teamwork.',
                        'Bribes are offered to throw a match—gold whispering like snakes.',
                        'A feast with dignitaries reveals politics sharper than spears.',
                        'You uncover a plot to rig outcomes; the judges pretend not to see.',
                        'A noble patron offers support… for a price later.',
                        'A masked competitor challenges you—silent, skilled, unsettling.',
                        'A priest offers “enhancements” that feel like curses in perfume.',
                        'Trials demand teamwork; lone heroes fail loudly.',
                        'A storytelling contest wins hearts—sometimes more useful than victory.',
                        'You win the games, and the gods feel suddenly very close.',
                        'You share prize with the poor; goodwill becomes a shield.',
                        'An elite order invites you—glory’s door opens, and so does its trap.',
                    ],
                },
                {
                    'name': 'Caravan of Helios',
                    'description': 'Your guild escorts a sun-baked caravan across a perilous desert where mirages speak and sand devours roads.',
                    'events': [
                        'Raiders strike at dusk; you form a shieldwall around the wagons.',
                        'A sandstorm erases the horizon; the world becomes a bowl of dust.',
                        'You find an oasis and ration water like it’s sacred wine.',
                        'Desert nomads offer guidance—if you respect their laws.',
                        'You uncover ruins buried in sand—columns like broken teeth.',
                        'A mythical sandworm moves beneath you; the ground ripples alive.',
                        'A dispute among merchants threatens to split the caravan.',
                        'Heat tests endurance; you learn the value of shade and patience.',
                        'Nocturnal predators circle the camp, eyes like embers.',
                        'A hidden cache of treasure is found—temptation dressed as luck.',
                        'Maps fail; stars become your only honest guide.',
                        'Around the fire, tales are traded like currency.',
                        'Water runs low; you avert panic with discipline and trust.',
                        'You guide a lost traveler back—earning a blessing and a rumor.',
                        'A moral choice: who drinks first when there isn’t enough?',
                        'A mirage nearly leads you to doom; you snap out of it together.',
                        'Ancient writing tells of a fallen city swallowed by pride and sand.',
                        'You reach your destination; relief feels like a second life.',
                        'The caravan leader rewards you generously—coin and connections.',
                        'New trade routes are forged; the desert remembers your names.',
                    ],
                },
                {
                    'name': 'Odyssey of Poseidon',
                    'description': 'Your guild sails beyond known waters to chart hidden isles where monsters, nymphs, and old gods still linger.',
                    'events': [
                        'You discover an island of gentle giants who trade stonecraft for song.',
                        'A siren’s hymn lures you toward jagged rocks; you plug ears and press on.',
                        'A message in a bottle points toward treasure—and danger.',
                        'You help a stranded sea-creature return home; it leaves a pearl as thanks.',
                        'A maze of whirlpools forces you to choose paths like a riddle.',
                        'A ghost ship sails beside you, offering warnings in dead languages.',
                        'A floating market appears at dawn; it’s gone by noon.',
                        'A stowaway is found; their information is valuable and suspicious.',
                        'A rare celestial event crowns the sea in silver light.',
                        'A rival crew challenges you to a race—pride with oars.',
                        'You rescue sailors from a wreck; gratitude buys future help.',
                        'A water spirit tests your worth: “Do you take, or do you tend?”',
                        'An underwater cave yields pearls, but something watches from within.',
                        'Instruments fail; you navigate by stars and instinct.',
                        'A dolphin guides you through reefs like a friendly omen.',
                        'Pirates attack; you defend what’s yours with clean fury.',
                        'You sail through bioluminescent waters—stars beneath your keel.',
                        'A massive sea turtle surfaces and offers slow, stubborn wisdom.',
                        'You calm a raging storm with an artifact that smells of old temples.',
                        'You find an island that appears only once a century—and you were on time.',
                    ],
                },
            ]


            # Select a random adventure type
            adventure_type = random.choice(adventure_types)

            # Calculate adventure time based on difficulty
            time = timedelta(hours=difficulty * 0.05)

            # Format time for display - handle days properly
            def format_timedelta(td):
                total_seconds = int(td.total_seconds())
                days = total_seconds // 86400
                hours = (total_seconds % 86400) // 3600
                minutes = (total_seconds % 3600) // 60
                seconds = total_seconds % 60
                
                parts = []
                if days > 0:
                    parts.append(f"{days}d")
                if hours > 0 or days > 0:  # Show hours if there are any, or if showing days
                    parts.append(f"{hours}h")
                if minutes > 0 or hours > 0 or days > 0:  # Show minutes if there are any, or if showing hours/days
                    parts.append(f"{minutes}m")
                if seconds > 0 and days == 0:  # Only show seconds if not showing days
                    parts.append(f"{seconds}s")
                
                return " ".join(parts) if parts else "0s"

            formatted_time = format_timedelta(time)

            # Start the guild adventure with the selected adventure type
            await self.bot.start_guild_adventure(guild["id"], difficulty, time, adventure_type)

            # Update the guild's money and fetch the channel ID
            gold = 1000  # Define how gold is calculated or fetched
            channel_id = await self.bot.pool.fetchval(
                'UPDATE guild SET "money"="money"+$1 WHERE "id"=$2 RETURNING "channel";',
                gold,
                ctx.character_data["guild"],
            )
            print(f"Fetched channel ID: {channel_id} (Type: {type(channel_id)})")

            # Create the embed for adventure start
            embed = Embed(
                title=f"Guild Adventure Started for **{guild['name']}**!",
                description=f"**Adventure:** {adventure_type['name']}\n\n{adventure_type['description']}",
                color=discord.Color.blue()
            )
            embed.add_field(
                name="Participants",
                value=", ".join([u.mention for u in joined]),
                inline=False
            )
            embed.add_field(
                name="Difficulty",
                value=f"**{difficulty}**",
                inline=True
            )
            embed.add_field(
                name="Estimated Time",
                value=f"**{formatted_time}**",
                inline=True
            )
            embed.set_footer(text="Good luck, adventurers!")
            embed.timestamp = discord.utils.utcnow()  # Adds the current timestamp

            # Send the embed to the guild's channel
            if channel_id:
                try:
                    # Ensure channel_id is an integer
                    if isinstance(channel_id, str) and channel_id.isdigit():
                        channel_id = int(channel_id)
                    elif isinstance(channel_id, int):
                        pass
                    else:
                        print("Unexpected channel ID type or format.")
                        channel_id = None

                    if channel_id:
                        guild_channel = self.bot.get_channel(channel_id)
                        if guild_channel:
                            with suppress(discord.Forbidden, discord.HTTPException):
                                await guild_channel.send(embed=embed)
                        else:
                            print(f"Guild channel with ID {channel_id} not found.")
                except TypeError as e:
                    print(f"Error converting channel ID to int: {e}")
            else:
                print("No channel ID found in the database.")

            # Send the embed to the command invoker
            await ctx.send(embed=embed)
        except Exception as e:
            import traceback
            error_message = f"Error occurred: {e}\n"
            error_message += traceback.format_exc()
            await ctx.send(error_message)
            print(error_message)

    @has_guild()
    @guild.command(brief=_("View your guild adventure's status"))
    @locale_doc
    async def status(self, ctx):
        _(
            """Check your guild adventure's status

            This will either display the time left or the reward. The reward can range from 20 times the difficulty up to 50 times the difficulty.
            Only guild leaders and officers can finish adventures, the status can be seen by every guild member."""
        )
        try:
            adventure = await self.bot.get_guild_adventure(ctx.character_data["guild"])

            if not adventure:
                return await ctx.send(
                    _(
                        "Your guild isn't on an adventure yet. Ask your guild officer to"
                        " use `{prefix}guild adventure` to start one"
                    ).format(prefix=ctx.clean_prefix)
                )

            difficulty, remain_time, is_completed, adventure_type = adventure

            if is_completed:
                if ctx.character_data["guildrank"] in ["Leader", "Officer"]:
                    # Remove the adventure from the database
                    await self.bot.delete_guild_adventure(ctx.character_data["guild"])

                    # Generate the adventure summary
                    events = random.sample(
                        adventure_type['events'], k=min(5, len(adventure_type['events']))
                    )
                    event_text = "\n".join(f"- {event}" for event in events)

                    # Calculate the reward
                    gold = random.randint(difficulty * 200, difficulty * 500)

                    # Update the guild's money and fetch the channel ID
                    channel_id = await self.bot.pool.fetchval(
                        'UPDATE guild SET "money"="money"+$1 WHERE "id"=$2 RETURNING "channel";',
                        gold,
                        ctx.character_data["guild"],
                    )
                    print(f"Fetched channel ID: {channel_id} (Type: {type(channel_id)})")

                    # Create the embed for adventure completion
                    embed = Embed(
                        title=_("Guild Adventure Completed: {adventure_name}").format(
                            adventure_name=adventure_type['name']
                        ),
                        description=adventure_type['description'],
                        color=discord.Color.gold()
                    )
                    embed.add_field(
                        name=_("Adventure Summary"),
                        value=event_text,
                        inline=False
                    )
                    embed.add_field(
                        name=_("Reward"),
                        value=_("${gold} has been added to the guild bank.").format(gold=gold),
                        inline=False
                    )
                    embed.set_footer(
                        text=_("Completed by {user}").format(user=str(ctx.author)),
                        icon_url=ctx.author.avatar.url if ctx.author.avatar else None
                    )

                    # Update user XP and collect XP gains
                    async with self.bot.pool.acquire() as conn:
                        # Fetch the advmembers array for the specified guild
                        guild_data = await conn.fetchrow(
                            'SELECT advmembers FROM guild WHERE id=$1;', ctx.character_data["guild"]
                        )

                        if guild_data and guild_data["advmembers"]:
                            advmembers = guild_data["advmembers"]  # This is the list of user IDs

                            # Fetch xp for each user ID in advmembers
                            user_xp_data = await conn.fetch(
                                'SELECT "user", xp FROM profile WHERE "user" = ANY($1::BIGINT[])',
                                advmembers
                            )
                            xp_summary = []


                            randomid = random.choice(advmembers)
                            completed = True
                            self.bot.dispatch("raid_completion", ctx, completed, randomid)

                            # Loop through each user and add a random XP value
                            for record in user_xp_data:
                                user_id = record["user"]
                                current_xp = record["xp"]
                                current_level = rpgtools.xptolevel(current_xp)

                                # Calculate new XP (only once)
                                new_xp = round(
                                    random.randint(int(250 * current_level / 2), int(500 * current_level / 2))
                                )

                                # Update the user's XP in the profile table
                                await conn.execute(
                                    'UPDATE profile SET xp = xp + $1 WHERE "user" = $2;',
                                    new_xp, user_id
                                )

                                # Calculate the new level after adding XP
                                updated_xp = current_xp + new_xp
                                new_level = rpgtools.xptolevel(updated_xp)

                                # Check for level up
                                if new_level > current_level:
                                    await self.bot.process_guildlevelup(ctx, user_id, new_level, current_level)

                                # Add to XP summary
                                xp_summary.append(f"🎖️ <@{user_id}>: **{new_xp} XP**")

                                print(f"User ID: {user_id}, Added XP: {new_xp}")

                    # Send the embed to the guild's channel
                    if channel_id:
                        try:
                            # Ensure channel_id is an integer
                            if isinstance(channel_id, str) and channel_id.isdigit():
                                channel_id = int(channel_id)
                            elif isinstance(channel_id, int):
                                pass
                            else:
                                print("Unexpected channel ID type or format.")
                                channel_id = None

                            if channel_id:
                                guild_channel = self.bot.get_channel(channel_id)
                                if guild_channel:
                                    with suppress(discord.Forbidden, discord.HTTPException):
                                        await guild_channel.send(embed=embed)
                                else:
                                    print(f"Guild channel with ID {channel_id} not found.")
                        except TypeError as e:
                            print(f"Error converting channel ID to int: {e}")
                    else:
                        print("No channel ID found in the database.")

                    # Send the embed to the command invoker
                    await ctx.send(embed=embed)

                    # Create a dedicated XP reward embed
                    xp_embed = Embed(
                        title="🎉 Guild Adventure XP Rewards 🎉",
                        description="Congratulations to the guild members who participated in the adventure! Here are the XP rewards:",
                        color=discord.Color.green()
                    )

                    # Create a dedicated XP reward embed
                    xp_embed = Embed(
                        title="🎉 Guild Adventure XP Rewards 🎉",
                        description="Congratulations to the guild members who participated in the adventure! Here are the XP rewards:",
                        color=discord.Color.green()
                    )



                    # Ensure each field has 1024 or fewer characters
                    if xp_summary:
                        # Join the XP summary into a single string
                        xp_text = "\n".join(xp_summary)
                        
                        # Split into chunks to ensure no field exceeds 900 characters (safe limit)
                        chunk_size = 900
                        chunks = []
                        current_chunk = ""
                        
                        for line in xp_summary:
                            # If adding this line would exceed the limit, start a new chunk
                            # This ensures we don't break in the middle of a user's XP entry
                            if len(current_chunk) + len(line) + 1 > chunk_size:
                                if current_chunk:
                                    chunks.append(current_chunk.strip())
                                current_chunk = line
                            else:
                                current_chunk += "\n" + line if current_chunk else line
                        
                        # Add the last chunk if it exists
                        if current_chunk:
                            chunks.append(current_chunk.strip())
                        
                        # Add fields for each chunk
                        for i, chunk in enumerate(chunks, 1):
                            xp_embed.add_field(
                                name=f"XP Gains (Part {i})",
                                value=chunk,
                                inline=False
                            )
                    else:
                        xp_embed.add_field(
                            name="XP Gains",
                            value="No XP gains to display.",
                            inline=False
                        )


                    # Set footer with adventure completion message
                    xp_embed.set_footer(text="Adventure completed! 🏆")

                    # Send the XP embed to the guild's channel if available
                    if channel_id:
                        try:
                            # Ensure channel_id is an integer
                            if isinstance(channel_id, str) and channel_id.isdigit():
                                channel_id = int(channel_id)
                            elif isinstance(channel_id, int):
                                pass
                            else:
                                print("Unexpected channel ID type or format.")
                                channel_id = None

                            if channel_id:
                                guild_channel = self.bot.get_channel(channel_id)
                                if guild_channel:
                                    with suppress(discord.Forbidden, discord.HTTPException):
                                        await guild_channel.send(embed=xp_embed)

                                else:
                                    print(f"Guild channel with ID {channel_id} not found.")
                        except TypeError as e:
                            print(f"Error converting channel ID to int: {e}")
                    else:
                        print("No channel ID found in the database.")

                    # Also send the XP embed to the command invoker
                    await ctx.send(embed=xp_embed)

                else:
                    await ctx.send(
                        _(
                            "Your guild has completed an adventure: **{adventure_name}**.\n"
                            "Ask a guild officer to collect the reward."
                        ).format(adventure_name=adventure_type['name'])
                    )
                    


            else:
                # Format time for display - handle timedelta properly
                def format_timedelta_display(td):
                    total_seconds = int(td.total_seconds())
                    days = total_seconds // 86400
                    hours = (total_seconds % 86400) // 3600
                    minutes = (total_seconds % 3600) // 60
                    seconds = total_seconds % 60
                    
                    parts = []
                    if days > 0:
                        parts.append(f"{days}d")
                    if hours > 0 or days > 0:  # Show hours if there are any, or if showing days
                        parts.append(f"{hours}h")
                    if minutes > 0 or hours > 0 or days > 0:  # Show minutes if there are any, or if showing hours/days
                        parts.append(f"{minutes}m")
                    if seconds > 0 and days == 0:  # Only show seconds if not showing days
                        parts.append(f"{seconds}s")
                    
                    return " ".join(parts) if parts else "0s"

                formatted_remain = format_timedelta_display(remain_time)
                
                await ctx.send(
                    _(
                        "Your guild is currently on an adventure: **{adventure_name}**.\n"
                        "Time remaining: `{remain}`"
                    ).format(
                        adventure_name=adventure_type['name'],
                        remain=formatted_remain,
                    )
                )
        except Exception as e:
            import traceback
            error_message = f"Error occurred: {e}\n"
            error_message += traceback.format_exc()

            await ctx.send(error_message)

    @has_guild()
    @guild.command(
        aliases=["cooldowns", "t", "cds"], brief=_("Lists guild-specific cooldowns")
    )
    @locale_doc
    async def timers(self, ctx):
        _(
            """Lists guild-specific cooldowns, meaning all guild members have these cooldowns and cannot use the commands."""
        )
        cooldowns = await self.bot.redis.execute_command(
            "KEYS", f"guildcd:{ctx.character_data['guild']}:*"
        )
        adv = await self.bot.get_guild_adventure(ctx.character_data["guild"])
        if not cooldowns and (not adv or adv[2]):
            return await ctx.send(
                _("You don't have any active cooldown at the moment.")
            )
        timers = _("Commands on cooldown:")
        for key in cooldowns:
            key = key.decode()
            cooldown = await self.bot.redis.execute_command("TTL", key)
            cmd = key.replace(f"guildcd:{ctx.character_data['guild']}:", "")
            text = _("{cmd} is on cooldown and will be available after {time}").format(
                cmd=cmd, time=timedelta(seconds=int(cooldown))
            )
            timers = f"{timers}\n{text}"
        if adv and not adv[2]:
            # Format the time to make it more readable
            remain_time = adv[1]
            
            # Format timedelta properly
            def format_timedelta_display(td):
                total_seconds = int(td.total_seconds())
                days = total_seconds // 86400
                hours = (total_seconds % 86400) // 3600
                minutes = (total_seconds % 3600) // 60
                seconds = total_seconds % 60
                
                parts = []
                if days > 0:
                    parts.append(f"{days}d")
                if hours > 0 or days > 0:  # Show hours if there are any, or if showing days
                    parts.append(f"{hours}h")
                if minutes > 0 or hours > 0 or days > 0:  # Show minutes if there are any, or if showing hours/days
                    parts.append(f"{minutes}m")
                if seconds > 0 and days == 0:  # Only show seconds if not showing days
                    parts.append(f"{seconds}s")
                
                return " ".join(parts) if parts else "0s"
            
            formatted_time = format_timedelta_display(remain_time)
            
            text = _("Guild adventure is running and will be done after {time}").format(
                time=formatted_time
            )
            timers = f"{timers}\n{text}"
        await ctx.send(f"```{timers}```")


async def setup(bot):
    await bot.add_cog(Guild(bot))

