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
import datetime
import re
import traceback

from discord import Embed, File
from decimal import Decimal, ROUND_HALF_UP, getcontext
import utils.misc as rpgtools
import discord

from discord.enums import ButtonStyle
import random as randomm
from discord.ext import commands, tasks
from discord.ui.button import Button
from discord.interactions import Interaction
from discord.ui import Button, View

from classes.classes import Raider
from classes.classes import from_string as class_from_string
from classes.converters import IntGreaterThan
from cogs.shard_communication import user_on_cooldown as user_cooldown
from utils import random
from utils.checks import AlreadyRaiding, has_char, is_gm, is_god
from utils.i18n import _, locale_doc
from utils.joins import JoinView


def raid_channel():
    def predicate(ctx):
        return (
                ctx.bot.config.bot.is_beta
                or ctx.channel.id == ctx.bot.config.game.raid_channel
        )

    return commands.check(predicate)


def raid_free():
    async def predicate(ctx):
        ttl = await ctx.bot.redis.execute_command("TTL", "special:raid")
        if ttl != -2:
            raise AlreadyRaiding("There is already a raid ongoing.")
        return True

    return commands.check(predicate)


def is_cm():
    def predicate(ctx) -> bool:
        return (
                ctx.guild.id == ctx.bot.config.game.support_server_id
                and 491353140042530826 in [r.id for r in ctx.author.roles]
        )

    return commands.check(predicate)


class DecisionButton(Button):
    def __init__(self, label, *args, **kwargs):
        super().__init__(label=label, *args, **kwargs)

    async def callback(self, interaction: Interaction):
        view: DecisionView = self.view
        view.value = self.custom_id
        await interaction.response.send_message(f"You selected {self.custom_id}. Shortcut back: <#1199300390903099412>",
                                                ephemeral=True)
        view.stop()


class DecisionView(View):
    def __init__(self, player, options, timeout=60):
        super().__init__(timeout=timeout)
        self.player = player
        self.value = None
        for option in options:
            self.add_item(DecisionButton(style=ButtonStyle.primary, label=option, custom_id=option))

    async def interaction_check(self, interaction: Interaction) -> bool:
        return interaction.user == self.player


