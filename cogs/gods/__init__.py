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
from decimal import Decimal
from io import BytesIO

import discord

from discord.ext import commands

from classes.classes import Ritualist, from_string
from classes.converters import IntGreaterThan
from classes.errors import NoChoice
from cogs.shard_communication import next_day_cooldown
from cogs.shard_communication import user_on_cooldown as user_cooldown
from utils import random
from utils.checks import has_char, has_god, has_no_god
from utils.i18n import _, locale_doc
from utils.loot import (
    LootSelectionView,
    acquire_loot_locks,
    delete_user_loot,
    fetch_user_loot,
    loot_value,
    release_loot_locks,
    reserve_loot_action_cooldown,
    reset_loot_action_cooldown,
    unique_loot_ids,
)


class Gods(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bot.gods = {god["user"]: god for god in self.bot.config.gods}

    async def perform_sacrifice_loot(
        self,
        ctx,
        selected_loot_ids: list[int],
        requested_count: int | None = None,
        reserve_cooldown: bool = False,
    ) -> None:
        if reserve_cooldown:
            retry_after = await reserve_loot_action_cooldown(self.bot, ctx.author.id)
            if retry_after:
                return await ctx.send(
                    _(
                        "You are already using loot. Try again in {seconds} seconds."
                    ).format(seconds=int(retry_after))
                )

        selected_loot_ids = unique_loot_ids(selected_loot_ids)
        requested_count = requested_count or len(selected_loot_ids)

        async with self.bot.pool.acquire() as conn:
            available_loot = await fetch_user_loot(conn, ctx.author.id, selected_loot_ids)

        if not available_loot:
            await reset_loot_action_cooldown(self.bot, ctx)
            return await ctx.send(
                _("Those loot item(s) were already used by another action.")
            )

        selected_loot_ids = [int(item["id"]) for item in available_loot]
        locks, blocked = await acquire_loot_locks(
            self.bot, ctx.author.id, selected_loot_ids, "sacrifice"
        )
        if blocked:
            await reset_loot_action_cooldown(self.bot, ctx)
            return await ctx.send(
                _(
                    "Loot item(s) `{loot_ids}` are already being used. Finish or"
                    " cancel the other loot action first."
                ).format(loot_ids=", ".join([str(loot_id) for loot_id in blocked]))
            )

        try:
            async with self.bot.pool.acquire() as conn:
                async with conn.transaction():
                    deleted_loot = await delete_user_loot(
                        conn, ctx.author.id, selected_loot_ids
                    )
                    if not deleted_loot:
                        await reset_loot_action_cooldown(self.bot, ctx)
                        return await ctx.send(
                            _("Those loot item(s) were already used by another action.")
                        )

                    count = len(deleted_loot)
                    value = loot_value(deleted_loot)
                    for class_ in ctx.character_data["class"]:
                        c = from_string(class_)
                        if c and c.in_class_line(Ritualist):
                            value = round(value * Decimal(1 + 0.05 * c.class_grade()))

                    value = int(value)
                    await conn.execute(
                        'UPDATE profile SET "favor"="favor"+$1 WHERE "user"=$2;',
                        value,
                        ctx.author.id,
                    )

                    await self.bot.log_transaction(
                        ctx,
                        from_=ctx.author.id,
                        to=2,
                        subject="sacrifice",
                        data={"Item-Count": count, "Amount": value},
                        conn=conn,
                    )
        finally:
            await release_loot_locks(self.bot, locks)

        skipped = requested_count - count
        additional = _(
            " Skipped `{amount}` because they did not belong to you or were already"
            " used."
        ).format(amount=skipped)
        await ctx.send(
            _(
                "You prayed to {god}, and they accepted your {count} sacrificed loot"
                " item(s). Your standing with the god has increased by **{points}**"
                " points."
            ).format(god=ctx.character_data["god"], count=count, points=value)
            + (additional if skipped else "")
        )

    @has_god()
    @has_char()
    @user_cooldown(180, identifier="sacrificeexchange")
    @commands.command(brief=_("Sacrifice loot for favor"))
    @locale_doc
    async def sacrifice(self, ctx, *loot_ids: int):
        _(
            """`[loot_ids...]` - The loot IDs to sacrifice, can be one or multiple IDs separated by space; defaults to all loot

            Sacrifice loot to your God to gain favor points.

            If no loot IDs are given with this command, all loot you own will be sacrificed.
            You can see your current loot with `{prefix}loot`.

            Only players, who follow a God can use this command."""
        )
        none_given = len(loot_ids) == 0
        requested_loot_ids = unique_loot_ids(loot_ids)

        async with self.bot.pool.acquire() as conn:
            available_loot = await fetch_user_loot(
                conn, ctx.author.id, None if none_given else requested_loot_ids
            )

        if not available_loot:
            await reset_loot_action_cooldown(self.bot, ctx)
            if none_given:
                return await ctx.send(_("You don't have any loot."))
            return await ctx.send(
                _("You don't own any loot items with the IDs: {itemids}").format(
                    itemids=", ".join([str(loot_id) for loot_id in requested_loot_ids])
                )
            )

        if none_given:
            try:
                selected_loot_ids = await LootSelectionView(
                    ctx,
                    available_loot,
                    title=_("Select loot to sacrifice"),
                    placeholder=_("Choose loot to sacrifice"),
                ).prompt()
            except NoChoice:
                await reset_loot_action_cooldown(self.bot, ctx)
                return await ctx.send(_("You didn't choose anything."))

            if not selected_loot_ids:
                await reset_loot_action_cooldown(self.bot, ctx)
                return await ctx.send(_("Cancelled."))
        else:
            selected_loot_ids = [int(item["id"]) for item in available_loot]

        requested_count = (
            len(selected_loot_ids) if none_given else len(requested_loot_ids)
        )
        await self.perform_sacrifice_loot(
            ctx, selected_loot_ids, requested_count=requested_count
        )

    @has_char()
    @user_cooldown(1209600)  # to prevent double invoke
    @commands.command(brief=_("Choose or change your God"))
    @locale_doc
    async def follow(self, ctx):
        _(
            """Choose a God or change your current God for a reset point.
            Every player gets 2 reset points when they start playing, you cannot get any more.

            Following a God allows your `{prefix}luck` to fluctuate, check `{prefix}help luck` to see the exact effects this will have on your gameplay.
            If you don't have any reset points left, or became Godless, you cannot follow another God.

            (This command has a cooldown of 3 minutes.)"""
        )
        god_roles = {
            'Hecate': 1415388751064076318,
            'Thanatos': 1415388580171219094,
            'Apollo': 1415388833037418677,
            'Morpheus': 1415389046137422014
        }

        # Check if the user already has a god and handle reset points
        if not has_no_god(ctx):
            try:
                old_god = ctx.character_data["god"]
                old_role_id = god_roles.get(old_god)
                if old_role_id:
                    old_role = ctx.guild.get_role(old_role_id)
                    if old_role and old_role in ctx.author.roles:
                        await ctx.author.remove_roles(old_role)
            except Exception as e:
                print("Not in server")

        if ctx.character_data["reset_points"] < 0:
            return await ctx.send(_("You became Godless. You will not be able to follow a god anymore."))

        # Show gods selection to the user
        embeds = [
            discord.Embed(
                title=god["name"],
                description=god["description"],
                color=self.bot.config.game.primary_colour,
            )
            for god in self.bot.gods.values()
        ]
        god = await self.bot.paginator.ChoosePaginator(
            extras=embeds,
            placeholder=_("Choose a god"),
            choices=[g["name"] for g in self.bot.gods.values()],
        ).paginate(ctx)

        # Confirm the user's choice of God
        if not await ctx.confirm(
                _("""\
    ⚠ **Warning**: When you have a God, your luck will change (**including decreasing it!**)
    This impacts your adventure success chances amongst other things.
    Are you sure you want to follow {god}?""").format(god=god)
        ):
            return

        # Update the user's god and reset points in the database
        async with self.bot.pool.acquire() as conn:
            if (
                    await conn.fetchval(
                        'SELECT reset_points FROM profile WHERE "user"=$1;', ctx.author.id
                    )
                    < 0
            ):
                return await ctx.send(
                    _("You became Godless while using this command. Following a God is not allowed after that.")
                )


            await conn.execute(
                'UPDATE profile SET "god"=$1 WHERE "user"=$2;', god, ctx.author.id
            )

        # Get the target guild and check if the user is a member
        guild_id = 1323388333589528638
        target_guild = self.bot.get_guild(guild_id)

        if target_guild:
            member = target_guild.get_member(ctx.author.id)
            if member:
                # Assign the god's role in the target guild if the user is a member
                role_id = god_roles.get(god)
                if role_id:
                    role = target_guild.get_role(role_id)
                    if role:
                        await member.add_roles(role)
                        await ctx.send(
                            _("You are now a follower of {god}. Role assigned in the target guild.").format(god=god))
                    else:
                        await ctx.send(_("Failed to assign role in the target guild."))
                else:
                    await ctx.send(_("Failed to assign role."))
            else:
                await ctx.send(
                    _("You have successfully followed {god}, but you are not a member of the target guild.").format(
                        god=god))
        else:
            await ctx.send(
                _("You have successfully followed {god}, but the target guild could not be found.").format(god=god))

    @has_char()
    @has_god()
    @commands.command(brief=_("Unfollow your God and become Godless"))
    @locale_doc
    async def unfollow(self, ctx):
        _(
            """Unfollow your current God and become Godless. **This is permanent!**

            Looking to change your God instead? Simply use `{prefix}follow` again.

            Once you become Godless, all your reset points and your God are removed.
            Becoming Godless does not mean that your luck returns to 1.00 immediately, it changes along with everyone else's luck on Monday."""
        )
        if ctx.character_data["reset_points"] < 0:
            # this shouldn't happen in normal play, but you never know
            return await ctx.send(_("You already became Godless before."))

        old_god = ctx.character_data["god"]
        god_roles = {
            'Hecate': 1415388751064076318,
            'Thanatos': 1415388580171219094,
            'Apollo': 1415388833037418677,
            'Morpheus': 1415389046137422014
        }


        if not await ctx.confirm(
                _(
                    """\
        ⚠ **Warning**: After unfollowing your God, **you cannot follow any God anymore** and will remain Godless.
        If your luck is below average and you decided to unfollow, know that **your luck will not return to 1.0 immediately**.
    
        Are you sure you want to become Godless?"""
                )
        ):
            return await ctx.send(
                _("{god} smiles proudly down upon you.").format(
                    god=ctx.character_data["god"]
                )
            )

        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                'UPDATE profile SET "favor"=0, "god"=NULL, "reset_points"="reset_points"-1 WHERE'
                ' "user"=$1;',
                ctx.author.id,
            )

        old_role_id = god_roles.get(old_god)
        if old_role_id:
            old_role = ctx.guild.get_role(old_role_id)
            if old_role and old_role in ctx.author.roles:
                await ctx.author.remove_roles(old_role)

        await ctx.send(_("You are now Godless."))

    @has_god()
    @has_char()
    @next_day_cooldown()
    @commands.command(brief=_("Pray to your God to gain favor"))
    @locale_doc
    async def pray(self, ctx):
        _(
            # xgettext: no-python-format
            """Pray to your God in order to gain a random amont of favor points, ranging from 0 to 1000.

            There is a 33% chance you will gain 0 favor, a 33% chance to gain anywhere from 0 to 500 favor and a 33% chance to gain anywhere from 500 to 1000 favor.

            (This command has a cooldown until 12am UTC.)"""
        )
        if (rand := random.randint(0, 2)) == 0:
            message = random.choice(
                [
                    _("They obviously didn't like your prayer!"),
                    _("Noone heard you!"),
                    _("Your voice has made them screw off."),
                    _("Even a donkey would've been a better follower than you."),
                ]
            )
            val = 0
        elif rand == 1:
            val = random.randint(1, 500)
            message = random.choice(
                [
                    _("„Rather lousy, but okay“, they said."),
                    _("You were a little sleepy."),
                    _("They were a little amused about your singing."),
                    _("Hearing the same prayer over and over again made them tired."),
                ]
            )
        elif rand == 2:
            val = random.randint(0, 500) + 500
            message = random.choice(
                [
                    _("Your Gregorian chants were amazingly well sung."),
                    _("Even the birds joined in your singing."),
                    _(
                        "The other gods applauded while your god noted down the best"
                        " mark."
                    ),
                    _("Rarely have you had a better day!"),
                ]
            )
        if val > 0:
            await self.bot.pool.execute(
                'UPDATE profile SET "favor"="favor"+$1 WHERE "user"=$2;',
                val,
                ctx.author.id,
            )
        await ctx.send(
            _("Your prayer resulted in **{val}** favor. {message}").format(
                val=val, message=message
            )
        )

    @has_god()
    @has_char()
    @commands.command(aliases=["favour"], brief=_("Shows your God and favor"))
    @locale_doc
    async def favor(self, ctx):
        _(
            """Shows your current God and how much favor you have with them at the time.

            If you have enough favor to place in the top 25 of that God's followers, you will gain extra luck when the new luck is decided, this usually happens on Monday.
              - The top 25 to 21 will gain +0.1 luck
              - The top 20 to 16 will gain +0.2 luck
              - The top 15 to 11 will gain +0.3 luck
              - The top 10 to 6 will gain +0.4 luck
              - The top 5 to 1 will gain +0.5 luck

            These extra luck values are based off the decided luck value.
            For example, if your God's luck value is decided to be 1.2 and you are the 13th best follower, you will have 1.5 luck for that week.
            All favor is reset to 0 when the new luck is decided to make it fair for everyone."""
        )
        await ctx.send(
            _("Your god is **{god}** and you have **{favor}** favor with them.").format(
                god=ctx.character_data["god"], favor=ctx.character_data["favor"]
            )
        )

    # just like admin commands, these aren't translated
    @has_char()
    @commands.command(brief=_("Show the top followers of your God"))
    @locale_doc
    async def followers(self, ctx, limit: IntGreaterThan(0)):
        _(
            """`<limit>` - A whole number from 1 to 25. If you are a God, the upper bound is lifted.

            Display your God's (or your own, if you are a God) top followers, up to `<limit>`.
            """
        )
        if ctx.author.id in self.bot.gods:
            god = self.bot.gods[ctx.author.id]["name"]
        elif not ctx.character_data["god"]:
            return await ctx.send(
                _(
                    "You are not following any god currently, therefore the list cannot be generated."
                )
            )
        else:
            god = ctx.character_data["god"]
            if limit > 25:
                return await ctx.send(_("Normal followers may only view the top 25."))

        data = await self.bot.pool.fetch(
            'SELECT * FROM profile WHERE "god"=$1 ORDER BY "favor" DESC LIMIT $2;',
            god,
            limit,
        )

        if not data:
            return await ctx.send(_("No followers found for this God."))

        # Create an embed
        embed = discord.Embed(
            title=_("Top Followers of {god}").format(god=god),
            color=discord.Color.blue(),
        )

        for idx, record in enumerate(data, start=1):
            user = self.bot.get_user(record["user"])
            display_name = user.display_name if user else _("Unknown User")
            favor = record["favor"]
            luck = record["luck"]

            embed.add_field(
                name=f"{idx}. {display_name}",
                value=_("Favor: {favor}, Luck: {luck}").format(favor=favor, luck=luck),
                inline=False,
            )

        # Send the embed
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Gods(bot))