class Raid(commands.Cog):
    """Raids are only available in the support server. Use the support command for an invite link."""

    def __init__(self, bot):
        self.bot = bot
        self.raid = {}
        self.toggle_list = set()  # Use a set for efficient membership checking

        self.joined = []
        self.raidactive = False
        self.active_view = None
        self.raid_preparation = False
        self.boss = None
        self.allow_sending = discord.PermissionOverwrite(
            send_messages=True, read_messages=True
        )
        self.deny_sending = discord.PermissionOverwrite(
            send_messages=False, read_messages=False
        )
        self.read_only = discord.PermissionOverwrite(
            send_messages=False, read_messages=True
        )

    def getfinaldmg(self, damage: Decimal, defense):
        return v if (v := damage - defense) > 0 else 0

    async def set_raid_timer(self):
        await self.bot.redis.execute_command(
            "SET",
            "special:raid",
            "running",  # ctx isn't available
            "EX",
            3600,  # signup period + time until timeout
        )

    async def clear_raid_timer(self):
        await self.bot.redis.execute_command("DEL", "special:raid")

    @is_gm()
    @commands.command(hidden=True)
    async def gmclearraid(self, ctx):
        await self.bot.redis.execute_command("DEL", "special:raid")
        await ctx.send("Raid timer cleared!")

    @is_gm()
    @commands.command(hidden=True)
    async def alterraid(self, ctx, newhp: IntGreaterThan(0)):
        """[Bot Admin only] Change a raid boss' HP."""
        if not self.boss:
            return await ctx.send("No Boss active!")
        self.boss.update(hp=newhp, initial_hp=newhp)
        try:
            spawnmsg = await ctx.channel.fetch_message(self.boss["message"])
            edited_embed = spawnmsg.embeds[0]
            edited_embed.description = re.sub(
                r"\d+(,*\d)+ HP", f"{newhp:,.0f} HP", edited_embed.description
            )
            edited_embed.set_image(url="attachment://dragon.webp")
            await spawnmsg.edit(embed=edited_embed)
        except discord.NotFound:
            return await ctx.send("Could not edit Boss HP!")
        await ctx.send("Boss HP updated!")

    @is_gm()
    @commands.command()
    async def getraidkeys(self, ctx):
        try:
            keys = [str(key) for key in self.raid.keys()]

            if not keys:
                await ctx.send("No participants in the raid.")
                return

            # Convert list of keys to a single string
            message = ", ".join(keys)

            # Split the message into chunks of 2000 characters
            for chunk in [message[i:i + 2000] for i in range(0, len(message), 2000)]:
                await ctx.send(chunk)
        except Exception as e:
            await ctx.send(f"An error occurred: {e}")

    @is_gm()
    @raid_channel()
    @raid_free()
    @commands.command(hidden=True, brief=_("Start a Ragnorak raid"))
    async def spawn(self, ctx, hp: IntGreaterThan(0), rarity: str = "magic", raid_hp: int = 17776):
        try:
            if rarity not in ["magic", "legendary", "rare", "uncommon", "common", "mystery", "fortune", "divine"]:
                raise ValueError("Invalid rarity specified.")
            # rest of your function

            """[Bot Admin only] Starts a raid."""
            await ctx.message.delete()
            await self.set_raid_timer()

            self.boss = {"hp": hp, "initial_hp": hp, "min_dmg": 1, "max_dmg": 750}
            self.joined = []

            # await ctx.channel.set_permissions(
            # ctx.guild.default_role,
            # overwrite=self.read_only,
            # )

            fi = discord.File("assets/other/dragon.jpeg")
            em = discord.Embed(
                title="Ragnarok Spawned",
                description=(
                    f"This boss has {self.boss['hp']:,.0f} HP and has high-end loot!\nThe"
                    " Ragnarok will be vulnerable in 15 Minutes!"
                    f" Raiders HP: {'Standard' if raid_hp == 17776 else raid_hp}"
                ),
                color=self.bot.config.game.primary_colour,
            )

            em.set_image(url="attachment://dragon.jpeg")
            em.set_thumbnail(url=ctx.author.display_avatar.url)

            view = JoinView(
                Button(style=ButtonStyle.primary, label="Join the raid!"),
                message=_("You joined the raid."),
                timeout=60 * 15,
            )
            fi_path = "assets/other/dragon.jpeg"
            try:
                channels_ids = [1140211789573935164, 1199297906755252234,
                                1158743317325041754]  # Replace with your actual channel IDs

                message_ids = []  # To store the IDs of the sent messages

                for channel_id in channels_ids:
                    try:
                        channel = self.bot.get_channel(channel_id)  # Assumes ctx.guild is available
                        if channel:
                            fi = File(fi_path)  # Create a new File instance for each channel
                            sent_msg = await channel.send(embed=em, file=fi, view=view)
                            message_ids.append(sent_msg.id)
                        else:
                            await ctx.send(f"Channel with ID {channel_id} not found.")
                    except Exception as e:
                        error_message = f"Error in channel with ID {channel_id}: {e}. continuing.."
                        await ctx.send(error_message)
                        print(error_message)
                        continue

                self.boss.update(message=message_ids)

                if self.bot.config.bot.is_beta:
                    summary_channel = self.bot.get_channel(1199299514490683392)

                    channels_ids = [1140211789573935164, 1199297906755252234,
                                    1158743317325041754]  # Replace with your actual channel IDs
                    message_ids = []  # To store the IDs of the sent messages

                    for channel_id in channels_ids:
                        try:
                            channel = self.bot.get_channel(channel_id)  # Assumes ctx.guild is available
                            if channel:
                                role_id = 1199307259965079552  # Replace with the actual role ID
                                role = discord.utils.get(ctx.guild.roles, id=role_id)
                                content = f"{role.mention} Ragnarok spawned! 15 Minutes until he is vulnerable..."
                                sent_msg = await channel.send(content, allowed_mentions=discord.AllowedMentions(roles=True))
                                message_ids.append(sent_msg.id)
                        except Exception as e:
                            error_message = f"Error in channel with ID {channel_id}: {e}. continuing.."
                            await ctx.send(error_message)
                            print(error_message)
                            continue

                    self.boss.update(message=message_ids)
                    self.raid_preparation = True
                    self.raidactive = True

                    # Countdown messages
                    time_intervals = [300, 300, 180, 60, 30, 20, 10]
                    messages = ["**The dragon will be vulnerable in 10 minutes**",
                                "**The dragon will be vulnerable in 5 minutes**",
                                "**The dragon will be vulnerable in 2 minutes**",
                                "**The dragon will be vulnerable in 1 minute**",
                                "**The dragon will be vulnerable in 30 seconds**",
                                "**The dragon will be vulnerable in 20 seconds**",
                                "**The dragon will be vulnerable in 10 seconds**"]

                    for interval, message in zip(time_intervals, messages):
                        await asyncio.sleep(interval)
                        for channel_id in channels_ids:
                            try:
                                channel = self.bot.get_channel(channel_id)
                                if channel:
                                    await channel.send(message)
                            except Exception as e:
                                error_message = f"Error in channel with ID {channel_id}: {e}. continuing.."
                                await ctx.send(error_message)
                                print(error_message)
                                continue
            except Exception as e:
                error_message = f"Unexpected error: {e}"
                await ctx.send(error_message)
                print(error_message)

                self.raidactive = False

            view.stop()

            for channel_id in channels_ids:
                channel = self.bot.get_channel(channel_id)
                if channel:
                    await channel.send("**Ragnarok is vulnerable! Fetching participant data... Hang on!**")

            self.joined.extend(view.joined)
            # Assuming you have the role ID for the server booster role
            BOOSTER_ROLE_ID = 1281411439747268692  # Replace with your actual booster role ID

            tier_threshold = 1

            # Fetch Discord IDs where tier is 1 or above
            discord_ids = await self.bot.pool.fetch(
                'SELECT "user" FROM profile WHERE "tier" >= $1;',
                tier_threshold
            )

            # Extract the IDs from the result and append them to a list
            user_ids_list = [record['user'] for record in discord_ids]

            # Get User objects for each user ID, handling cases where a user may not be found
            users = [self.bot.get_user(user_id) or await self.bot.fetch_user(user_id) for user_id in user_ids_list]

            # Append the User objects to your existing list (e.g., self.joined)
            self.joined.extend(users)

            # Fetch members with the server booster role
            guild = self.bot.get_guild(1199287508794626078)  # Replace YOUR_GUILD_ID with your server's ID
            if guild:
                booster_role = guild.get_role(BOOSTER_ROLE_ID)
                if booster_role:
                    # Fetch all members with the server booster role
                    booster_members = [member for member in guild.members if booster_role in member.roles]
                    # Append these members to self.joined
                    self.joined.extend(booster_members)

            async with self.bot.pool.acquire() as conn:
                for u in self.joined:
                    profile = await conn.fetchrow('SELECT * FROM profile WHERE "user"=$1;', u.id)
                    if not profile:
                        # You might want to send a message or log that the profile wasn't found.
                        continue
                    dmg, deff = await self.bot.get_raidstats(
                        u,
                        atkmultiply=profile["atkmultiply"],
                        defmultiply=profile["defmultiply"],
                        classes=profile["class"],
                        race=profile["race"],
                        guild=profile["guild"],
                        conn=conn,
                    )
                    if raid_hp == 17776:
                        stathp = profile["stathp"] * 50
                        level = rpgtools.xptolevel(profile["xp"])
                        raidhp = profile["health"] + 250 + (level * 5) + stathp
                    else:
                        raidhp = raid_hp
                    self.raid[(u, "user")] = {"hp": raidhp, "armor": deff, "damage": dmg}

            raiders_joined = len(self.raid)  # Replace with your actual channel IDs

            # Final message with gathered data
            for channel_id in channels_ids:
                channel = self.bot.get_channel(channel_id)
                if channel:
                    await channel.send(f"**Done getting data! {raiders_joined} Raiders joined.**")

            start = datetime.datetime.utcnow()

            while (
                    self.boss["hp"] > 0
                    and len(self.raid) > 0
                    and datetime.datetime.utcnow() < start + datetime.timedelta(minutes=45)
            ):
                (target, participant_type) = random.choice(list(self.raid.keys()))
                dmg = random.randint(self.boss["min_dmg"], self.boss["max_dmg"])
                finaldmg = self.getfinaldmg(dmg, self.raid[(target, participant_type)]["armor"])
                self.raid[(target, participant_type)]["hp"] -= finaldmg

                em = discord.Embed(title="Ragnarok attacked!", colour=0xFFB900)

                if self.raid[(target, participant_type)]["hp"] > 0:  # If target is still alive
                    description = f"{target.mention if participant_type == 'user' else target} now has {self.raid[(target, participant_type)]['hp']} HP!"
                    em.description = description
                    em.add_field(name="Theoretical Damage", value=finaldmg + self.raid[(target, participant_type)]["armor"])
                    em.add_field(name="Shield", value=self.raid[(target, participant_type)]["armor"])
                    em.add_field(name="Effective Damage", value=finaldmg)
                else:  # If target has died
                    description = f"{target.mention if participant_type == 'user' else target} died!"
                    em.description = description
                    em.add_field(name="Theoretical Damage", value=finaldmg + self.raid[(target, participant_type)]["armor"])
                    em.add_field(name="Shield", value=self.raid[(target, participant_type)]["armor"])
                    em.add_field(name="Effective Damage", value=finaldmg)
                    del self.raid[(target, participant_type)]

                if participant_type == "user":
                    em.set_author(name=str(target), icon_url=target.display_avatar.url)
                else:  # For bots
                    em.set_author(name=str(target))
                em.set_thumbnail(url=f"https://gcdnb.pbrd.co/images/GTGxc2PQxJiD.png")
                for channel_id in channels_ids:
                    channel = self.bot.get_channel(channel_id)
                    if channel:
                        await channel.send(embed=em)

                dmg_to_take = sum(i["damage"] for i in self.raid.values())
                self.boss["hp"] -= dmg_to_take
                await asyncio.sleep(4)

                em = discord.Embed(title="The raid attacked Ragnarok!", colour=0xFF5C00)
                em.set_thumbnail(url=f"https://gcdnb.pbrd.co/images/EjEN1hcCFtID.png")
                em.add_field(name="Damage", value=dmg_to_take)

                if self.boss["hp"] > 0:
                    em.add_field(name="HP left", value=self.boss["hp"])
                else:
                    em.add_field(name="HP left", value="Dead!")
                for channel_id in channels_ids:
                    channel = self.bot.get_channel(channel_id)
                    if channel:
                        await channel.send(embed=em)
                await asyncio.sleep(4)

            if len(self.raid) == 0:
                for channel_id in channels_ids:
                    channel = self.bot.get_channel(channel_id)
                    if channel:
                        m = await channel.send("The raid was all wiped!")
                        await m.add_reaction("\U0001F1EB")

                summary_text = (
                    "Emoji_here The raid was all wiped! Ragnarok had"
                    f" **{self.boss['hp']:,.3f}** health remaining. Better luck next time."
                )
                try:
                    summary = (
                        "**Raid result:**\n"
                        f"Emoji_here Health: **{self.boss['initial_hp']:,.0f}**\n"
                        f"{summary_text}\n"
                        f"Emoji_here Raiders joined: **{raiders_joined}**"
                    )
                    summary = summary.replace(
                        "Emoji_here",
                        ":small_blue_diamond:" if self.boss["hp"] < 1 else ":vibration_mode:"
                    )
                    summary_channel = self.bot.get_channel(1199299514490683392)

                    summary_msg = await summary_channel.send(summary)
                    self.raid.clear()
                    await self.clear_raid_timer()

                except Exception as e:
                    await ctx.send(f"An error has occurred: {e}")
            elif self.boss["hp"] < 1:
                raid_duration = datetime.datetime.utcnow() - start
                minutes = (raid_duration.seconds % 3600) // 60
                seconds = raid_duration.seconds % 60
                summary_duration = f"{minutes} minutes, {seconds} seconds"

                await ctx.channel.set_permissions(
                    ctx.guild.default_role,
                    overwrite=self.allow_sending,
                )

                highest_bid = [
                    1_136_590_782_183_264_308,
                    0,
                ]  # userid, amount

                bots = sum(1 for _, p_type in self.raid.keys() if p_type == "bot")

                self.raid = {k: v for k, v in self.raid.items() if k[1] == "user"}

                raid_user_ids = [k[0].id for k, v in self.raid.items() if k[1] == 'user']

                def check(msg):
                    try:
                        val = int(msg.content)
                    except ValueError:
                        return False
                    if msg.channel.id != ctx.channel.id or not any(msg.author == k[0] for k in self.raid.keys()):
                        return False
                    if highest_bid[1] == 0:  # Allow starting bid to be $1
                        if val < 1:
                            return False
                        else:
                            return True
                    if val > highest_bid[1]:
                        if highest_bid[1] < 100:
                            return True
                    if val < int(highest_bid[1] * 1.1):  # Minimum bid is 10% higher than the highest bid
                        return False
                    if (
                            msg.author.id == highest_bid[0]
                    ):  # don't allow a player to outbid themselves
                        return False
                    return True

                # If there are no users left in the raid, skip the bidding
                if not self.raid:
                    await ctx.send(f"No survivors left to bid on the {rarity} Crate!")
                    summary_text = (
                        f"Emoji_here Defeated in: **{summary_duration}**\n"
                        f"Emoji_here Survivors: **0 players and {bots} of Drakath's forces**"
                    )
                else:
                    page = commands.Paginator()
                    for u in self.raid.keys():
                        page.add_line(u[0].mention)

                    emote_for_rarity = getattr(self.bot.cogs['Crates'].emotes, rarity)
                    page.add_line(
                        f"The raid killed the boss!\nHe was guarding a {emote_for_rarity} {rarity.capitalize()} Crate!\n"
                        "The highest bid for it wins <:roopiratef:1146234370827505686>\nSimply type how much you bid!"
                    )

                    # Assuming page.pages is a list of pages
                    for channel_id in channels_ids:
                        channel = self.bot.get_channel(channel_id)
                        if channel:
                            for p in page.pages:
                                await channel.send(p[4:-4])

                    while True:
                        try:
                            msg = await self.bot.wait_for("message", timeout=60, check=check)
                        except asyncio.TimeoutError:
                            break
                        bid = int(msg.content)
                        money = await self.bot.pool.fetchval(
                            'SELECT money FROM profile WHERE "user"=$1;', msg.author.id
                        )
                        if money and money >= bid:
                            highest_bid = [msg.author.id, bid]
                            if highest_bid[1] >= 100:
                                next_bid = int(highest_bid[1] * 1.1)
                                for channel_id in channels_ids:
                                    channel = self.bot.get_channel(channel_id)
                                    if channel:
                                        await channel.send(f"{msg.author.mention} bids **${msg.content}**!\n The minimum next bid is **${next_bid}**.")

                            else:
                                for channel_id in channels_ids:
                                    channel = self.bot.get_channel(channel_id)
                                    if channel:
                                        await channel.send(f"{msg.author.mention} bids **${msg.content}**!")


                    msg_content = (
                        f"Auction done! Winner is <@{highest_bid[0]}> with"
                        f" **${highest_bid[1]}**!\nGiving {rarity.capitalize()} Crate... Done!"
                    )

                    # Send the initial message to all channels
                    for channel_id in channels_ids:
                        channel = self.bot.get_channel(channel_id)
                        if channel:
                            msg = await channel.send(msg_content)

                    # Execute the database commands once outside the loop
                    money = await self.bot.pool.fetchval(
                        'SELECT money FROM profile WHERE "user"=$1;', highest_bid[0]
                    )

                    if money >= highest_bid[1]:
                        column_name = f"crates_{rarity}"

                        async with self.bot.pool.acquire() as conn:
                            await conn.execute(
                                f'UPDATE profile SET "money"="money"-$1, "{column_name}"="{column_name}"+1 WHERE "user"=$2;',
                                highest_bid[1],
                                highest_bid[0],
                            )

                            await self.bot.log_transaction(
                                ctx,
                                from_=highest_bid[0],
                                to=2,
                                subject="raid bid winner",
                                data={"Gold": highest_bid[1]},
                                conn=conn,
                            )

                        # Edit the message content once after executing the database commands

                        summary_crate = (
                            f"Emoji_here {rarity.capitalize()} crate {emote_for_rarity} "
                            f"sold to: **<@{highest_bid[0]}>** for **${highest_bid[1]:,.0f}**"
                        )
                    else:
                        for channel_id in channels_ids:
                            channel = self.bot.get_channel(channel_id)
                            if channel:
                                await channel.send(
                                    f"<@{highest_bid[0]}> spent the money in the meantime... Meh!"
                                    " No one gets it then, pah!\nThis incident has been reported and"
                                    " they will get banned if it happens again. Cheers!"
                                )

                        # Edit the message content once after executing the database commands
                        for channel_id in channels_ids:
                            channel = self.bot.get_channel(channel_id)
                            if channel:
                                await channel.send(
                                    f"Emoji_here The {rarity.capitalize()} Crate was not given to anyone since the"
                                    f" supposed winning bidder <@{highest_bid[0]}> spent the money in"
                                    " the meantime. They will get banned if it happens again."
                                )

                    # cash_pool = 4
                    #cash_pool = 1000000 / 4
                    cash_pool = hp * 1.3
                    survivors = len(self.raid)
                    self.raid = {(user, p_type): data for (user, p_type), data in self.raid.items() if
                                 p_type == "user" and not user.bot}
                    cash = int(cash_pool / survivors)
                    users = [user.id for user, p_type in self.raid.keys() if p_type == "user"]
                    await self.bot.pool.execute(
                        'UPDATE profile SET "money"="money"+$1 WHERE "user"=ANY($2);',
                        cash,
                        users
                    )
                    # Send the final message to all channels
                    for channel_id in channels_ids:
                        channel = self.bot.get_channel(channel_id)
                        if channel:
                            await channel.send(
                                f"**Gave ${cash:,.0f} of Ragnarok's ${cash_pool:,.0f} drop to all survivors!**")

                    summary_text = (
                        f"Emoji_here Defeated in: **{summary_duration}**\n"
                        f"{summary_crate}\n"
                        f"Emoji_here Payout per survivor: **${cash:,.0f}**\n"
                        f"Emoji_here Survivors: **{survivors} and {bots} of placeholders forces**"
                    )

                    # Assuming channels_ids is a list of channel IDs
                    if self.boss["hp"] > 1:
                        for channel_id in channels_ids:
                            channel = self.bot.get_channel(channel_id)
                            if channel:
                                m = await ctx.send(
                                    "The raid did not manage to kill Ragnarok within 45 Minutes... He disappeared!")
                                await m.add_reaction("\U0001F1EB")
                                summary_text = (
                                    "Emoji_here The raid did not manage to kill Ragnarok within 45"
                                    f" Minutes... He disappeared with **{self.boss['hp']:,.3f}** health remaining."
                                )

                await asyncio.sleep(30)
                await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=self.deny_sending)
                await self.clear_raid_timer()
                try:
                    self.raid.clear()
                except Exception as e:
                    await ctx.send(f"An error occurred: {e}")

                if self.bot.config.bot.is_beta:
                    summary = (
                        "**Raid result:**\n"
                        f"Emoji_here Health: **{self.boss['initial_hp']:,.0f}**\n"
                        f"{summary_text}\n"
                        f"Emoji_here Raiders joined: **{raiders_joined}**"
                    )
                    summary = summary.replace(
                        "Emoji_here",
                        ":small_blue_diamond:" if self.boss["hp"] < 1 else ":vibration_mode:"
                    )
                    summary_channel = self.bot.get_channel(1199299514490683392)
                    summary_msg = await summary_channel.send(summary)

                #await ctx.send("attempting to clear keys...")
                try:
                    self.raid.clear()
                except Exception as e:
                    await ctx.send(f"An error occurred: {e}")
                self.raid_preparation = False
                self.boss = None
        except Exception as e:
            import traceback
            error_message = f"Error occurred: {e}\n"
            error_message += traceback.format_exc()
            await ctx.send(error_message)
            print(error_message)

    async def get_random_user_info(self, ctx):
        try:
            # Fetch a random user ID and display name from the database
            async with self.bot.pool.acquire() as connection:
                # Modify the query based on your database structure
                result = await connection.fetchrow('SELECT "user" FROM profile ORDER BY RANDOM() LIMIT 1')

                # Get the display name using the Discord API
                user_id = result["user"]
                user = await self.bot.fetch_user(user_id)
                display_name = user.display_name

                # Return user ID and display name
                return {"user_id": user_id, "display_name": display_name}

        except Exception as e:
            # Handle exceptions, you can customize this part based on your needs
            await ctx.send(f"An error occurred in get_random_user_info: {e}")
            return None

    @commands.command()
    @is_gm()
    async def aijoin(self, ctx, quantity: int = 1):
        try:
            if not self.raid_preparation:
                return await ctx.send("You can only add bots during raid preparation!")

            bot_counts = {}  # Keep track of how many bots have been added

            for _ in range(quantity):
                # Fetch a random user ID and display name from the database
                user_info = await self.get_random_user_info(ctx)

                # If a bot has been added before, update its count
                if "bot" in bot_counts:
                    bot_counts["bot"] += 1
                else:
                    bot_counts["bot"] = 1

                # Construct the bot player entry and add it to the raid dictionary

                bot_entry = (user_info["display_name"], "bot")
                self.raid[bot_entry] = {
                    "user": user_info["user_id"],
                    "hp": Decimal(str(round(randomm.uniform(50.0, 400.0), 2))).quantize(Decimal("0.00"),
                                                                                        rounding=ROUND_HALF_UP),
                    "armor": Decimal(str(round(randomm.uniform(50.0, 150.0), 2))).quantize(Decimal("0.00"),
                                                                                           rounding=ROUND_HALF_UP),
                    "damage": Decimal(str(round(randomm.uniform(100.0, 250.0), 2))).quantize(Decimal("0.00"),
                                                                                             rounding=ROUND_HALF_UP),
                }
            # Construct the summary for reinforcements
            reinforcement_summary = ', '.join([f"{count} {bot}" for bot, count in bot_counts.items()])

            random_number = randomm.randint(1, 3)
            if random_number == 1:
                embed = Embed(
                    title="The Shadows Stir...",
                    description=(
                        "As the whispers of Drakath's faithful grew louder, a dark mist enveloped the battlefield. "
                        f"From the heart of this shadow, {quantity} warriors emerged. "
                        "Ragnarok's challenges just became more... sinister."),
                    color=0x8a2be2  # Setting the color to a shade of purple to match the theme
                )
                embed.set_thumbnail(
                    url="https://i.ibb.co/RGXPhCD/several-evil-warriors-purple-corruption-purple-flames.png")

                await ctx.send(embed=embed)

            if random_number == 2:
                embed = Embed(
                    title="Astraea's Grace...",
                    description=(
                        "As the benevolent aura of Goddess Astraea permeates the air, a radiant light bathes the battlefield. "
                        f"From the celestial realm, {quantity} champions descended. "
                        "Ragnarok's challenges now face the divine intervention of Astraea."),
                    color=0xffd700  # Setting the color to gold to match the theme for a benevolent goddess
                )
                embed.set_thumbnail(
                    url="https://i.ibb.co/TTh7rZJ/image.png")  # Replace with an image URL representing Astraea's grace

                await ctx.send(embed=embed)

            if random_number == 3:
                embed = Embed(
                    title="Sepulchure's Malevolence...",
                    description=(
                        "As the malevolent presence of Sepulchure looms over the battlefield, a darkness shrouds the surroundings. "
                        f"From the depths of this abyss, {quantity} dreadknights emerged. "
                        "Ragnarok's challenges now bear the mark of Sepulchure's sinister influence."),
                    color=0x800000  # Setting the color to maroon to match the theme for an evil god
                )
                embed.set_thumbnail(
                    url="https://i.ibb.co/FmdPdV2/2.png")  # Replace with an image URL representing Sepulchure's malevolence

                await ctx.send(embed=embed)

        except Exception as e:
            await ctx.send(f"An error occurred: {e}")

    @is_god()
    @raid_free()
    @commands.command(hidden=True, brief=_("Start a Kvothe raid"))
    async def kvothespawn(self, ctx, scrael: IntGreaterThan(1)):
        """[Kvothe only] Starts a raid."""
        await self.set_raid_timer()
        scrael = [{"hp": random.randint(80, 100), "id": i + 1} for i in range(scrael)]

        view = JoinView(
            Button(style=ButtonStyle.primary, label="Join the raid!"),
            message=_("You joined the raid."),
            timeout=60 * 15,
        )

        await ctx.send(
            """
The cthae has gathered an army of scrael. Fight for your life!

**Only Kvothe's followers may join.**""",
            file=discord.File("assets/other/cthae.webp"),
            view=view,
        )
        if not self.bot.config.bot.is_beta:
            await asyncio.sleep(300)
            await ctx.send("**The scrael arrive in 10 minutes**")
            await asyncio.sleep(300)
            await ctx.send("**The scrael arrive in 5 minutes**")
            await asyncio.sleep(180)
            await ctx.send("**The scrael arrive in 2 minutes**")
            await asyncio.sleep(60)
            await ctx.send("**The scrael arrive in 1 minute**")
            await asyncio.sleep(30)
            await ctx.send("**The scrael arrive in 30 seconds**")
            await asyncio.sleep(20)
            await ctx.send("**The scrael arrive in 10 seconds**")
            await asyncio.sleep(10)
            await ctx.send(
                "**The scrael arrived! Fetching participant data... Hang on!**"
            )
        else:
            await asyncio.sleep(60)

        view.stop()

        async with self.bot.pool.acquire() as conn:
            raid = {}
            for u in view.joined:
                if (
                        not (
                                profile := await conn.fetchrow(
                                    'SELECT * FROM profile WHERE "user"=$1;', u.id
                                )
                        )
                        or profile["god"] != "Kvothe"
                ):
                    continue
                try:
                    dmg, deff = await self.bot.get_raidstats(
                        u,
                        atkmultiply=profile["atkmultiply"],
                        defmultiply=profile["defmultiply"],
                        classes=profile["class"],
                        race=profile["race"],
                        guild=profile["guild"],
                        god=profile["god"],
                        conn=conn,
                    )
                except ValueError:
                    continue
                raid[u] = {"hp": 100, "armor": deff, "damage": dmg, "kills": 0}

        await ctx.send("**Done getting data!**")

        start = datetime.datetime.utcnow()

        while (
                len(scrael) > 0
                and len(raid) > 0
                and datetime.datetime.utcnow() < start + datetime.timedelta(minutes=45)
        ):
            target, target_data = random.choice(list(raid.items()))
            dmg = random.randint(35, 65)
            dmg = self.getfinaldmg(
                dmg, target_data["armor"] * Decimal(random.choice(["0.4", "0.5"]))
            )
            target_data["hp"] -= dmg
            em = discord.Embed(title=f"Scrael left: `{len(scrael)}`", colour=0x000000)
            em.add_field(name="Scrael HP", value=f"{scrael[0]['hp']} HP left")
            if target_data["hp"] > 0:
                em.add_field(
                    name="Attack", value=f"Scrael is fighting against `{target}`"
                )
            else:
                em.add_field(name="Attack", value=f"Scrael killed `{target}`")
            em.add_field(
                name="Scrael Damage", value=f"Has dealt `{dmg}` damage to `{target}`"
            )
            em.set_image(url=f"{self.bot.BASE_URL}/scrael.jpg")
            await ctx.send(embed=em)
            if target_data["hp"] <= 0:
                del raid[target]
                if len(raid) == 0:  # no more raiders
                    break
            scrael[0]["hp"] -= target_data["damage"]
            await asyncio.sleep(7)
            em = discord.Embed(title=f"Heroes left: `{len(raid)}`", colour=0x009900)
            em.set_author(
                name=f"Hero ({target})", icon_url=f"{self.bot.BASE_URL}/swordsman1.jpg"
            )
            em.add_field(
                name="Hero HP", value=f"`{target}` got {target_data['hp']} HP left"
            )
            if scrael[0]["hp"] > 0:
                em.add_field(
                    name="Hero attack",
                    value=(
                        f"Is attacking the scrael and dealt `{target_data['damage']}`"
                        " damage"
                    ),
                )
            else:
                money = random.randint(250, 750)
                await self.bot.pool.execute(
                    'UPDATE profile SET "money"="money"+$1 WHERE "user"=$2;',
                    money,
                    target.id,
                )
                scrael.pop(0)
                em.add_field(
                    name="Hero attack", value=f"Killed the scrael and received ${money}"
                )
                if raid.get(target, None):
                    raid[target]["kills"] += 1
            em.set_image(url=f"{self.bot.BASE_URL}/swordsman2.jpg")
            await ctx.send(embed=em)
            await asyncio.sleep(7)

        if len(scrael) == 0:
            most_kills = sorted(raid.items(), key=lambda x: -(x[1]["kills"]))[0][0]
            async with self.bot.pool.acquire() as conn:
                await conn.execute(
                    'UPDATE profile SET "crates_legendary"="crates_legendary"+$1 WHERE'
                    ' "user"=$2;',
                    1,
                    most_kills.id,
                )
                await self.bot.log_transaction(
                    ctx,
                    from_=1,
                    to=most_kills.id,
                    subject="crates",
                    data={"Rarity": "legendary", "Amount": 1},
                    conn=conn,
                )
            await ctx.send(
                "The scrael were defeated! Our most glorious hero,"
                f" {most_kills.mention}, has received Kvothe's grace, a"
                f" {self.bot.cogs['Crates'].emotes.legendary}."
            )
        elif len(raid) == 0:
            await ctx.send(
                "The scrael have extinguished life in Kvothe's temple! All heroes died!"
            )
        await self.clear_raid_timer()

    @is_god()
    @raid_free()
    @commands.command(hidden=True, brief=_("Start an Eden raid"))
    async def edenspawn(self, ctx, hp: IntGreaterThan(0)):
        """[Eden only] Starts a raid."""
        await self.set_raid_timer()
        self.boss = {"hp": hp, "min_dmg": 100, "max_dmg": 500}
        await ctx.channel.set_permissions(
            ctx.guild.default_role,
            overwrite=self.read_only,
        )

        view = JoinView(
            Button(style=ButtonStyle.primary, label="Join the raid!"),
            message=_("You joined the raid."),
            timeout=60 * 15,
        )

        await ctx.send(
            f"""
The guardian of the gate to the garden has awoken! To gain entry to the Garden of Sanctuary that lays behind the gate you must defeat the guardian.
This boss has {self.boss['hp']} HP and will be vulnerable in 15 Minutes

**Only followers of Eden may join.**
""",
            file=discord.File("assets/other/guardian.webp"),
            view=view,
        )
        if not self.bot.config.bot.is_beta:
            await asyncio.sleep(300)
            await ctx.send("**The guardian will be vulnerable in 10 minutes**")
            await asyncio.sleep(300)
            await ctx.send("**The guardian will be vulnerable in 5 minutes**")
            await asyncio.sleep(180)
            await ctx.send("**The guardian will be vulnerable in 2 minutes**")
            await asyncio.sleep(60)
            await ctx.send("**The guardian will be vulnerable in 1 minute**")
            await asyncio.sleep(30)
            await ctx.send("**The guardian will be vulnerable in 30 seconds**")
            await asyncio.sleep(20)
            await ctx.send("**The guardian will be vulnerable in 10 seconds**")
            await asyncio.sleep(10)
        else:
            await asyncio.sleep(60)

        view.stop()

        await ctx.send(
            "**The guardian is vulnerable! Fetching participant data... Hang on!**"
        )

        async with self.bot.pool.acquire() as conn:
            raid = {}
            for u in view.joined:
                if (
                        not (
                                profile := await conn.fetchrow(
                                    'SELECT * FROM profile WHERE "user"=$1;', u.id
                                )
                        )
                        or profile["god"] != "Eden"
                ):
                    continue
                try:
                    dmg, deff = await self.bot.get_raidstats(
                        u,
                        atkmultiply=profile["atkmultiply"],
                        defmultiply=profile["defmultiply"],
                        classes=profile["class"],
                        race=profile["race"],
                        guild=profile["guild"],
                        god=profile["god"],
                        conn=conn,
                    )
                except ValueError:
                    continue
                raid[u] = {"hp": 250, "armor": deff, "damage": dmg}

        await ctx.send("**Done getting data!**")

        start = datetime.datetime.utcnow()

        while (
                self.boss["hp"] > 0
                and len(raid) > 0
                and datetime.datetime.utcnow() < start + datetime.timedelta(minutes=45)
        ):
            target = random.choice(list(raid.keys()))  # the guy it will attack
            dmg = random.randint(self.boss["min_dmg"], self.boss["max_dmg"])
            dmg = self.getfinaldmg(dmg, raid[target]["armor"])
            raid[target]["hp"] -= dmg  # damage dealt
            if raid[target]["hp"] > 0:
                em = discord.Embed(
                    title="The Guardian attacks the seekers of the garden!",
                    description=f"{target} now has {raid[target]['hp']} HP!",
                    colour=0xFFB900,
                )
            else:
                em = discord.Embed(
                    title="The Guardian attacks the seekers of the garden!",
                    description=f"{target} died!",
                    colour=0xFFB900,
                )
            em.add_field(name="Theoretical Damage", value=dmg + raid[target]["armor"])
            em.add_field(name="Shield", value=raid[target]["armor"])
            em.add_field(name="Effective Damage", value=dmg)
            em.set_author(name=str(target), icon_url=target.display_avatar.url)
            em.set_thumbnail(url=f"{self.bot.BASE_URL}/guardian_small.jpg")
            await ctx.send(embed=em)
            if raid[target]["hp"] <= 0:
                del raid[target]
            dmg_to_take = sum(i["damage"] for i in raid.values())
            self.boss["hp"] -= dmg_to_take
            await asyncio.sleep(4)
            em = discord.Embed(
                title="The seekers attacked the Guardian!", colour=0xFF5C00
            )
            em.set_thumbnail(url=f"{self.bot.BASE_URL}/eden_followers.jpg")
            em.add_field(name="Damage", value=dmg_to_take)
            if self.boss["hp"] > 0:
                em.add_field(name="HP left", value=self.boss["hp"])
            else:
                em.add_field(name="HP left", value="Dead!")
            await ctx.send(embed=em)
            await asyncio.sleep(4)

        if len(raid) == 0:
            await ctx.send("The raid was all wiped!")
        elif self.boss["hp"] < 1:
            await ctx.channel.set_permissions(
                ctx.guild.default_role,
                overwrite=self.allow_sending,
            )
            winner = random.choice(list(raid.keys()))
            await self.bot.pool.execute(
                'UPDATE profile SET "crates_legendary"="crates_legendary"+1 WHERE'
                ' "user"=$1;',
                winner.id,
            )
            await ctx.send(
                "The guardian was defeated, the seekers can enter the garden! Eden has"
                f" gracefully given {winner.mention} a legendary crate for their"
                " efforts."
            )

            # cash = int(hp / 4 / len(raid))  # what da hood gets per survivor
            cash = 11241
            users = [u.id for u in raid]
            await self.bot.pool.execute(
                'UPDATE profile SET money=money+$1 WHERE "user"=ANY($2);',
                cash,
                users,
            )
            await ctx.send(
                f"**Gave ${cash} of the Guardian's ${int(hp / 4)} drop to all"
                " survivors!**"
            )

        else:
            await ctx.send(
                "The raid did not manage to kill the Guardian within 45 Minutes... The"
                " entrance remains blocked!"
            )

        await asyncio.sleep(30)
        await ctx.channel.set_permissions(
            ctx.guild.default_role,
            overwrite=self.deny_sending,
        )
        await self.clear_raid_timer()
        self.boss = None

    @is_god()
    @raid_free()
    @commands.command(hidden=True, brief=_("Start a CHamburr raid"))
    async def chamburrspawn(self, ctx, hp: IntGreaterThan(0)):
        """[CHamburr only] Starts a raid."""
        await self.set_raid_timer()
        self.boss = {"hp": hp, "min_dmg": 100, "max_dmg": 500}
        await ctx.channel.set_permissions(
            ctx.guild.default_role,
            overwrite=self.read_only,
        )

        view = JoinView(
            Button(style=ButtonStyle.primary, label="Join the raid!"),
            message=_("You joined the raid."),
            timeout=60 * 15,
        )

        await ctx.send(
            f"""
*Time to eat the hamburger! No, this time, the hamburger will eat you up...*

This boss has {self.boss['hp']} HP and has high-end loot!
The hamburger will be vulnerable in 15 Minutes

**Only followers of CHamburr may join.**""",
            file=discord.File("assets/other/hamburger.webp"),
            view=view,
        )
        if not self.bot.config.bot.is_beta:
            await asyncio.sleep(300)
            await ctx.send("**The hamburger will be vulnerable in 10 minutes**")
            await asyncio.sleep(300)
            await ctx.send("**The hamburger will be vulnerable in 5 minutes**")
            await asyncio.sleep(180)
            await ctx.send("**The hamburger will be vulnerable in 2 minutes**")
            await asyncio.sleep(60)
            await ctx.send("**The hamburger will be vulnerable in 1 minute**")
            await asyncio.sleep(30)
            await ctx.send("**The hamburger will be vulnerable in 30 seconds**")
            await asyncio.sleep(20)
            await ctx.send("**The hamburger will be vulnerable in 10 seconds**")
            await asyncio.sleep(10)
        else:
            await asyncio.sleep(60)

        view.stop()

        await ctx.send(
            "**The hamburger is vulnerable! Fetching participant data... Hang on!**"
        )

        async with self.bot.pool.acquire() as conn:
            raid = {}
            for u in view.joined:
                if (
                        not (
                                profile := await conn.fetchrow(
                                    'SELECT * FROM profile WHERE "user"=$1;', u.id
                                )
                        )
                        or profile["god"] != "CHamburr"
                ):
                    continue
                try:
                    dmg, deff = await self.bot.get_raidstats(
                        u,
                        atkmultiply=profile["atkmultiply"],
                        defmultiply=profile["defmultiply"],
                        classes=profile["class"],
                        race=profile["race"],
                        guild=profile["guild"],
                        god=profile["god"],
                        conn=conn,
                    )
                except ValueError:
                    continue
                raid[u] = {"hp": 250, "armor": deff, "damage": dmg}

        await ctx.send("**Done getting data!**")

        start = datetime.datetime.utcnow()

        while (
                self.boss["hp"] > 0
                and len(raid) > 0
                and datetime.datetime.utcnow() < start + datetime.timedelta(minutes=45)
        ):
            target = random.choice(list(raid.keys()))  # the guy it will attack
            dmg = random.randint(self.boss["min_dmg"], self.boss["max_dmg"])
            dmg = self.getfinaldmg(dmg, raid[target]["armor"])
            raid[target]["hp"] -= dmg  # damage dealt
            if raid[target]["hp"] > 0:
                em = discord.Embed(
                    title="Hamburger attacked!",
                    description=f"{target} now has {raid[target]['hp']} HP!",
                    colour=0xFFB900,
                )
            else:
                em = discord.Embed(
                    title="Hamburger attacked!",
                    description=f"{target} died!",
                    colour=0xFFB900,
                )
            em.add_field(name="Theoretical Damage", value=dmg + raid[target]["armor"])
            em.add_field(name="Shield", value=raid[target]["armor"])
            em.add_field(name="Effective Damage", value=dmg)
            em.set_author(name=str(target), icon_url=target.display_avatar.url)
            em.set_thumbnail(url=f"{self.bot.BASE_URL}/hamburger.jpg")
            await ctx.send(embed=em)
            if raid[target]["hp"] <= 0:
                del raid[target]
            dmg_to_take = sum(i["damage"] for i in raid.values())
            self.boss["hp"] -= dmg_to_take
            await asyncio.sleep(4)
            em = discord.Embed(
                title="The raid attacked the hamburger!", colour=0xFF5C00
            )
            em.set_thumbnail(url=f"https://i.imgur.com/jxtVg6a.png")
            em.add_field(name="Damage", value=dmg_to_take)
            if self.boss["hp"] > 0:
                em.add_field(name="HP left", value=self.boss["hp"])
            else:
                em.add_field(name="HP left", value="Dead!")
            await ctx.send(embed=em)
            await asyncio.sleep(4)

        if len(raid) == 0:
            await ctx.send("The raid was all wiped!")
        elif self.boss["hp"] < 1:
            await ctx.channel.set_permissions(
                ctx.guild.default_role,
                overwrite=self.allow_sending,
            )
            highest_bid = [
                356_091_260_429_402_122,
                0,
            ]  # userid, amount

            def check(msg):
                if (
                        msg.channel.id != ctx.channel.id
                        or (not msg.content.isdigit())
                        or (msg.author not in raid)
                ):
                    return False
                if not (int(msg.content) > highest_bid[1]):
                    return False
                if (
                        msg.author.id == highest_bid[0]
                ):  # don't allow a player to outbid themselves
                    return False
                return True

            page = commands.Paginator()
            for u in list(raid.keys()):
                page.add_line(u.mention)
            page.add_line(
                "The raid killed the boss!\nHe dropped a"
                f" {self.bot.cogs['Crates'].emotes.legendary} Legendary Crate!\nThe highest"
                " bid for it wins <:roosip:505447694408482846>\nSimply type how much"
                " you bid!"
            )
            for p in page.pages:
                await ctx.send(p[4:-4])

            while True:
                try:
                    msg = await self.bot.wait_for("message", timeout=60, check=check)
                except asyncio.TimeoutError:
                    break
                bid = int(msg.content)
                money = await self.bot.pool.fetchval(
                    'SELECT money FROM profile WHERE "user"=$1;', msg.author.id
                )
                if money and money >= bid:
                    highest_bid = [msg.author.id, bid]
                    await ctx.send(f"{msg.author.mention} bids **${msg.content}**!")
            msg = await ctx.send(
                f"Auction done! Winner is <@{highest_bid[0]}> with"
                f" **${highest_bid[1]}**!\nGiving Legendary Crate..."
            )
            money = await self.bot.pool.fetchval(
                'SELECT money FROM profile WHERE "user"=$1;', highest_bid[0]
            )
            if money >= highest_bid[1]:
                async with self.bot.pool.acquire() as conn:
                    await conn.execute(
                        'UPDATE profile SET "money"="money"-$1,'
                        ' "crates_legendary"="crates_legendary"+1 WHERE "user"=$2;',
                        highest_bid[1],
                        highest_bid[0],
                    )
                    await self.bot.log_transaction(
                        ctx,
                        from_=highest_bid[0],
                        to=2,
                        subject="Raid Bid Winner",
                        data={"Gold": highest_bid[1]},
                        conn=conn,
                    )
                await msg.edit(content=f"{msg.content} Done!")
            else:
                await ctx.send(
                    f"<@{highest_bid[0]}> spent the money in the meantime... Meh!"
                    " Noone gets it then, pah!\nThis incident has been reported and"
                    " they will get banned if it happens again. Cheers!"
                )

            cash = int(hp / 4 / len(raid))  # what da hood gets per survivor
            users = [u.id for u in raid]
            await self.bot.pool.execute(
                'UPDATE profile SET money=money+$1 WHERE "user"=ANY($2);',
                cash,
                users,
            )
            await ctx.send(
                f"**Gave ${cash} of the hamburger's ${int(hp / 4)} drop to all"
                " survivors!**"
            )

        else:
            await ctx.send(
                "The raid did not manage to kill the hamburger within 45 Minutes... He"
                " disappeared!"
            )

        await asyncio.sleep(30)
        await ctx.channel.set_permissions(
            ctx.guild.default_role,
            overwrite=self.deny_sending,
        )
        await self.clear_raid_timer()
        self.boss = None

    @is_god()
    @raid_free()
    @commands.command(hidden=True, brief=_("Starts Astraea's trial"))
    async def goodspawn(self, ctx):
        """[Astraea only] Starts a Trial."""
        await self.set_raid_timer()

        try:

            view = JoinView(
                Button(style=ButtonStyle.primary, label="Join the trial!"),
                message=_("You joined the trial."),
                timeout=60 * 15,
            )

            channels = [
                self.bot.get_channel(1154245321451388948),  # This is the current channel where the command was invoked
                self.bot.get_channel(1199300356081995847),  # Replace with the actual channel ID
            ]

            channel1 = self.bot.get_channel(1154245321451388948)
            channel2 = self.bot.get_channel(1199300356081995847)
            role_id1 = 1153887457775980566
            role_id2 = 1199303066227331163

            if channel1:
                role1 = ctx.guild.get_role(role_id1)
                if role1:
                    await channel1.send(content=f"{role1.mention}", allowed_mentions=discord.AllowedMentions(roles=True))

            if channel2:
                role2 = ctx.guild.get_role(role_id2)
                if role2:
                    await channel2.send(content=f"{role2.mention}", allowed_mentions=discord.AllowedMentions(roles=True))

            # Message content, organized for better formatting
            message_intro = """
            In Athena's grace, embrace the light,
            Seek trials that soothe, heal the blight.
            With kindness as your guiding star,
            Illuminate souls from near and far.
            """

            message_trial = """
            **__Champions of compassion, take your stand.__**
            Trial Begins in 15 minutes
            """

            message_note = """
            **Only followers of Astraea may join.**
            """

            # Create the embed with structured fields
            embed = discord.Embed(
                title="Champions of Compassion",
                color=discord.Color.blue()
            )
            embed.add_field(name="Athena's Blessing", value=message_intro, inline=False)
            embed.add_field(name="Trial Information", value=message_trial, inline=False)
            embed.add_field(name="Notice", value=message_note, inline=False)
            embed.set_footer(text="Prepare your souls for the trials to come.")
            embed.timestamp = discord.utils.utcnow()

            # Attach the file (image)
            file = discord.File("assets/other/lyx.webp", filename="lyx.webp")
            embed.set_image(url="attachment://lyx.webp")

            # Updated helper function to send to both channels and handle file closing issue
            async def send_to_channels(embed=None, content=None, view=None, file_path=None):
                """Helper function to send a message to all channels."""
                for channel in channels:
                    if channel is not None:  # Ensure the channel is valid
                        try:
                            if file_path:
                                file = discord.File(file_path, filename="lyx.webp")
                                await channel.send(embed=embed, content=content, view=view, file=file)
                            else:
                                await channel.send(embed=embed, content=content, view=view)
                        except Exception as e:
                            await ctx.send(f"Failed to send message to {channel.name}: {str(e)}")
                    else:
                        await ctx.send("One of the channels could not be found.")

            # Call this function with file_path
            await send_to_channels(embed=embed, content=None, view=view, file_path="assets/other/lyx.webp")

            # Sending the embed with the file to the channels

            if not self.bot.config.bot.is_beta:
                await asyncio.sleep(300)
                await send_to_channels(content="**Astraea and her Ouroboros will be visible in 10 minutes**")
                await asyncio.sleep(300)
                await send_to_channels(content="**Astraea and her Ouroboros will be visible in 5 minutes**")
                await asyncio.sleep(180)
                await send_to_channels(content="**Astraea and her Ouroboros will be visible in 2 minutes**")
                await asyncio.sleep(60)
                await send_to_channels(content="**Astraea and her Ouroboros will be visible in 1 minute**")
                await asyncio.sleep(30)
                await send_to_channels(content="**Astraea and her Ouroboros will be visible in 30 seconds**")
                await asyncio.sleep(20)
                await send_to_channels(content="**Astraea and her Ouroboros will be visible in 10 seconds**")
            else:
                await asyncio.sleep(300)
                await send_to_channels(content="**Astraea's trial will commence in 10 minutes**")
                await asyncio.sleep(300)
                await send_to_channels(content="**Astraea's trial will commence in 5 minutes**")
                await asyncio.sleep(180)
                await send_to_channels(content="**Astraea's trial will commence in 2 minutes**")
                await asyncio.sleep(60)
                await send_to_channels(content="**Astraea's trial will commence in 1 minute**")
                await asyncio.sleep(30)
                await send_to_channels(content="**Astraea's trial will commence in 30 seconds**")
                await asyncio.sleep(20)
                await send_to_channels(content="**Astraea's trial will commence in 10 seconds**")

            view.stop()

            await send_to_channels(content="**Astraea's trial will commence! Fetch participant data... Hang on!**")

            async with self.bot.pool.acquire() as conn:
                raid = []
                for u in view.joined:
                    if (
                            not (
                                    profile := await conn.fetchrow(
                                        'SELECT * FROM profile WHERE "user"=$1;', u.id
                                    )
                            )
                            or profile["god"] != "Astraea"
                    ):
                        continue
                    raid.append(u)

            await send_to_channels(content="**Done getting data!**")

            while len(raid) > 1:
                time = random.choice(["day", "night"])
                if time == "day":
                    em = discord.Embed(
                        title="It turns day",
                        description="As the sun's golden rays grace the horizon, a sense of renewal spreads across the "
                                    "land. The world awakens from its slumber, bathed in warmth and hope.",
                        colour=0xFFB900,
                    )
                else:
                    em = discord.Embed(
                        title="It turns night",
                        description="The world embraces the embrace of the night, shrouded in mystery and quietude. The "
                                    "stars twinkle like distant promises, and the nocturnal creatures begin their "
                                    "whispered symphony.",
                        colour=0xFFB900,
                    )
                em.set_thumbnail(url=f"{self.bot.BASE_URL}/image/lyx.png")
                await send_to_channels(embed=em)
                await asyncio.sleep(5)
                target = random.choice(raid)
                if time == "day":
                    event = random.choice(
                        [
                            {
                                "text": "Extend a Healing Hand",
                                "win": 80,
                                "win_text": "Your compassionate efforts have brought healing and solace. Astraea smiles "
                                            "upon you.",
                                "lose_text": "Despite your intentions, your healing touch falters. Astraea's grace eludes "
                                             "you.",
                            },
                            {
                                "text": "Ease Emotional Burdens",
                                "win": 50,
                                "win_text": "Through your empathetic words, you mend fractured souls. Astraea's favor "
                                            "shines on you.",
                                "lose_text": "Your words fall short, unable to mend the hearts before you. Astraea's "
                                             "blessing slips away.",
                            },
                            {
                                "text": "Kindness in Action",
                                "win": 60,
                                "win_text": "Your selfless actions spread ripples of kindness. Astraea's radiant gaze "
                                            "embraces you.",
                                "lose_text": "Your attempts at kindness don't fully resonate. Astraea's warmth remains "
                                             "distant.",
                            },
                        ]
                    )
                else:
                    event = random.choice(
                        [
                            {
                                "text": "Guiding Light of Compassion",
                                "win": 30,
                                "win_text": "Amidst the tranquil night, your compassion brings light to dark corners. "
                                            "Astraea's approval graces you.",
                                "lose_text": "Your efforts to bring solace in the night are met with challenges. Astraea's "
                                             "light evades you.",
                            },
                            {
                                "text": "Healing Moon's Embrace",
                                "win": 45,
                                "win_text": "Under the moon's serenity, your healing touch is magnified. Astraea's "
                                            "presence envelops you.",
                                "lose_text": "Your attempts to heal are hindered by unseen forces. Astraea's touch remains "
                                             "elusive.",
                            },
                            {
                                "text": "Celestial Blessing of Serenity",
                                "win": 20,
                                "win_text": "As the stars align in your favor, Astraea's serene blessings envelop you. A "
                                            "tranquil aura emanates from your being, soothing all around.",
                                "lose_text": "Despite your efforts to channel the cosmos, Astraea's tranquility eludes "
                                             "you, leaving only fleeting traces of its presence.",
                            },
                            {
                                "text": "Stellar Harmonies of Renewal",
                                "win": 20,
                                "win_text": "In harmony with the celestial melodies, your actions resonate with Astraea's "
                                            "essence. The stars themselves seem to sing your praises, infusing the air "
                                            "with renewal.",
                                "lose_text": "The cosmic harmonies remain elusive, and your attempts to align with "
                                             "Astraea's melody falter, leaving a sense of missed opportunity in the "
                                             "night's chorus.",
                            }
                        ]
                    )
                does_win = event["win"] >= random.randint(1, 100)
                if does_win:
                    text = event["win_text"]
                else:
                    text = event["lose_text"]
                    raid.remove(target)
                em = discord.Embed(
                    title=event["text"],
                    description=text,
                    colour=0xFFB900,
                )
                em.set_author(name=f"{target}", icon_url=target.display_avatar.url)
                em.set_footer(text=f"{len(raid)} followers remain")
                em.set_thumbnail(url=f"{self.bot.BASE_URL}/image/lyx.png")
                await send_to_channels(embed=em)
                await asyncio.sleep(5)

            winner = raid[0]
            async with self.bot.pool.acquire() as conn:
                # Fetch the luck value for the specified user (winner)
                luck_query = await conn.fetchval(
                    'SELECT luck FROM profile WHERE "user" = $1;',
                    winner.id,
                )

            # Convert luck_query to float
            luck_query_float = float(luck_query)

            # Perform the multiplication
            weightdivine = 0.20 * luck_query_float

            # Round to the nearest .000
            rounded_weightdivine = round(weightdivine, 3)

            options = ['legendary', 'fortune', 'divine']
            weights = [0.40, 0.40, rounded_weightdivine]

            crate = randomm.choices(options, weights=weights)[0]

            await send_to_channels(
                content=f"In the divine radiance of Astraea, {winner.mention} ascends to the cosmic realm. Guided by the "
                        f"goddess's embrace, they uncover a celestial treasure—an enigmatic, {crate} crate adorned with "
                        f"stardust among the constellations."
            )

            # Update the profile and clear the raid timer
            async with self.bot.pool.acquire() as conn:
                await conn.execute(
                    f'UPDATE profile SET "crates_{crate}" = "crates_{crate}" + 1 WHERE "user" = $1;',
                    winner.id,
                )

            await self.clear_raid_timer()
        except Exception as e:
            import traceback
            error_message = f"Error occurred: {e}\n"
            error_message += traceback.format_exc()
            await ctx.send(error_message)
            print(error_message)

    async def get_player_decision(self, player, options, role, prompt=None, embed=None):
        """
        Sends a prompt or embed with options to the player and returns their decision.
        :param player: The player to wait for a response from.
        :param options: The list of available options.
        :param role: The role of the player (follower, champion, or priest).
        :param prompt: (Optional) The message to display.
        :param embed: (Optional) The embed to send.
        :return: The player's chosen option or the default action based on the role if they don't respond in time.
        """

        view = DecisionView(player, options)

        if embed:
            message = await player.send(embed=embed, view=view)
        else:
            message = await player.send(prompt + "\n\n" + "\n".join(options), view=view)

        await view.wait()

        if view.value:
            return view.value
        else:
            # Return default action based on role in case of timeout
            default_actions = {
                "follower": "Chant",
                "champion": "Smite",  # Assuming you want to default to "Smite" for the champion, you can adjust this
                "priest": "Bless"
            }
            await player.send(f"You took too long to decide. Defaulting to '{default_actions[role]}'.")
            return default_actions[role]

    @is_god()
    @raid_free()
    @commands.command(hidden=True, brief=_("Start an Infernal Ritual raid"))
    async def evilspawn(self, ctx):
        """[Evil God only] Starts a raid."""

        try:

            await self.set_raid_timer()

            view = JoinView(
                Button(style=ButtonStyle.primary, label="Join the dark ritual!"),
                message=_("You have joined the ritual."),
                timeout=60 * 15,
            )

            embed = Embed(
                title="A Sacred Temple Emerges",
                description="""
            A sacred temple emerges, filled with divine power. The dark followers are called upon to weaken this temple and bring forth the darkness.
            This temple's divine power will be vulnerable in 15 Minutes

            **Only followers of the Sepulchure may join.**
                """,
                color=0x901C1C  # You can change the color as you like
            )

            # Use an image URL instead of a local file
            image_url = "https://i.ibb.co/Yf6q0K4/OIG-15.png"
            embed.set_image(url=image_url)

            await ctx.send(embed=embed, view=view)

            await ctx.send(
                "Note: This mode may need improvements and is considered **BETA**. There may be issues, too easy or unfair.")

            if self.bot.config.bot.is_beta:
                await asyncio.sleep(300)
                await ctx.send("**The temple's defense mechanisms will activate in 10 minutes**")
                await asyncio.sleep(300)
                await ctx.send("**The temple's defense mechanisms will activate in 5 minutes**")
                await asyncio.sleep(180)
                await ctx.send("**The temple's defense mechanisms will activate in 2 minutes**")
                await asyncio.sleep(60)
                await ctx.send("**The temple's defense mechanisms will activate in 1 minute**")
                await asyncio.sleep(60)
                await ctx.send("**The temple's defense mechanisms will activate in 30 seconds**")
                await asyncio.sleep(20)
                await ctx.send("**The temple's defense mechanisms will activate in 10 seconds**")
                await asyncio.sleep(10)
            else:
                await asyncio.sleep(300)
                await ctx.send("**The temple's defense mechanisms will activate in 10 minutes**")
                await asyncio.sleep(300)
                await ctx.send("**The temple's defense mechanisms will activate in 5 minutes**")
                await asyncio.sleep(180)
                await ctx.send("**The temple's defense mechanisms will activate in 2 minutes**")
                await asyncio.sleep(60)
                await ctx.send("**The temple's defense mechanisms will activate in 1 minute**")
                await asyncio.sleep(60)
                await ctx.send("**The temple's defense mechanisms will activate in 30 seconds**")
                await asyncio.sleep(20)
                await ctx.send("**The temple's defense mechanisms will activate in 10 seconds**")
                await asyncio.sleep(10)
                await ctx.send("**The temple's defense mechanisms will activate in 30 seconds**")
                await asyncio.sleep(20)
                await ctx.send("**The temple's defense mechanisms will activate in 10 seconds**")

            view.stop()

            await ctx.send(
                "**The temple's defenses are now active! Fetching participant data... Hang on!**"
            )

            raid = {}
            progress = 0

            def progress_bar(current, total, bar_length=10):
                progress = (current / total)
                arrow = '▶️'
                space = '⬜'
                num_of_arrows = int(progress * bar_length)
                return arrow * num_of_arrows + space * (bar_length - num_of_arrows)

            async with self.bot.pool.acquire() as conn:
                for u in view.joined:
                    if (
                            not (
                                    profile := await conn.fetchrow(
                                        'SELECT * FROM profile WHERE "user"=$1;', u.id
                                    )
                            )
                            or profile["god"] != "Sepulchure"
                    ):
                        continue
                    try:
                        dmg, deff = await self.bot.get_raidstats(
                            u,
                            atkmultiply=profile["atkmultiply"],
                            defmultiply=profile["defmultiply"],
                            classes=profile["class"],
                            race=profile["race"],
                            guild=profile["guild"],
                            god=profile["god"],
                            conn=conn,
                        )
                    except ValueError:
                        continue
                    raid[u] = {"hp": 250, "armor": deff, "damage": dmg}

            async def is_valid_participant(user, conn):
                # Here, for example, we're checking if the user is a follower of "Sepulchure"
                profile = await conn.fetchrow('SELECT * FROM profile WHERE "user"=$1;', user.id)
                if profile and profile["god"] == "Sepulchure":
                    return True
                return False

            await ctx.send("**Done getting data!**")
            embed_message_id = None
            async with self.bot.pool.acquire() as conn:
                participants = [u for u in view.joined if await is_valid_participant(u, conn)]

            champion = random.choice(participants)
            participants.remove(champion)

            priest = random.choice(participants) if participants else None
            if priest:
                participants.remove(priest)

            followers = participants

            announcement_color = 0x901C1C
            champion_embed = discord.Embed(
                title="👑 Champion Announcement 👑",
                description=f"{champion.mention} has been chosen as the champion!",
                color=announcement_color
            )
            await ctx.send(embed=champion_embed)
            if priest:
                priest_embed = discord.Embed(
                    title="🌟 Priest Announcement 🌟",
                    description=f"{priest.mention} has been chosen as the priest!",
                    color=announcement_color
                )
                await ctx.send(embed=priest_embed)
            # Generate a list of follower mentions
            follower_mentions = "\n".join(f"{follower.mention}" for follower in followers)

            follower_embed = discord.Embed(
                title="🔥 Followers 🔥",
                description=follower_mentions,
                color=announcement_color
            )
            await ctx.send(embed=follower_embed)

            # Common Embed Color for the Ritual Theme
            EVIL_RITUAL_COLOR = discord.Color.dark_red()

            # General Ritual Embed
            ritual_embed_help = discord.Embed(
                title="🌌 The Dark Ritual Begins 🌌",
                description=("The night has come, and the ritual is about to start. "
                             "Work together to ensure its successful completion, "
                             "but remember, darkness is not without its perils..."),
                color=EVIL_RITUAL_COLOR
            )
            ritual_embed_help.add_field(name="Warning",
                                        value="Should the Champion fall, the ritual ends in failure. Protect them at all costs!")

            # Champion Embed
            champion_embed_help = discord.Embed(
                title="🛡️ Role: Champion 🛡️",
                description=("As the heart of the ritual, you are its beacon and its shield. "
                             "Your survival is key. If you fall, darkness fades and the ritual fails."),
                color=EVIL_RITUAL_COLOR
            )
            champion_embed_help.add_field(name="⚔️ Smite", value="Strike at those who dare oppose the ritual.",
                                          inline=False)
            champion_embed_help.add_field(name="❤️ Mend", value="Call upon the dark energies to mend your wounds.",
                                          inline=False)
            champion_embed_help.add_field(name="🌀 Accelerate",
                                          value="Force the ritual to progress quicker at your command.",
                                          inline=False)

            # Followers Embed
            followers_embed_help = discord.Embed(
                title="🔮 Role: Followers 🔮",
                description="Your energy and devotion fuel the ritual. Assist the Champion and Priest in any way possible.",
                color=EVIL_RITUAL_COLOR
            )
            followers_embed_help.add_field(name="🌌 Enhance Ritual", value="Pour your energy to hasten the ritual.",
                                           inline=False)
            followers_embed_help.add_field(name="🛡️ Shield Champion",
                                           value="Protect the Champion using your collective might.", inline=False)
            followers_embed_help.add_field(name="💥 Empower Priest",
                                           value="Augment the Priest's dark spells for a turn.",
                                           inline=False)
            followers_embed_help.add_field(name="🔥 Weaken Guardian",
                                           value="Sap the strength of any opposing guardians.",
                                           inline=False)
            followers_embed_help.add_field(name="🎵 Dark Chant",
                                           value="Intensify the power of the ritual with your chants.",
                                           inline=False)

            # Priest Embed
            priest_embed_help = discord.Embed(
                title="🌙 Role: Priest 🌙",
                description="Harness the forbidden magics to assist or devastate. Your spells can tip the balance.",
                color=EVIL_RITUAL_COLOR
            )
            priest_embed_help.add_field(name="🔥 Dark Blessing", value="Augment the Champion's might with black magic.",
                                        inline=False)
            priest_embed_help.add_field(name="🔮 Ethereal Barrier",
                                        value="Protect the Champion with an unworldly shield.",
                                        inline=False)
            priest_embed_help.add_field(name="😵 Hex", value="Curse the Guardians, making them feeble and weak.",
                                        inline=False)

            # You can then send these embeds to the main chat or to the respective players.
            await ctx.send(embed=ritual_embed_help)

            # DM the champion the instructions
            await champion.send(embed=champion_embed_help)

            # DM the priest the instructions if they exist
            if priest:
                await priest.send(embed=priest_embed_help)

            # DM the followers the instructions
            for follower in followers:
                await follower.send(embed=followers_embed_help)

            # Turn-based logic
            TOTAL_TURNS = 25

            CHAMPION_ABILITIES = {
                "Smite": "Strike the guardians with divine power.",
                "Heal": "Heal yourself.",
                "Haste": "Boost the ritual's progress but remain exposed next turn."
            }
            default_champion_damage = 750
            champion_stats = {
                "hp": 1500,
                "damage": default_champion_damage,
                "protection": False,  # No protection at the start
                "shield_points": 0,  # No shield points at the start
                "barrier active": False,  # Assuming no active barrier at the start
                "max_hp": 1500,  # Maximum allowable HP
                "healing_rate": 200  # Hypothetical amount champion heals for; adjust as needed
            }

            guardians_stats = {
                "hp": 5000,  # Assuming a default value; adjust as necessary
                "cursed": False,  # Assuming the guardian isn't cursed at the start
                "damage_multiplier": 1.0,  # Default damage multiplier
                "shield active": False,  # No active shield at the start
                "base_damage": 150,  # Hypothetical base damage; adjust as needed
                "regeneration_rate": 500  # Amount of HP restored by "regenerate" action; adjust as needed
            }

            guardians_down_turns = 0
            TIMEOUT = 90
            priest_stats = {
                "healing boost": 1.0  # Default value; adjust if needed
            }

            for turn in range(TOTAL_TURNS):

                if priest:
                    decision_embed = discord.Embed(
                        title="🌠 Decision Time! 🌠",
                        description=f"{priest.mention}, it's your sacred duty to guide the ritual. Make your choice wisely:",
                        color=discord.Color.gold()  # Golden color for the priest
                    )

                    # Using emojis for added flair
                    decision_embed.add_field(name="🙏 Bless", value="Boost the Champion's power", inline=True)
                    decision_embed.add_field(name="🔮 Barrier", value="Protect the Champion", inline=True)
                    decision_embed.add_field(name="🔥 Curse", value="Weaken the guardians", inline=True)

                    # Add a footer to the embed
                    decision_embed.set_footer(
                        text="Time is of the essence. The fate of the ritual rests in your hands!")

                    await ctx.send(f"It's {priest.mention}'s turn to make a decision, check DMs!")

                    try:
                        priest_decision = await asyncio.wait_for(
                            self.get_player_decision(
                                player=priest,
                                options=["Bless", "Barrier", "Curse"],
                                role="priest",
                                embed=decision_embed
                            ),
                            timeout=TIMEOUT
                        )

                        if priest_decision == "Bless":
                            champion_stats["damage"] += 200 * priest_stats["healing boost"]
                        elif priest_decision == "Barrier":
                            champion_stats["barrier active"] = True
                        elif priest_decision == "Curse":
                            guardians_stats["cursed"] = True
                    except asyncio.TimeoutError:
                        await ctx.send(f"{priest.mention} took too long! Moving on...")

                # Guardian's decisions
                await ctx.send(f"It's the Guardian's turn to make a decision.")

                # Enhanced Guardian's Decision-making
                if progress >= 80:
                    guardian_decision = "purify"
                elif guardians_stats.get("cursed"):
                    if guardians_stats.get("hp") <= 4500:
                        guardian_decision = random.choice(
                            ["strike", "purify", "regenerate"])  # Add chance to regenerate if cursed
                    else:
                        guardian_decision = random.choice(
                            ["strike", "purify"])  # Add chance to regenerate if cursed
                elif champion_stats.get("barrier active"):
                    guardian_decision = random.choice(["purify", "counter"])  # Add chance to counter barrier
                else:
                    guardian_decision = random.choice(["strike", "shield", "purify"])

                if guardian_decision == "strike":
                    damage = random.randint(100, 250)
                    if champion_stats.get("barrier active"):
                        damage = int(damage * 0.5)
                        del champion_stats["barrier active"]
                    champion_stats["hp"] -= damage
                elif guardian_decision == "shield":
                    guardians_stats["shield active"] = True
                elif guardian_decision == "purify":
                    progress = max(0, progress - 10)
                elif guardian_decision == "regenerate":  # New regenerate action
                    guardians_stats["hp"] += 500
                elif guardian_decision == "counter":  # New counter action
                    champion_stats["damage"] = default_champion_damage  # Reset champion damage

                # Negative Effects of Curse: Guardian's moves become less effective without the curse
                if not guardians_stats.get("cursed"):
                    guardians_stats["damage_multiplier"] = 1.0  # Increase damage output by 50% if not cursed
                else:
                    guardians_stats["damage_multiplier"] = 0.6  # Reset to normal if cursed

                # Followers' decisions
                await ctx.send(f"It's the Followers' turn to make decisions, check DMs!")

                follower_combined_decision = {
                    "Boost Ritual": 0,
                    "Protect Champion": 0,
                    "Empower Priest": 0,
                    "Sabotage Guardian": 0,
                    "Chant": 0
                }

                follower_embed = discord.Embed(
                    title="🛡 Followers' Resolve 🛡",
                    description="Followers, your combined strength can tip the scales of this ritual. Choose your action:",
                    color=discord.Color.purple()  # Purple color for the followers
                )

                # Add abilities with emojis for added flair
                follower_embed.add_field(name="🔆 Boost Ritual", value="Increase the ritual's progress", inline=True)
                follower_embed.add_field(name="🛡️ Protect Champion", value="Provide a shield to the champion",
                                         inline=True)
                follower_embed.add_field(name="🌟 Empower Priest", value="Amplify the priest's next action", inline=True)
                follower_embed.add_field(name="💥 Sabotage Guardian", value="Disrupt the guardian's next move",
                                         inline=True)
                follower_embed.add_field(name="🎶 Chant", value="Contribute to the overall power of the ritual",
                                         inline=True)

                # Add a footer to the embed
                follower_embed.set_footer(
                    text="Your collective decision will shape the fate of the ritual. Act wisely!")

                # Separate function to obtain each follower's decision
                async def get_follower_decision(follower):
                    decision = await self.get_player_decision(
                        player=follower,
                        options=list(follower_combined_decision.keys()),
                        role="follower",
                        embed=follower_embed
                    )
                    return (follower, decision)

                # Prepare a list of tasks to gather
                tasks = [get_follower_decision(follower) for follower in followers]

                # Gather all tasks and wait for their completion
                results = await asyncio.gather(*tasks, return_exceptions=True)

                # Process the results
                for result in results:
                    if isinstance(result, Exception):  # handle exceptions if there are any
                        continue
                    follower, decision = result
                    follower_combined_decision[decision] += 1

                # boost_ritual: Increase ritual progress by the number of followers who chose it
                if follower_combined_decision["Boost Ritual"]:
                    progress += 10

                # protect_champion: If any follower chose it, provide a shield to the champion
                if follower_combined_decision["Protect Champion"] > 0:
                    champion_stats["protection"] = True
                    # Setting a value for the protection, for example, 200 points of shield
                    champion_stats["shield_points"] = 200

                # empower_priest: Let's say this boosts the next priest action by some percentage.
                # The exact logic depends on what the priest's actions are, and how you want this to manifest.
                # As an example, let's say it boosts the priest's healing ability:
                if follower_combined_decision["Empower Priest"] > 0:
                    priest_stats["healing boost"] = 1.2  # Boosts healing by 20% for the next turn

                # sabotage_guardian: This could delay the guardian's next action, weaken it, or have some other effect.
                # Here's an example where it reduces the guardian's damage for the next turn:
                if follower_combined_decision["Sabotage Guardian"] > 0:
                    guardians_stats["damage multiplier"] = 0.8  # Reduces damage output by 20%

                # chant: This could have a varied effect. Let's say it boosts overall progress by a percentage of the chanters:
                progress += 2 * follower_combined_decision["Chant"]  # Add 0.5% progress for each chanter

                # Champion's decisions
                abilities_msg = "\n".join(f"{k}: {v}" for k, v in CHAMPION_ABILITIES.items())
                try:
                    # Champion's decisions
                    champion_embed = discord.Embed(
                        title="⚔️ Champion's Valor ⚔️",
                        description=f"{champion.mention}, as the chosen one, your power will determine the outcome of this ritual. Choose your action:",
                        color=discord.Color.red()  # Red color for the champion
                    )

                    # Add abilities with emojis for added flair
                    champion_embed.add_field(name="⚡ Smite", value="Deal significant damage to the guardians",
                                             inline=True)
                    champion_embed.add_field(name="❤️ Heal", value="Recover some of your lost HP", inline=True)
                    champion_embed.add_field(name="🌀 Haste", value="Boost the ritual's progress", inline=True)

                    # Add a footer to the embed
                    champion_embed.set_footer(text="Your allies rely on your might. Choose wisely and swiftly!")

                    await ctx.send(f"It's {champion.mention}'s turn to make a decision, check DMs!")

                    champion_decision = "Smite"  # default action
                    try:
                        champion_decision = await asyncio.wait_for(
                            self.get_player_decision(
                                player=champion,
                                options=["Smite", "Heal", "Haste"],
                                role="champion",
                                embed=champion_embed
                            ),
                            timeout=TIMEOUT
                        )
                    except asyncio.TimeoutError:
                        await ctx.send(f"{champion.mention} took too long to decide! Defaulting to 'Smite'.")

                    if champion_decision == "Smite":
                        guardians_stats["hp"] -= champion_stats["damage"]
                    elif champion_decision == "Heal":
                        champion_stats["hp"] = min(champion_stats["hp"] + 200, 1500)
                    elif champion_decision == "Haste":
                        progress += 20  # This line is now correctly inside the try block

                except Exception as e:
                    ctx.send(f"An error has occured {e}")

                    # Guardians' reactions post champion's decision

                def apply_damage_with_protection(target_stats, damage):
                    """Apply damage to target taking protection (shield) into consideration."""
                    if "protection" in target_stats and target_stats["protection"]:
                        # Calculate remaining damage after shield absorption
                        damage_after_shield = max(damage - target_stats.get("shield_points", 0), 0)
                        # Update shield points in the target_stats
                        target_stats["shield_points"] = max(target_stats.get("shield_points", 0) - damage, 0)
                    else:
                        damage_after_shield = damage

                    # Apply remaining damage to target's HP
                    target_stats["hp"] -= damage_after_shield

                # Guardian striking the champion
                if guardian_decision == "strike" and guardians_stats.get("damage_multiplier"):
                    damage_to_champion = int(random.randint(100, 200) * guardians_stats["damage_multiplier"])
                    apply_damage_with_protection(champion_stats, damage_to_champion)

                if guardians_down_turns > 0:
                    guardians_down_turns -= 1
                    if guardians_down_turns == 0:
                        guardians_stats["hp"] = 5000
                elif guardians_stats["hp"] <= 0:
                    guardians_down_turns = 2
                else:
                    damage_to_champion = random.randint(100, 200)
                    apply_damage_with_protection(champion_stats, damage_to_champion)
                    if champion_stats["hp"] <= 0:
                        break

                progress += 5

                # Aesthetic improvements for the Ritual Progress embed
                progress_color = 0x4CAF50 if progress >= 80 else 0xFFC107 if progress >= 50 else 0xFF5722
                em = discord.Embed(
                    title="🌌 Ritual Progress 🌌",
                    description=f"Turn {turn + 1}/{TOTAL_TURNS}",
                    color=progress_color
                )
                ritual_status = f"{progress_bar(progress, 100)} ({progress}%)"
                champion_status = f"❤️ {champion_stats['hp']} HP"
                guardians_status = f"🛡️ {guardians_stats['hp']} HP"
                em.add_field(name="<:ritual:1156170252430876692> Ritual Completion <:ritual:1156170252430876692>",
                             value=ritual_status, inline=False)
                em.add_field(name=f"🛡️ {champion.name} 🛡️", value=champion_status, inline=True)
                em.add_field(name="⚔️ Guardians ⚔️", value=guardians_status, inline=True)

                # Display priest and guardian buffs
                if champion_stats.get("damage") > default_champion_damage:
                    em.add_field(name="Priest's Blessing", value="🔥 Champion's power boosted", inline=True)
                if champion_stats.get("barrier_active"):
                    em.add_field(name="Priest's Barrier", value="🔰 Champion Protected", inline=True)
                if guardians_stats.get("cursed"):
                    em.add_field(name="Priest's Curse", value="😵 Guardians Weakened", inline=True)
                if guardians_stats.get("shield_active"):
                    em.add_field(name="Guardians' Shield", value="🔰 Active", inline=True)

                if turn != 0 and embed_message_id:
                    old_message = await ctx.channel.fetch_message(embed_message_id)
                    await old_message.delete()

                message = await ctx.send(embed=em)
                embed_message_id = message.id

                # Decision Summary Embed
                decision_embed = discord.Embed(
                    title="⚔️ Fates Converge ⚔️",
                    description="The forces have made their choices. The ritual's fate hangs in the balance...",
                    color=0x8B0000  # Dark red color
                )

                # Display Priest's Decision
                if priest:
                    priest_action = priest_decision if priest_decision else "No decision"
                    decision_embed.add_field(name=f"🌠 {priest.name} (Priest) 🌠", value=priest_action, inline=False)

                # Display Guardian's Decision
                decision_embed.add_field(name="⚔️ Guardians ⚔️", value=guardian_decision, inline=False)

                # Display Follower's Collective Decision
                followers_decisions = "\n".join(
                    [f"{action}: {count}" for action, count in follower_combined_decision.items()])
                decision_embed.add_field(name="🛡 Followers' Collective Will 🛡", value=followers_decisions, inline=False)

                # Display Champion's Decision
                decision_embed.add_field(name=f"🔥 {champion.name} (Champion) 🔥", value=champion_decision, inline=False)

                # Add a footer for added menace
                decision_embed.set_footer(text="The ritual's energy surges as choices clash...")

                # Send the Decision Summary Embed
                await ctx.send(embed=decision_embed)

                if progress >= 100:
                    break

                # Cleanup: Reset certain states for the next turn
                if guardians_stats.get("cursed"):
                    del guardians_stats["cursed"]
                if guardians_stats.get("damage_multiplier"):
                    del guardians_stats["damage_multiplier"]
                if champion_stats.get("damage") > default_champion_damage:
                    champion_stats["damage"] = default_champion_damage
                if champion_stats.get("protection"):
                    del champion_stats["protection"]

                await asyncio.sleep(15)

            # Post-Raid Outcome
            if progress >= 100:
                progress = 100
                # Create an enhanced embed message

                users = [u.id for u in raid]
                random_user = random.choice(users)
                async with self.bot.pool.acquire() as conn:
                    # Fetch the luck value for the specified user (winner)
                    luck_query = await conn.fetchval(
                        'SELECT luck FROM profile WHERE "user" = $1;',
                        random_user,
                    )

                # Convert luck_query to float
                luck_query_float = float(luck_query)

                # Perform the multiplication
                weightdivine = 0.20 * luck_query_float

                # Round to the nearest .000
                rounded_weightdivine = round(weightdivine, 3)

                options = ['legendary', 'fortune', 'divine']
                weights = [0.40, 0.40, rounded_weightdivine]

                crate = randomm.choices(options, weights=weights)[0]

                embed = Embed(
                    title="The Ritual of Sepulchure",
                    description=f"The night resonates with dark energy as Sepulchure's power reaches its zenith. The ritual has been successfully consummated! As a token of Sepulchure's dark favor, one chosen acolyte will be granted a {crate} crate. The rest, fear not, for riches of the shadow realm shall be your reward.",
                    color=0x901C1C  # Red color;
                )

                # Add the image to the embed
                embed.set_image(url="https://i.ibb.co/G09cMBq/OIG-17.png")

                await ctx.send(embed=embed)
                await ctx.send(
                    f"🦇 Dark tidings, <@{random_user}>! You have been selected as the chosen acolyte, and you shall receive a {crate} crate in the Ritual of Sepulchure. 💀")
                async with self.bot.pool.acquire() as conn:
                    await conn.execute(
                        f'UPDATE profile SET "crates_{crate}" = "crates_{crate}" + 1 WHERE "user" = $1;',
                        random_user,
                    )
                # Reward the participants.
                cash_reward = random.randint(20000, 50000)
                await self.bot.pool.execute(
                    'UPDATE profile SET money=money+$1 WHERE "user"=ANY($2);',
                    cash_reward,
                    users,
                )
                await ctx.send(
                    f"**Distributed ${cash_reward} of the Sepulchure's favor to all"
                    " the loyal followers!**"
                )

            elif champion_stats["hp"] <= 0:
                await ctx.send(f"{champion.mention} has been defeated. The ritual has failed!")

            await self.clear_raid_timer()
        except Exception as e:
            await ctx.send(f"An error occurred: {e}")
            # If you have a logger set up:d

    @is_god()
    @raid_free()
    @commands.command(hidden=True, brief=_("Start a Drakath raid"))
    async def chaosspawn(self, ctx, boss_hp: IntGreaterThan(0)):
        """[Drakath only] Starts a raid."""
        await self.set_raid_timer()

        # Define the channels where the raid messages will be sent
        channels = [
            self.bot.get_channel(1154244627822551060),  # This is the current channel where the command was invoked
            self.bot.get_channel(1199300319256006746),  # Replace with the actual channel ID
        ]

        async def send_to_channels(embed=None, content=None, view=None):
            """Helper function to send a message to all channels."""
            for channel in channels:
                await channel.send(embed=embed, content=content, view=view)

        view = JoinView(
            Button(style=ButtonStyle.primary, label="Join the raid!"),
            message=_("You joined the raid."),
            timeout=60 * 15,
        )

        channel1 = self.bot.get_channel(1154244627822551060)
        channel2 = self.bot.get_channel(1199300319256006746)
        role_id1 = 1153880715419717672
        role_id2 = 1199302687083204649

        if channel1:
            role1 = ctx.guild.get_role(role_id1)
            if role1:
                await channel1.send(content=f"{role1.mention}", allowed_mentions=discord.AllowedMentions(roles=True))

        if channel2:
            role2 = ctx.guild.get_role(role_id2)
            if role2:
                await channel2.send(content=f"{role2.mention}", allowed_mentions=discord.AllowedMentions(roles=True))

        em = discord.Embed(
            title="Raid the Void",
            description=f"""
    In Drakath's name, unleash the storm,
    Raiders of chaos, in shadows swarm.
    No order, no restraint, just untamed glee,
    Drakath's chaos shall set us free.

    Eclipse the Void Conqueror has {boss_hp} HP and will be vulnerable in 15 Minutes

    **Only followers of Drakath may join.**""",
            color=0xFFB900,
        )
        em.set_image(url="https://i.imgur.com/YoszTlc.png")

        # Send the initial raid message and join button to both channels
        await send_to_channels(embed=em, view=view)


        if not self.bot.config.bot.is_beta:
            await asyncio.sleep(300)
            await send_to_channels(content="**The raid on the void will start in 10 minutes**")
            await asyncio.sleep(300)
            await send_to_channels(content="**The raid on the void will start in 5 minutes**")
            await asyncio.sleep(180)
            await send_to_channels(content="**The raid on the void will start in 2 minutes**")
            await asyncio.sleep(60)
            await send_to_channels(content="**The raid on the void will start in 1 minute**")
            await asyncio.sleep(30)
            await send_to_channels(content="**The raid on the void will start in 30 seconds**")
            await asyncio.sleep(20)
            await send_to_channels(content="**The raid on the void will start in 10 seconds**")
        else:
            await asyncio.sleep(300)
            await send_to_channels(content="**The raid on the void will start in 10 minutes**")
            await asyncio.sleep(300)
            await send_to_channels(content="**The raid on the void will start in 5 minutes**")
            await asyncio.sleep(180)
            await send_to_channels(content="**The raid on the void will start in 2 minutes**")
            await asyncio.sleep(60)
            await send_to_channels(content="**The raid on the void will start in 1 minute**")
            await asyncio.sleep(30)
            await send_to_channels(content="**The raid on the void will start in 30 seconds**")
            await asyncio.sleep(20)
            await send_to_channels(content="**The raid on the void will start in 10 seconds**")

        view.stop()

        await send_to_channels(content="**The raid on the facility started! Fetching participant data... Hang on!**")

        async with self.bot.pool.acquire() as conn:
            raid = {}
            for u in view.joined:
                if (
                        not (
                                profile := await conn.fetchrow(
                                    'SELECT * FROM profile WHERE "user"=$1;', u.id
                                )
                        )
                        or profile["god"] != "Drakath"
                ):
                    continue
                raid[u] = 250

        await send_to_channels(content="**Done getting data!**")

        start = datetime.datetime.utcnow()

        while (
                boss_hp > 0
                and len(raid) > 0
                and datetime.datetime.utcnow() < start + datetime.timedelta(minutes=45)
        ):
            target = random.choice(list(raid.keys()))
            dmg = random.randint(100, 300)
            raid[target] -= dmg
            if raid[target] > 0:
                em = discord.Embed(
                    title="Eclipse attacks!",
                    description=f"{target} now has {raid[target]} HP!",
                    colour=0xFFB900,
                )
            else:
                em = discord.Embed(
                    title="Eclipse hits critical!",
                    description=f"{target} died!",
                    colour=0xFFB900,
                )
            em.add_field(name="Damage", value=dmg)
            em.set_author(name=str(target), icon_url=target.display_avatar.url)
            em.set_thumbnail(url="https://i.imgur.com/YS4A6R7.png")
            await send_to_channels(embed=em)
            if raid[target] <= 0:
                del raid[target]
                if len(raid) == 0:
                    break

            if random.randint(1, 5) == 1:
                await asyncio.sleep(4)
                target = random.choice(list(raid.keys()))
                raid[target] += 100
                em = discord.Embed(
                    title=f"{target} uses Chaos Restore!",
                    description=f"It's super effective!\n{target} now has {raid[target]} HP!",
                    colour=0xFFB900,
                )
                em.set_author(name=str(target), icon_url=target.display_avatar.url)
                em.set_thumbnail(url="https://i.imgur.com/md5dWFk.png")
                await send_to_channels(embed=em)

            if random.randint(1, 5) == 1:
                await asyncio.sleep(4)
                if len(raid) >= 3:
                    targets = random.sample(list(raid.keys()), 3)
                else:
                    targets = list(raid.keys())
                for target in targets:
                    raid[target] -= 100
                    if raid[target] <= 0:
                        del raid[target]
                em = discord.Embed(
                    title="Eclipse prepares a void pulse!",
                    description=f"It's super effective!\n{', '.join(str(u) for u in targets)} take 100 damage!",
                    colour=0xFFB900,
                )
                em.set_thumbnail(url="https://i.imgur.com/lDqNHua.png")
                await send_to_channels(embed=em)

            dmg_to_take = sum(
                25 if random.randint(1, 10) != 10 else random.randint(75, 100)
                for u in raid
            )
            boss_hp -= dmg_to_take
            await asyncio.sleep(4)
            em = discord.Embed(
                title="The power of Drakath's Followers attacks Eclipse!", colour=0xFF5C00
            )
            em.set_thumbnail(url="https://i.imgur.com/kf3zcLs.png")
            em.add_field(name="Damage", value=dmg_to_take)
            if boss_hp > 0:
                em.add_field(name="HP left", value=boss_hp)
            else:
                em.add_field(name="HP left", value="Dead!")
            await send_to_channels(embed=em)
            await asyncio.sleep(4)

        if boss_hp > 1 and len(raid) > 0:
            em = discord.Embed(
                title="Defeat",
                description="As Drakath's malevolent laughter echoes through the shattered realm, his followers stand "
                            "defeated before the overwhelming might of their vanquished foe, a stark reminder of "
                            "chaos's unyielding and capricious nature.",
                color=0xFFB900,
            )
            em.set_image(url="https://i.imgur.com/s5tvHMd.png")
            await send_to_channels(embed=em)
            await self.clear_raid_timer()
        elif len(raid) == 0:
            em = discord.Embed(
                title="Defeat",
                description="Amidst the smoldering ruins and the mocking whispers of the chaotic winds, Drakath's "
                            "followers find themselves humbled by the boss's insurmountable power, their hopes dashed "
                            "like shattered illusions in the wake of their failure.",
                color=0xFFB900,
            )
            em.set_image(url="https://i.imgur.com/UpWW3fF.png")
            await send_to_channels(embed=em)
            await self.clear_raid_timer()
        else:
            winner = random.choice(list(raid.keys()))
            try:
                async with self.bot.pool.acquire() as conn:
                    luck_query = await conn.fetchval(
                        'SELECT luck FROM profile WHERE "user" = $1;',
                        winner.id,
                    )

                luck_query_float = float(luck_query)
                weightdivine = 0.20 * luck_query_float
                rounded_weightdivine = round(weightdivine, 3)

                options = ['legendary', 'fortune', 'divine']
                weights = [0.40, 0.40, rounded_weightdivine]

                crate = randomm.choices(options, weights=weights)[0]

                try:
                    async with self.bot.pool.acquire() as conn:
                        await conn.execute(
                            f'UPDATE profile SET "crates_{crate}" = "crates_{crate}" + 1 WHERE "user" = $1;',
                            winner.id,
                        )

                except Exception as e:
                    print(f"An error occurred: {e}")

                em = discord.Embed(
                    title="Win!",
                    description=f"The forces aligned with Drakath have triumphed over Eclipse, wresting victory from the "
                                f"clutches of chaos itself!\n{winner.mention} emerges as a true champion of anarchy, "
                                f"earning a {crate}) crate from Drakath as a token of recognition for their unrivaled "
                                f"prowess!",
                    color=0xFFB900,
                )
                em.set_thumbnail(url="https://i.imgur.com/3pg9Msj.png")
                em.set_image(url="https://i.imgur.com/s5tvHMd.png")
                em.add_field(name="Crate Found", value=crate)
                await send_to_channels(embed=em)
                await self.clear_raid_timer()
            except Exception:
                em = discord.Embed(
                    title="Win!",
                    description=f"The forces aligned with Drakath have triumphed over Eclipse, wresting victory from the "
                                f"clutches of chaos itself!\n{winner.mention} emerges as a true champion of anarchy, "
                                f"earning a {crate}) crate from Drakath as a token of recognition for their unrivaled "
                                f"prowess!",
                    color=0xFFB900,
                )
                em.set_thumbnail(url="https://i.imgur.com/3pg9Msj.png")
                em.set_image(url="https://i.imgur.com/s5tvHMd.png")
                await send_to_channels(embed=em)
                await self.clear_raid_timer()

    @commands.command()
    async def joinraid(self, ctx):
        if not self.raidactive:
            await ctx.send("No active raid to join right now!")
            return

        if ctx.author not in self.joined:
            self.joined.append(ctx.author)
            await ctx.send(f"{ctx.author.mention} has joined the raid!")
        else:
            await ctx.send(f"{ctx.author.mention}, you've already joined the raid!")

    @is_god()
    @raid_free()
    @commands.command(hidden=True, brief=_("Start a Kirby raid"))
    async def kirbycultspawn(self, ctx, hp: IntGreaterThan(0)):
        """[Kirby only] Starts a raid."""
        await self.set_raid_timer()
        self.boss = {"hp": hp, "min_dmg": 200, "max_dmg": 300}

        view = JoinView(
            Button(style=ButtonStyle.primary, label="Join the raid!"),
            message=_("You joined the raid."),
            timeout=60 * 15,
        )

        em = discord.Embed(
            title="Dark Mind attacks Dream Land",
            description=f"""
**A great curse has fallen upon Dream Land! Dark Mind is trying to conquer Dream Land and absorb it to the Mirror World! Join forces and defend Dream Land!**

This boss has {self.boss['hp']} HP and will be vulnerable in 15 Minutes

**Only followers of Kirby may join.**""",
            color=0xFFB900,
        )
        em.set_image(url=f"{self.bot.BASE_URL}/image/dark_mind.png")
        await ctx.send(embed=em, view=view)

        if not self.bot.config.bot.is_beta:
            await asyncio.sleep(300)
            await ctx.send("**The attack on Dream Land will start in 10 minutes**")
            await asyncio.sleep(300)
            await ctx.send("**The attack on Dream Land will start in 5 minutes**")
            await asyncio.sleep(180)
            await ctx.send("**The attack on Dream Land will start in 2 minutes**")
            await asyncio.sleep(60)
            await ctx.send("**The attack on Dream Land will start in 1 minute**")
            await asyncio.sleep(30)
            await ctx.send("**The attack on Dream Land will start in 30 seconds**")
            await asyncio.sleep(20)
            await ctx.send("**The attack on Dream Land will start in 10 seconds**")
            await asyncio.sleep(10)
        else:
            await asyncio.sleep(60)

        view.stop()

        await ctx.send(
            "**The attack on Dream Land started! Fetching participant data... Hang on!**"
        )

        async with self.bot.pool.acquire() as conn:
            raid = {}
            for u in view.joined:
                if (
                        not (
                                profile := await conn.fetchrow(
                                    'SELECT * FROM profile WHERE "user"=$1;', u.id
                                )
                        )
                        or profile["god"] != "Kirby"
                ):
                    continue
                try:
                    dmg, deff = await self.bot.get_raidstats(u, god="Kirby", conn=conn)
                except ValueError:
                    continue
                raid[u] = {"hp": 250, "armor": deff, "damage": dmg}

        await ctx.send("**Done getting data!**")

        start = datetime.datetime.utcnow()

        while (
                self.boss["hp"] > 0
                and len(raid) > 0
                and datetime.datetime.utcnow() < start + datetime.timedelta(minutes=10)
        ):
            target = random.choice(list(raid.keys()))
            dmg = random.randint(self.boss["min_dmg"], self.boss["max_dmg"])
            dmg = self.getfinaldmg(dmg, raid[target]["armor"])
            raid[target]["hp"] -= dmg
            if raid[target]["hp"] > 0:
                em = discord.Embed(
                    title="Dark Mind attacked!",
                    description=f"{target} now has {raid[target]['hp']} HP!",
                    colour=0xFFB900,
                )
            else:
                em = discord.Embed(
                    title="Dark Mind attacked!",
                    description=f"{target} died!",
                    colour=0xFFB900,
                )
            em.add_field(name="Theoretical Damage", value=dmg + raid[target]["armor"])
            em.add_field(name="Shield", value=raid[target]["armor"])
            em.add_field(name="Effective Damage", value=dmg)
            em.set_author(name=str(target), icon_url=target.display_avatar.url)
            em.set_thumbnail(url=f"{self.bot.BASE_URL}/image/dark_mind.png")
            await ctx.send(embed=em)
            if raid[target]["hp"] <= 0:
                del raid[target]
            dmg_to_take = sum(i["damage"] for i in raid.values())
            self.boss["hp"] -= dmg_to_take
            await asyncio.sleep(4)
            em = discord.Embed(title="The raiders attacked Dark Mind!", colour=0xFF5C00)
            em.set_thumbnail(url=f"{self.bot.BASE_URL}/image/kirby_raiders.png")
            em.add_field(name="Damage", value=dmg_to_take)
            if self.boss["hp"] > 0:
                em.add_field(name="HP left", value=self.boss["hp"])
            else:
                em.add_field(name="HP left", value="Dead!")
            await ctx.send(embed=em)
            await asyncio.sleep(4)

        if len(raid) == 0:
            em = discord.Embed(
                title="Defeat!",
                description="Dark Mind was too strong! You cannot stop him from conquering Dream Land as he ushers in a dark period of terror and tyranny!",
                color=0xFFB900,
            )
            em.set_image(url=f"{self.bot.BASE_URL}/image/kirby_loss.png")
            await self.clear_raid_timer()
            return await ctx.send(embed=em)
        elif self.boss["hp"] > 0:
            em = discord.Embed(
                title="Timed out!",
                description="You took too long! The mirror world has successfully absorbed Dream Land and it is lost forever.",
                color=0xFFB900,
            )
            em.set_image(url=f"{self.bot.BASE_URL}/image/kirby_timeout.png")
            await self.clear_raid_timer()
            return await ctx.send(embed=em)
        em = discord.Embed(
            title="Win!",
            description="Hooray! Dream Land is saved!",
            color=0xFFB900,
        )
        em.set_image(url=f"{self.bot.BASE_URL}/image/kirby_win.png")
        await ctx.send(embed=em)
        await asyncio.sleep(5)
        em = discord.Embed(
            title="Dark Mind returns!",
            description="Oh no! Dark Mind is back in his final form, stronger than ever before! Defeat him once and for all to protect Dream Land!",
            color=0xFFB900,
        )
        em.set_image(url=f"{self.bot.BASE_URL}/image/kirby_return.png")
        await ctx.send(embed=em)
        await asyncio.sleep(5)

        self.boss = {"hp": hp, "min_dmg": 300, "max_dmg": 400}
        while self.boss["hp"] > 0 and len(raid) > 0:
            target = random.choice(list(raid.keys()))
            dmg = random.randint(self.boss["min_dmg"], self.boss["max_dmg"])
            dmg = self.getfinaldmg(dmg, raid[target]["armor"])
            raid[target]["hp"] -= dmg
            if raid[target]["hp"] > 0:
                em = discord.Embed(
                    title="Dark Mind attacked!",
                    description=f"{target} now has {raid[target]['hp']} HP!",
                    colour=0xFFB900,
                )
            else:
                em = discord.Embed(
                    title="Dark Mind attacked!",
                    description=f"{target} died!",
                    colour=0xFFB900,
                )
            em.add_field(name="Theoretical Damage", value=dmg + raid[target]["armor"])
            em.add_field(name="Shield", value=raid[target]["armor"])
            em.add_field(name="Effective Damage", value=dmg)
            em.set_author(name=str(target), icon_url=target.display_avatar.url)
            em.set_thumbnail(url=f"{self.bot.BASE_URL}/image/dark_mind_final.png")
            await ctx.send(embed=em)
            if raid[target]["hp"] <= 0:
                del raid[target]
            dmg_to_take = sum(i["damage"] for i in raid.values())
            self.boss["hp"] -= dmg_to_take
            await asyncio.sleep(4)
            em = discord.Embed(title="The raiders attacked Dark Mind!", colour=0xFF5C00)
            em.set_thumbnail(url=f"{self.bot.BASE_URL}/image/kirby_raiders.png")
            em.add_field(name="Damage", value=dmg_to_take)
            if self.boss["hp"] > 0:
                em.add_field(name="HP left", value=self.boss["hp"])
            else:
                em.add_field(name="HP left", value="Dead!")
            await ctx.send(embed=em)
            await asyncio.sleep(4)

        if self.boss["hp"] > 0:
            em = discord.Embed(
                title="Defeat!",
                description="Dark Mind was too strong! You cannot stop him from conquering Dream Land as he ushers in a dark period of terror and tyranny!",
                color=0xFFB900,
            )
            em.set_image(url=f"{self.bot.BASE_URL}/image/kirby_loss.png")
            await self.clear_raid_timer()
            return await ctx.send(embed=em)
        winner = random.choice(list(raid.keys()))
        em = discord.Embed(
            title="Win!",
            description=f"Hooray! Dark Mind is defeated and his dream of conquering Dream Land is shattered. You return back to Dream Land to Cappy Town where you are met with a huge celebration! The Mayor gives {winner.mention} a Legendary Crate for your bravery!\n**Gave $10000 to each survivor**",
            color=0xFFB900,
        )
        em.set_image(url=f"{self.bot.BASE_URL}/image/kirby_final_win.png")
        await ctx.send(embed=em)

        users = [u.id for u in raid]

        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                'UPDATE profile SET "crates_legendary"="crates_legendary"+1 WHERE "user"=$1;',
                winner.id,
            )
            await conn.execute(
                'UPDATE profile SET "money"="money"+$1 WHERE "user"=ANY($2);',
                10000,
                users,
            )
        await self.clear_raid_timer()

    @is_god()
    @raid_free()
    @commands.command(hidden=True, brief=_("Start a Jesus raid"))
    async def jesusspawn(self, ctx, hp: IntGreaterThan(0)):
        """[Jesus only] Starts a raid."""
        await self.set_raid_timer()
        self.boss = {"hp": hp, "min_dmg": 100, "max_dmg": 500}
        await ctx.channel.set_permissions(
            ctx.guild.default_role,
            overwrite=self.read_only,
        )

        view = JoinView(
            Button(style=ButtonStyle.primary, label="Join the raid!"),
            message=_("You joined the raid."),
            timeout=60 * 15,
        )

        await ctx.send(
            f"""
**Atheistus the Tormentor has returned to earth to punish humanity for their belief.**

This boss has {self.boss['hp']} HP and has high-end loot!
Atheistus will be vulnerable in 15 Minutes

**Only followers of Jesus may join.**""",
            file=discord.File("assets/other/atheistus.webp"),
            view=view,
        )

        if not self.bot.config.bot.is_beta:
            await asyncio.sleep(300)
            await ctx.send("**Atheistus will be vulnerable in 10 minutes**")
            await asyncio.sleep(300)
            await ctx.send("**Atheistus will be vulnerable in 5 minutes**")
            await asyncio.sleep(180)
            await ctx.send("**Atheistus will be vulnerable in 2 minutes**")
            await asyncio.sleep(60)
            await ctx.send("**Atheistus will be vulnerable in 1 minute**")
            await asyncio.sleep(30)
            await ctx.send("**Atheistus will be vulnerable in 30 seconds**")
            await asyncio.sleep(20)
            await ctx.send("**Atheistus will be vulnerable in 10 seconds**")
            await asyncio.sleep(10)
        else:
            await asyncio.sleep(60)

        view.stop()

        await ctx.send(
            "**Atheistus is vulnerable! Fetching participant data... Hang on!**"
        )

        async with self.bot.pool.acquire() as conn:
            raid = {}
            for u in view.joined:
                if (
                        not (
                                profile := await conn.fetchrow(
                                    'SELECT * FROM profile WHERE "user"=$1;', u.id
                                )
                        )
                        or profile["god"] != "Jesus"
                ):
                    continue
                try:
                    dmg, deff = await self.bot.get_raidstats(u, god="Jesus", conn=conn)
                except ValueError:
                    continue
                raid[u] = {"hp": 250, "armor": deff, "damage": dmg}

        await ctx.send("**Done getting data!**")

        start = datetime.datetime.utcnow()

        while (
                self.boss["hp"] > 0
                and len(raid) > 0
                and datetime.datetime.utcnow() < start + datetime.timedelta(minutes=45)
        ):
            target = random.choice(list(raid.keys()))
            dmg = random.randint(self.boss["min_dmg"], self.boss["max_dmg"])
            dmg = self.getfinaldmg(dmg, raid[target]["armor"])
            raid[target]["hp"] -= dmg
            if raid[target]["hp"] > 0:
                em = discord.Embed(
                    title="Atheistus attacked!",
                    description=f"{target} now has {raid[target]['hp']} HP!",
                    colour=0xFFB900,
                )
            else:
                em = discord.Embed(
                    title="Atheistus attacked!",
                    description=f"{target} died!",
                    colour=0xFFB900,
                )
            em.add_field(name="Theoretical Damage", value=dmg + raid[target]["armor"])
            em.add_field(name="Shield", value=raid[target]["armor"])
            em.add_field(name="Effective Damage", value=dmg)
            em.set_author(name=str(target), icon_url=target.display_avatar.url)
            em.set_thumbnail(url=f"{self.bot.BASE_URL}/atheistus.jpg")
            await ctx.send(embed=em)
            if raid[target]["hp"] <= 0:
                del raid[target]
            dmg_to_take = sum(i["damage"] for i in raid.values())
            self.boss["hp"] -= dmg_to_take
            await asyncio.sleep(4)
            em = discord.Embed(title="The raid attacked Atheistus!", colour=0xFF5C00)
            em.set_thumbnail(url=f"{self.bot.BASE_URL}/knight.jpg")
            em.add_field(name="Damage", value=dmg_to_take)
            if self.boss["hp"] > 0:
                em.add_field(name="HP left", value=self.boss["hp"])
            else:
                em.add_field(name="HP left", value="Dead!")
            await ctx.send(embed=em)
            await asyncio.sleep(4)

        if len(raid) == 0:
            await ctx.send("The raid was all wiped!")
        elif self.boss["hp"] < 1:
            await ctx.channel.set_permissions(
                ctx.guild.default_role,
                overwrite=self.allow_sending,
            )
            highest_bid = [
                356_091_260_429_402_122,
                0,
            ]  # userid, amount

            def check(msg):
                try:
                    val = int(msg.content)
                except ValueError:
                    return False
                if msg.channel.id != ctx.channel.id or not any(msg.author == k[0] for k in self.raid.keys()):
                    return False
                if highest_bid[1] == 0:  # Allow starting bid to be $1
                    if val < 1:
                        return False
                    else:
                        return True
                if val > highest_bid[1]:
                    if highest_bid[1] < 100:
                        return True
                    else:
                        return False
                if val < int(highest_bid[1] * 1.1):  # Minimum bid is 10% higher than the highest bid
                    return False
                if (
                        msg.author.id == highest_bid[0]
                ):  # don't allow a player to outbid themselves
                    return False
                return True

            page = commands.Paginator()
            for u in list(raid.keys()):
                page.add_line(u.mention)
            page.add_line(
                "The raid killed the boss!\nHe dropped a"
                f" {self.bot.cogs['Crates'].emotes.legendary} Legendary Crate!\nThe highest"
                " bid for it wins <:roosip:505447694408482846>\nSimply type how much"
                " you bid!"
            )
            for p in page.pages:
                await ctx.send(p[4:-4])

            while True:
                try:
                    msg = await self.bot.wait_for("message", timeout=60, check=check)
                except asyncio.TimeoutError:
                    break
                bid = int(msg.content)
                money = await self.bot.pool.fetchval(
                    'SELECT money FROM profile WHERE "user"=$1;', msg.author.id
                )
                if money and money >= bid:
                    highest_bid = [msg.author.id, bid]
                    if highest_bid[1] > 100:
                        next_bid = int(highest_bid[1] * 1.1)
                        await ctx.send(
                            f"{msg.author.mention} bids **${msg.content}**!\n The minimum next bid is **${next_bid}**.")
                    else:
                        await ctx.send(
                            f"{msg.author.mention} bids **${msg.content}**!")
            msg = await ctx.send(
                f"Auction done! Winner is <@{highest_bid[0]}> with"
                f" **${highest_bid[1]}**!\nGiving Legendary Crate..."
            )
            money = await self.bot.pool.fetchval(
                'SELECT money FROM profile WHERE "user"=$1;', highest_bid[0]
            )
            if money >= highest_bid[1]:
                async with self.bot.pool.acquire() as conn:
                    await conn.execute(
                        'UPDATE profile SET "money"="money"-$1,'
                        ' "crates_legendary"="crates_legendary"+1 WHERE "user"=$2;',
                        highest_bid[1],
                        highest_bid[0],
                    )
                    await self.bot.log_transaction(
                        ctx,
                        from_=highest_bid[0],
                        to=2,
                        subject="Highest Bid Winner",
                        data={"Gold": highest_bid[1]},
                        conn=conn,
                    )
                await msg.edit(content=f"{msg.content} Done!")
            else:
                await ctx.send(
                    f"<@{highest_bid[0]}> spent the money in the meantime... Meh!"
                    " Noone gets it then, pah!\nThis incident has been reported and"
                    " they will get banned if it happens again. Cheers!"
                )

            cash = int(hp / 4 / len(raid))  # what da hood gets per survivor
            users = [u.id for u in raid]
            await self.bot.pool.execute(
                'UPDATE profile SET money=money+$1 WHERE "user"=ANY($2);',
                cash,
                users,
            )
            await ctx.send(
                f"**Gave ${cash} of Atheistus' ${int(hp / 4)} drop to all survivors!"
                " Thanks to you, the world can live in peace and love again.**"
            )

        else:
            await ctx.send(
                "The raid did not manage to kill Atheistus within 45 Minutes... He"
                " disappeared!"
            )

        await asyncio.sleep(30)
        await ctx.channel.set_permissions(
            ctx.guild.default_role,
            overwrite=self.deny_sending,
        )
        await self.clear_raid_timer()
        self.boss = None

    def getpriceto(self, level: float):
        return sum(i * 25000 for i in range(1, int(level * 10) - 9))

    def getpricetohp(self, level: float):
        return 2 * sum(i * 25000 for i in range(1, int(level * 10) - 9))

    @commands.group(invoke_without_command=True, brief=_("Increase your raidstats"))
    @locale_doc
    async def increase(self, ctx):
        _(
            """Upgrade your raid damage or defense multiplier. These will affect your performance in raids and raidbattles."""
        )
        await ctx.send(
            _(
                "Use `{prefix}increase damage/defense` to upgrade your raid"
                " damage/defense multiplier by 10%."
            ).format(prefix=ctx.clean_prefix)
        )

    @user_cooldown(60, identifier="increase")
    @has_char()
    @increase.command(brief=_("Upgrade your raid damage"))
    @locale_doc
    async def damage(self, ctx):
        _("""Increase your raid damage.""")
        newlvl = ctx.character_data["atkmultiply"] + Decimal("0.1")
        price = self.getpriceto(newlvl)
        if ctx.character_data["money"] < price:
            return await ctx.send(
                _(
                    "Upgrading your weapon attack raid multiplier to {newlvl} costs"
                    " **${price}**, you are too poor."
                ).format(newlvl=newlvl, price=price)
            )
        if not await ctx.confirm(
                _(
                    "Upgrading your weapon attack raid multiplier to {newlvl} costs"
                    " **${price}**, proceed?"
                ).format(newlvl=newlvl, price=price)
        ):
            return
        async with self.bot.pool.acquire() as conn:
            if not await self.bot.has_money(ctx.author, price, conn=conn):
                return await ctx.send(
                    _(
                        "Upgrading your weapon attack raid multiplier to {newlvl} costs"
                        " **${price}**, you are too poor."
                    ).format(newlvl=newlvl, price=price)
                )
            await conn.execute(
                'UPDATE profile SET "atkmultiply"=$1, "money"="money"-$2 WHERE'
                ' "user"=$3;',
                newlvl,
                price,
                ctx.author.id,
            )
            await self.bot.log_transaction(
                ctx,
                from_=ctx.author.id,
                to=2,
                subject="Raid Stats Upgrade ATK",
                data={"Gold": price},
                conn=conn,
            )
        await ctx.send(
            _(
                "You upgraded your weapon attack raid multiplier to {newlvl} for"
                " **${price}**."
            ).format(newlvl=newlvl, price=price)
        )

    @user_cooldown(60, identifier="increase")
    @has_char()
    @increase.command(brief=_("Upgrade your raid damage"))
    @locale_doc
    async def health(self, ctx):
        _("""Increase your raid health.""")
        newlvl = ctx.character_data["hplevel"] + Decimal("0.1")
        healthpool = ctx.character_data["health"] + 5
        healthpoolcheck = ctx.character_data["health"] + 5 + 250
        price = self.getpricetohp(newlvl)
        if ctx.character_data["money"] < price:
            return await ctx.send(
                _(
                    "Upgrading your health pool to {healthpoolcheck} costs"
                    " **${price}**, you are too poor."
                ).format(healthpoolcheck=healthpoolcheck, price=price)
            )
        if not await ctx.confirm(
                _(
                    "Upgrading your health pool to {healthpoolcheck} costs"
                    " **${price}**, proceed?"
                ).format(healthpoolcheck=healthpoolcheck, price=price)
        ):
            return
        async with self.bot.pool.acquire() as conn:
            if not await self.bot.has_money(ctx.author, price, conn=conn):
                return await ctx.send(
                    _(
                        "Upgrading your health pool to {healthpoolcheck} costs"
                        " **${price}**, you are too poor."
                    ).format(healthpoolcheck=healthpoolcheck, price=price)
                )
            await conn.execute(
                'UPDATE profile SET "health"=$1, "money"="money"-$2 WHERE'
                ' "user"=$3;',
                healthpool,
                price,
                ctx.author.id,
            )
            await conn.execute(
                'UPDATE profile SET "hplevel"=$1 WHERE "user"=$2;',
                newlvl,
                ctx.author.id,
            )
            await self.bot.log_transaction(
                ctx,
                from_=ctx.author.id,
                to=2,
                subject="Raid Stats Upgrade HEALTH",
                data={"Gold": price},
                conn=conn,
            )
        await ctx.send(
            _(
                "You upgraded your health pool to {healthpoolcheck} for"
                " **${price}**."
            ).format(healthpoolcheck=healthpoolcheck, price=price)
        )

    @user_cooldown(60, identifier="increase")
    @has_char()
    @increase.command(brief=_("Upgrade your raid defense"))
    @locale_doc
    async def defense(self, ctx):
        _("""Increase your raid defense.""")
        newlvl = ctx.character_data["defmultiply"] + Decimal("0.1")
        price = self.getpriceto(newlvl)
        if ctx.character_data["money"] < price:
            return await ctx.send(
                _(
                    "Upgrading your shield defense raid multiplier to {newlvl} costs"
                    " **${price}**, you are too poor."
                ).format(newlvl=newlvl, price=price)
            )
        if not await ctx.confirm(
                _(
                    "Upgrading your shield defense raid multiplier to {newlvl} costs"
                    " **${price}**, proceed?"
                ).format(newlvl=newlvl, price=price)
        ):
            return
        async with self.bot.pool.acquire() as conn:
            if not await self.bot.has_money(ctx.author, price, conn=conn):
                return await ctx.send(
                    _(
                        "Upgrading your shield defense raid multiplier to {newlvl}"
                        " costs **${price}**, you are too poor."
                    ).format(newlvl=newlvl, price=price)
                )
            await conn.execute(
                'UPDATE profile SET "defmultiply"=$1, "money"="money"-$2 WHERE'
                ' "user"=$3;',
                newlvl,
                price,
                ctx.author.id,
            )
            await self.bot.log_transaction(
                ctx,
                from_=ctx.author.id,
                to=2,
                subject="Raid Stats Upgrade DEF",
                data={"Gold": price},
                conn=conn,
            )
        await ctx.send(
            _(
                "You upgraded your shield defense raid multiplier to {newlvl} for"
                " **${price}**."
            ).format(newlvl=newlvl, price=price)
        )

    import discord
    from discord.ext import commands
    from decimal import Decimal, getcontext
    import traceback

    # Set decimal precision high enough for your application's needs
    getcontext().prec = 28

    # Replace this with the actual ID you want to check against
    SPECIAL_USER_ID = 144932915682344960

    import discord
    from discord.ext import commands
    from decimal import Decimal
    import traceback

    import discord
    from discord.ext import commands
    from decimal import Decimal
    import traceback

    @commands.command()
    async def rspref(self, ctx):
        if ctx.author.id in self.toggle_list:
            self.toggle_list.remove(ctx.author.id)
            await ctx.send("You are now using the new raid stats.")
        else:
            self.toggle_list.add(ctx.author.id)
            await ctx.send("You are now using the old raid stats.")

    @has_char()
    @commands.command(aliases=["rs"], brief=_("View your raid stats or compare two players"))
    @locale_doc
    async def raidstats(self, ctx, player1: discord.Member = None, player2: discord.Member = None):

        if ctx.author.id in self.toggle_list:

            # Execute code if the ID matches one of the specified IDs
            # Old raidstats implementation
            _(
                """View your raidstats. These will affect your performance in raids and raidbattles."""
            )

            if player1:
                target_player = player1
            else:
                target_player = ctx.author

            try:
                # Fetch class, attack multiplier, defense multiplier, health, and health per level
                query = '''
                    SELECT "class", "atkmultiply", "defmultiply", "health", "hplevel", 
                           "guild", "xp", "statdef", "statatk", "stathp" 
                    FROM profile 
                    WHERE "user" = $1;
                '''
                result = await self.bot.pool.fetch(query, target_player.id)

                if result:
                    player_data = result[0]
                    level = rpgtools.xptolevel(player_data["xp"])
                    statdeff = player_data["statdef"] * Decimal("0.1")
                    statatk = player_data["statatk"] * Decimal("0.1")
                    atk = player_data["atkmultiply"] + statatk
                    deff = player_data["defmultiply"] + statdeff

                    stathp = player_data["stathp"] * 50
                    base = 250 + (level * 5)
                    hp = player_data["health"] + stathp + base
                    hplevel = player_data["hplevel"]
                    guild = player_data["guild"]
                    hpprice = self.getpricetohp(hplevel + Decimal("0.1"))
                    atkp = self.getpriceto(atk + Decimal("0.1") - statatk)
                    deffp = self.getpriceto(deff + Decimal("0.1") - statdeff)
                    classes = [class_from_string(c) for c in player_data["class"]]

                    if buildings := await self.bot.get_city_buildings(player_data["guild"]):
                        atk += Decimal("0.1") * buildings["raid_building"]
                        deff += Decimal("0.1") * buildings["raid_building"]

                    async with self.bot.pool.acquire() as conn:
                        dmg, defff = await self.bot.get_raidstats(target_player, conn=conn)

                    # Sanitize atk and deff to prevent negative multipliers
                    atk = max(float(atk), 0)
                    deff = max(float(deff), 0)


                    embed = discord.Embed(
                        title=f"{target_player.display_name}'s Raid Multipliers",
                        description=(
                            f"**Damage Multiplier:** x{atk}\n"
                            f"**Upgrading:** ${int(atkp)}\n\n"  # Removed decimal
                            f"**Health Multiplier:** x{hplevel}\n"
                            f"**Upgrading:** ${int(hpprice)}\n\n"  # Removed decimal
                            f"**Defense Multiplier:** x{deff}\n"
                            f"**Upgrading:** ${int(deffp)}\n\n"  # Removed decimal
                            f"**Player's Damage:** {dmg}\n"
                            f"**Player's Defense:** {defff}\n"
                            f"**Player's Health:** {hp}"
                        ),
                        color=0x00ff00,  # You can change the color code as needed
                    )
                else:
                    embed = discord.Embed(
                        description="❌ Player's data could not be retrieved.",
                        color=0xFF0000
                    )

            except Exception as e:
                error_message = f"Error occurred: {e}\n{traceback.format_exc()}"
                await ctx.send(error_message)
                print(error_message)
                return

            await ctx.send(embed=embed)

        else:
            # New raidstats implementation
            _(
                """View your raid stats or compare two players' raid stats. These stats will affect performance in raids and raid battles."""
            )

            # Function to fetch and process player data
            async def get_player_data(player):
                try:
                    query = '''
                        SELECT "class", "atkmultiply", "defmultiply", "health", "hplevel", 
                               "guild", "xp", "statdef", "statatk", "stathp" 
                        FROM profile 
                        WHERE "user" = $1;
                    '''
                    result = await self.bot.pool.fetch(query, player.id)

                    if not result:
                        return None

                    player_data = result[0]
                    level = rpgtools.xptolevel(player_data["xp"])
                    statdeff = player_data["statdef"] * Decimal("0.1")
                    statatk = player_data["statatk"] * Decimal("0.1")
                    atk = player_data["atkmultiply"] + statatk
                    deff = player_data["defmultiply"] + statdeff

                    stathp = player_data["stathp"] * 50
                    base = 250 + (level * 5)
                    hp = player_data["health"] + stathp + base
                    hplevel = player_data["hplevel"]
                    guild = player_data["guild"]
                    hpprice = self.getpricetohp(hplevel + Decimal("0.1"))
                    atkp = self.getpriceto(atk + Decimal("0.1") - statatk)
                    deffp = self.getpriceto(deff + Decimal("0.1") - statdeff)
                    classes = [class_from_string(c) for c in player_data["class"]]

                    if buildings := await self.bot.get_city_buildings(player_data["guild"]):
                        atk += Decimal("0.1") * buildings["raid_building"]
                        deff += Decimal("0.1") * buildings["raid_building"]

                    async with self.bot.pool.acquire() as conn:
                        dmg, defff = await self.bot.get_raidstats(player, conn=conn)

                    # Sanitize atk and deff to prevent negative multipliers
                    atk = max(float(atk), 0)
                    deff = max(float(deff), 0)

                    # Format multipliers to one decimal place
                    atk = float(f"{atk:.1f}")
                    deff = float(f"{deff:.1f}")
                    hplevel = float(f"{hplevel:.1f}")

                    # Convert Decimal and integer values to float for consistency
                    dmg = float(dmg)
                    defff = float(defff)
                    hp = float(hp)
                    hpprice = float(hpprice)
                    atkp = float(atkp)
                    deffp = float(deffp)


                    return {
                        "player": player,
                        "atk": atk,
                        "deff": deff,
                        "hp": hp,
                        "hplevel": hplevel,
                        "hpprice": hpprice,
                        "atkp": atkp,
                        "deffp": deffp,
                        "dmg": dmg,
                        "defff": defff,
                    }
                except Exception as e:
                    error_message = f"Error fetching data for {player.display_name}: {e}\n{traceback.format_exc()}"
                    await ctx.send(error_message)
                    print(error_message)
                    return None

            # Function to compare two players
            def compare_players(data1, data2):
                # Define a scoring system with sanitized atk and deff
                power1 = (max(data1["atk"], 0) * data1["dmg"]) + (max(data1["deff"], 0) * data1["defff"]) + data1["hp"]
                power2 = (max(data2["atk"], 0) * data2["dmg"]) + (max(data2["deff"], 0) * data2["defff"]) + data2["hp"]

                # Determine the difference
                difference = power1 - power2
                threshold = max(power1, power2) * 0.05  # 5% threshold for uncertainty

                if abs(difference) < threshold:
                    # Power method is too close; perform combat simulation

                    def simulate_combat(attacker_first=True):
                        # Initialize HPs
                        p1_hp = data1["hp"]
                        p2_hp = data2["hp"]

                        if attacker_first:
                            # Player 1 attacks Player 2
                            damage = data1["dmg"] - data2["defff"]
                            damage = max(damage, 0)
                            p2_hp -= damage

                            # Player 2 retaliates if still alive
                            if p2_hp > 0:
                                damage = data2["dmg"] - data1["defff"]
                                damage = max(damage, 0)
                                p1_hp -= damage

                                # Player 1 attacks again if still alive
                                if p1_hp > 0:
                                    damage = data1["dmg"] - data2["defff"]
                                    damage = max(damage, 0)
                                    p2_hp -= damage
                        else:
                            # Player 2 attacks Player 1
                            damage = data2["dmg"] - data1["defff"]
                            damage = max(damage, 0)
                            p1_hp -= damage

                            # Player 1 retaliates if still alive
                            if p1_hp > 0:
                                damage = data1["dmg"] - data2["defff"]
                                damage = max(damage, 0)
                                p2_hp -= damage

                                # Player 2 attacks again if still alive
                                if p2_hp > 0:
                                    damage = data2["dmg"] - data1["defff"]
                                    damage = max(damage, 0)
                                    p1_hp -= damage

                        # Determine outcome
                        if p1_hp > 0 and p2_hp <= 0:
                            return 'player1'
                        elif p2_hp > 0 and p1_hp <= 0:
                            return 'player2'
                        elif p1_hp > p2_hp:
                            return 'player1'
                        elif p2_hp > p1_hp:
                            return 'player2'
                        else:
                            return 'tie'

                    # Simulate both strike orders
                    outcome_first_p1 = simulate_combat(attacker_first=True)
                    outcome_first_p2 = simulate_combat(attacker_first=False)

                    # Analyze outcomes
                    if outcome_first_p1 == outcome_first_p2:
                        if outcome_first_p1 == 'player1':
                            result = f"🏆 **{data1['player'].display_name}** would win the raid battle against **{data2['player'].display_name}**."
                            method_used = "Combat Simulation (Player 1 strikes first)"
                        elif outcome_first_p1 == 'player2':
                            result = f"🏆 **{data2['player'].display_name}** would win the raid battle against **{data1['player'].display_name}**."
                            method_used = "Combat Simulation (Player 1 strikes first)"
                        else:
                            result = "⚖️ **The outcome is uncertain; the players are too closely matched.**"
                            method_used = "Combat Simulation (Player 1 strikes first)"
                    else:
                        # Outcomes differ based on who strikes first
                        result = (
                            "⚖️ **The outcome depends on who strikes first; it's too close to call definitively.**\n\n"
                            f"🔹 When **{data1['player'].display_name}** strikes first:\n {'🏆 ' + data1['player'].display_name + ' wins.' if outcome_first_p1 == 'player1' else '🏆 ' + data2['player'].display_name + ' wins.' if outcome_first_p1 == 'player2' else '⚖️ Tie.'}\n"
                            f"\n🔹 When **{data2['player'].display_name}** strikes first:\n {'🏆 ' + data1['player'].display_name + ' wins.' if outcome_first_p2 == 'player1' else '🏆 ' + data2['player'].display_name + ' wins.' if outcome_first_p2 == 'player2' else '⚖️ Tie.'}"
                        )
                        method_used = "Combat Simulation (Both strike orders)"

                    return result, method_used
                else:
                    # Use power-based comparison
                    if difference > 0:
                        result = f"🏆 **{data1['player'].display_name}** is more likely to win the raid battle against **{data2['player'].display_name}**."
                    else:
                        result = f"🏆 **{data2['player'].display_name}** is more likely to win the raid battle against **{data1['player'].display_name}**."
                    method_used = "Power-Based Comparison"

                    return result, method_used

            # Function to create a stylish embed for a player
            def create_player_embed(data):
                embed = discord.Embed(
                    title=f"{data['player'].display_name}'s Raid Stats",
                    color=0x1E90FF,  # DodgerBlue
                    timestamp=ctx.message.created_at
                )
                embed.set_thumbnail(
                    url=data['player'].avatar.url if data['player'].avatar else data['player'].default_avatar.url)
                embed.add_field(name="⚔️ **Damage Multiplier**", value=f"x{data['atk']}", inline=True)
                embed.add_field(name="🛡️ **Defense Multiplier**", value=f"x{data['deff']}", inline=True)
                embed.add_field(name="❤️ **Health Multiplier**", value=f"x{data['hplevel']}", inline=True)
                embed.add_field(
                    name="💰 **Upgrade Costs**",
                    value=(
                        f"**Damage:** ${int(data['atkp'])}\n"  # Removed decimal
                        f"**Defense:** ${int(data['deffp'])}\n"  # Removed decimal
                        f"**Health:** ${int(data['hpprice'])}"
                    ),
                    inline=False
                )
                embed.add_field(
                    name="📈 **Player's Stats**",
                    value=(
                        f"**Damage:** {data['dmg']}\n"
                        f"**Defense:** {data['defff']}\n"
                        f"**Health:** {data['hp']}"
                    ),
                    inline=False
                )
                embed.set_footer(
                    text=f"Requested by {ctx.author}",
                    icon_url=ctx.author.avatar.url if ctx.author.avatar else ctx.author.default_avatar.url
                )
                return embed

            # Determine which players to fetch data for
            if player1 and player2:
                # Compare two players
                data1 = await get_player_data(player1)
                data2 = await get_player_data(player2)

                if not data1 or not data2:
                    await ctx.send("❌ One or both players' data could not be retrieved.")
                    return

                # Create embeds for both players
                embed1 = create_player_embed(data1)
                embed2 = create_player_embed(data2)

                # Compare and get the result along with the method used
                comparison_result, method_used = compare_players(data1, data2)

                # Create a final embed to show comparison result
                comparison_embed = discord.Embed(
                    title="🆚 Raid Battle Comparison",
                    description=comparison_result,
                    color=0xFFD700,  # Gold
                    timestamp=ctx.message.created_at
                )
                comparison_embed.set_footer(
                    text=f"Comparison requested by {ctx.author}",
                    icon_url=ctx.author.avatar.url if ctx.author.avatar else ctx.author.default_avatar.url
                )

                # Send the main comparison embeds
                try:
                    await ctx.send(embeds=[embed1, embed2, comparison_embed])
                except Exception as e:
                    # Fallback for discord.py versions that don't support multiple embeds
                    await ctx.send(embed=embed1)
                    await ctx.send(embed=embed2)
                    await ctx.send(embed=comparison_embed)

            else:
                # Show stats for one player (either specified or the command invoker)
                target_player = player1 if player1 else ctx.author
                data = await get_player_data(target_player)

                if not data:
                    await ctx.send("❌ Player's data could not be retrieved.")
                    return

                embed = create_player_embed(data)

                await ctx.send(embed=embed)

    @commands.command(brief=_("Did somebody say Raid?"))
    @locale_doc
    async def raid(self, ctx):
        _("""Informs you about joining raids.""")
        await ctx.send(
            _(
                "Did you ever want to join together with other players to defeat the"
                " dragon that roams this land? Raids got you covered!\nJoin the support"
                " server (`{prefix}support`) for more information."
            ).format(prefix=ctx.clean_prefix)
        )


async def setup(bot):
    await bot.add_cog(Raid(bot))
