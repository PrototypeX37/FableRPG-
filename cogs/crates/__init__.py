"""
The IdleRPG Discord Bot
Copyright (C) 2018-2021 Diniboy and Gelbpunkt
Copyright (C) 2024 Lunar (discord itslunar.))

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
from collections import Counter, namedtuple

import discord
import random
import uuid

from discord.ext import commands
from utils import misc as rpgtools

from classes.classes import (
    ALL_CLASSES_TYPES,
    Mage,
    Paragon,
    Raider,
    Ranger,
    Ritualist,
    Thief,
    Warrior,
    Paladin,
    Reaper,
    SantasHelper,
)
from classes.classes import from_string as class_from_string

from classes.converters import (
    CrateRarity,
    IntFromTo,
    IntGreaterThan,
    MemberWithCharacter,
)
from cogs.shard_communication import user_on_cooldown as user_cooldown
from utils import random
from utils.checks import has_char, has_money, is_gm, is_class
from utils.i18n import _, locale_doc


class Crates(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.crate = 0
        self.emotes = namedtuple(
            "CrateEmotes", "common uncommon rare magic legendary item mystery fortune divine"
        )(
            common="<:F_common:1139514874016309260>",
            uncommon="<:F_uncommon:1139514875828252702>",
            rare="<:F_rare:1139514880517484666>",
            magic="<:F_Magic:1139514865174720532>",
            legendary="<:F_Legendary:1139514868400132116>",
            item="<a:ItemAni:896715561550110721>",
            mystery="<:F_mystspark:1139521536320094358>",
            fortune="<:f_money:1146593710516224090>",
            divine="<:f_divine:1169412814612471869>",
        )

    @has_char()
    @commands.command(aliases=["boxes"], brief=_("Show your crates."))
    @locale_doc
    async def crates(self, ctx):
        _(
            """Shows all the crates you can have.

            Common crates contain items ranging from stats 1 to 30
            Uncommon crates contain items ranging from stats 10 to 35
            Rare crates contain items ranging from stats 20 to 40
            Magic crates contain items ranging from stats 30 to 45
            Legendary crates contain items ranging from stats 41 to 55
            Divine crates contain items ranging from stats 51 to 100
            Mystery crates contain a random crate
            Fortune Crates contain either XP or money.

            You can receive crates by voting for the bot using `{prefix}vote`, using `{prefix}daily` and with a small chance from `{prefix}familyevent`, if you have children."""
        )

        embed = discord.Embed(
            title=_("Your Crates"), color=discord.Color.blurple()
        ).set_author(name=ctx.disp, icon_url=ctx.author.display_avatar.url)

        for rarity in ("common", "uncommon", "rare", "magic", "legendary", "mystery", "fortune", "divine"):
            amount = ctx.character_data[f"crates_{rarity}"]
            emote = getattr(self.emotes, rarity)

            # Check if the author's ID is the specific ID
            if ctx.author.id == 823030177025753100 and rarity == "fortune":
                emote = "🥠"  # Change the emote for fortune rarity

            embed.add_field(
                name=f"{emote} {rarity.title()}",
                value=_("{amount} crates").format(amount=amount),
                inline=False,
            )

        embed.set_footer(
            text=_("Use {prefix}open [rarity] to open one!").format(
                prefix=ctx.clean_prefix
            )
        )

        await ctx.send(embed=embed)

    @commands.cooldown(1, 10, commands.BucketType.user)
    @has_char()
    @commands.command(name="open", brief=_("Open a crate"))
    @locale_doc
    async def _open(
            self, ctx, rarity: CrateRarity = "common", amount: IntFromTo(1, 100) = 1
    ):
        _(
            """`[rarity]` - the crate's rarity to open, can be common, uncommon, rare, magic or legendary; defaults to common
            `[amount]` - the amount of crates to open, may be in range from 1 to 100 at once

            Open one of your crates to receive a weapon. To check which crates contain which items, check `{prefix}help crates`.
            This command takes up a lot of space, so choose a spammy channel to open crates."""
        )
        try:
            if ctx.character_data[f"crates_{rarity}"] < amount:
                return await ctx.send(
                    _(
                        "Seems like you don't have {amount} crate(s) of this rarity yet."
                        " Vote me up to get a random one or find them!"
                    ).format(amount=amount)
                )

            async with self.bot.pool.acquire() as conn:
                await conn.execute(
                    f'UPDATE profile SET "crates_{rarity}"="crates_{rarity}"-$1 WHERE'
                    ' "user"=$2;',
                    amount,
                    ctx.author.id,
                )

                if rarity == "mystery":
                    crates = {
                        "common": 0,
                        "uncommon": 0,
                        "rare": 0,
                        "magic": 0,
                        "legendary": 0,
                        "fortune": 0,
                        "divine": 0,
                    }

                    for _i in range(amount):
                        rng = random.randint(0, 10000)
                        if rng < 5:
                            new_rarity = "divine"
                        elif rng < 15:
                            new_rarity = "fortune"
                        elif rng < 30:
                            new_rarity = "legendary"
                        elif rng < 200:
                            new_rarity = "magic"
                        elif rng < 1000:
                            new_rarity = "rare"
                        elif rng < 2000:
                            new_rarity = "uncommon"
                        else:
                            new_rarity = "common"

                        # if ctx.author.id == 708435868842459169:
                        # new_rarity = "divine"

                        crates[new_rarity] += 1

                    await conn.execute(
                        'UPDATE profile SET "crates_common"="crates_common"+$1, "crates_uncommon"="crates_uncommon"+$2, "crates_rare"="crates_rare"+$3, "crates_magic"="crates_magic"+$4, "crates_legendary"="crates_legendary"+$5, "crates_fortune"="crates_fortune"+$6, "crates_divine"="crates_divine"+$7 WHERE "user"=$8;',
                        crates["common"],
                        crates["uncommon"],
                        crates["rare"],
                        crates["magic"],
                        crates["legendary"],
                        crates["fortune"],
                        crates["divine"],
                        ctx.author.id,
                    )

                    for r, a in crates.items():
                        if a > 0:
                            await self.bot.log_transaction(
                                ctx,
                                from_=1,
                                to=ctx.author.id,
                                subject="crates",
                                data={"Rarity": r, "Amount": a},
                                conn=conn,
                            )

                    text = _(
                        "You opened {mystery_amount} {mystery_emoji} and received:\n"
                        "- {common_amount} {common_emoji}\n"
                        "- {uncommon_amount} {uncommon_emoji}\n"
                        "- {rare_amount} {rare_emoji}\n"
                        "- {magic_amount} {magic_emoji}\n"
                        "- {legendary_amount} {legendary_emoji}\n"
                        "- {fortune_amount} {fortune_emoji}\n"
                        "- {divine_amount} {divine_emoji}\n"
                    ).format(
                        mystery_amount=amount,
                        mystery_emoji=self.emotes.mystery,
                        common_amount=crates["common"],
                        common_emoji=self.emotes.common,
                        uncommon_amount=crates["uncommon"],
                        uncommon_emoji=self.emotes.uncommon,
                        rare_amount=crates["rare"],
                        rare_emoji=self.emotes.rare,
                        magic_amount=crates["magic"],
                        magic_emoji=self.emotes.magic,
                        legendary_amount=crates["legendary"],
                        legendary_emoji=self.emotes.legendary,
                        fortune_amount=crates["fortune"],
                        fortune_emoji=self.emotes.fortune,
                        divine_amount=crates["divine"],
                        divine_emoji=self.emotes.divine,
                    )

                    await ctx.send(text)

                elif rarity == "fortune":

                    level = rpgtools.xptolevel(ctx.character_data["xp"])
                    random_number = random.randint(1, 100)

                    if random_number <= 50:  # Lower half, reward with XP

                        min_value, max_value = 100, 500  # Adjust the XP range as needed

                        reward_type = "xp"

                    else:  # Upper half, reward with money

                        if random.randint(1, 100) <= 75:  # Simulating 70% chance

                            min_value, max_value = 250000, 470000

                        else:

                            min_value, max_value = 470001, 850000

                        reward_type = "money"

                    value = random.randint(min_value, max_value)

                    async with self.bot.pool.acquire() as conn:

                        user_id = ctx.author.id

                        if reward_type == "xp":

                            nurflevel = level

                            if level > 12:
                                nurflevel = 12

                            xpvar = 2000 * level + 1500

                            random_xp = random.randint(1000 * nurflevel, xpvar)

                            await conn.execute('UPDATE profile SET "xp" = "xp" + $1 WHERE "user" = $2', random_xp,
                                               user_id)

                            await ctx.send(
                                f"You opened a Fortune crate and gained **{random_xp}XP!**")

                            await self.bot.public_log(
                                f"**{ctx.author}** opened a fortune crate and gained **{random_xp} XP!**"
                            )

                            new_level = int(rpgtools.xptolevel(ctx.character_data["xp"] + random_xp))

                            if level != new_level:
                                await self.bot.process_levelup(ctx, new_level, level)

                        else:

                            reward = round(value, -2)

                            await conn.execute('UPDATE profile SET "money" = "money" + $1 WHERE "user" = $2', reward,
                                               user_id)

                            await ctx.send(f"You opened a Fortune crate and found **${reward}!**")

                            await self.bot.public_log(
                                f"**{ctx.author}** opened a fortune crate and received **${reward}!**"
                            )


                else:
                    items = []
                    for _i in range(amount):
                        # A number to detemine the crate item range
                        rand = random.randint(0, 9)
                        if rarity == "common":
                            if rand < 2:  # 20% 20-30
                                minstat, maxstat = (20, 30)
                            elif rand < 5:  # 30% 10-19
                                minstat, maxstat = (10, 19)
                            else:  # 50% 1-9
                                minstat, maxstat = (1, 9)
                        elif rarity == "uncommon":
                            if rand < 2:  # 20% 30-35
                                minstat, maxstat = (30, 35)
                            elif rand < 5:  # 30% 20-29
                                minstat, maxstat = (20, 29)
                            else:  # 50% 10-19
                                minstat, maxstat = (10, 19)
                        elif rarity == "rare":
                            if rand < 2:  # 20% 35-40
                                minstat, maxstat = (35, 40)
                            elif rand < 5:  # 30% 30-34
                                minstat, maxstat = (30, 34)
                            else:  # 50% 20-29
                                minstat, maxstat = (20, 29)
                        elif rarity == "magic":
                            if rand < 2:  # 20% 41-45
                                minstat, maxstat = (41, 45)
                            elif rand < 5:  # 30% 35-40
                                minstat, maxstat = (35, 40)
                            else:
                                minstat, maxstat = (30, 34)
                        elif rarity == "legendary":  # no else because why
                            if rand < 2:  # 20% 49-50
                                minstat, maxstat = (49, 65)
                            elif rand < 5:  # 30% 46-48
                                minstat, maxstat = (46, 48)
                            else:  # 50% 41-45
                                minstat, maxstat = (41, 45)
                        elif rarity == "divine":
                            rand = random.randint(1, 100)  # Using a range of 1 to 100 for clearer percentages
                            if rand <= 10:
                                minstat, maxstat = (77, 100)
                            elif rand <= 60:
                                minstat, maxstat = (57, 76)
                            elif rand <= 80:
                                minstat, maxstat = (52, 56)
                            else:
                                minstat, maxstat = (47, 51)

                        item = await self.bot.create_random_item(
                            minstat=minstat,
                            maxstat=maxstat,
                            minvalue=1,
                            maxvalue=250,
                            owner=ctx.author,
                            conn=conn,
                        )
                        items.append(item)
                        await self.bot.log_transaction(
                            ctx,
                            from_=1,
                            to=ctx.author.id,
                            subject="crate open item",
                            data={"Name": item["name"], "Value": item["value"]},
                            conn=conn,
                        )

                    if amount == 1:
                        embed = discord.Embed(
                            title=_("You gained an item!"),
                            description=_("You found a new item when opening a crate!"),
                            color=0xFF0000,
                        )
                        embed.set_thumbnail(url=ctx.author.display_avatar.url)
                        embed.add_field(name=_("ID"), value=item["id"], inline=False)
                        embed.add_field(name=_("Name"), value=item["name"], inline=False)
                        embed.add_field(name=_("Element"), value=item["element"], inline=False)
                        embed.add_field(name=_("Type"), value=item["type"], inline=False)
                        embed.add_field(name=_("Damage"), value=item["damage"], inline=True)
                        embed.add_field(name=_("Armor"), value=item["armor"], inline=True)
                        embed.add_field(
                            name=_("Value"), value=f"${item['value']}", inline=False
                        )
                        embed.set_footer(
                            text=_("Remaining {rarity} crates: {crates}").format(
                                crates=ctx.character_data[f"crates_{rarity}"] - 1,
                                rarity=rarity,
                            )
                        )
                        await ctx.send(embed=embed)
                        if rarity == "legendary":
                            await self.bot.public_log(
                                f"**{ctx.author}** opened a legendary crate and received"
                                f" {item['name']} with **{item['damage'] or item['armor']}"
                                f" {'damage' if item['damage'] else 'armor'}**."
                            )
                        if rarity == "divine":
                            await self.bot.public_log(
                                f"**{ctx.author}** opened a divine crate and received"
                                f" {item['name']} with **{item['damage'] or item['armor']}"
                                f" {'damage' if item['damage'] else 'armor'}**."
                            )
                        elif rarity == "magic" and item["damage"] + item["armor"] >= 41:
                            if item["damage"] >= 41:
                                await self.bot.public_log(
                                    f"**{ctx.author}** opened a magic crate and received"
                                    f" {item['name']} with **{item['damage'] or item['armor']}"
                                    f" {'damage' if item['damage'] else 'armor'}**."
                                )
                    else:
                        stats_raw = [i["damage"] + i["armor"] for i in items]
                        stats = Counter(stats_raw)
                        types = Counter([i["type"] for i in items])
                        most_common = "\n".join(
                            [f"- {i[0]} (x{i[1]})" for i in stats.most_common(5)]
                        )
                        most_common_types = "\n".join(
                            [f"- {i[0]} (x{i[1]})" for i in types.most_common()]
                        )
                        top = "\n".join([f"- {i}" for i in sorted(stats, reverse=True)[:5]])
                        average_stat = round(sum(stats_raw) / amount, 2)
                        await ctx.send(
                            _(
                                "Successfully opened {amount} {rarity} crates. Average stat:"
                                " {average_stat}\nMost common stats:\n```\n{most_common}\n```\nBest"
                                " stats:\n```\n{top}\n```\nTypes:\n```\n{most_common_types}\n```"
                            ).format(
                                amount=amount,
                                rarity=rarity,
                                average_stat=average_stat,
                                most_common=most_common,
                                top=top,
                                most_common_types=most_common_types,
                            )
                        )
                        if rarity == "legendary":
                            await self.bot.public_log(
                                f"**{ctx.author}** opened {amount} legendary crates and received"
                                f" stats:\n```\n{most_common}\n```\nAverage: {average_stat}"
                            )
                        if rarity == "divine":
                            await self.bot.public_log(
                                f"**{ctx.author}** opened {amount} divine crates and received"
                                f" stats:\n```\n{most_common}\n```\nAverage: {average_stat}"
                            )
                        elif rarity == "magic":
                            await self.bot.public_log(
                                f"**{ctx.author}** opened {amount} magic crates and received"
                                f" stats:\n```\n{most_common}\n```\nAverage: {average_stat}"
                            )
        except Exception as e:
            await ctx.send(f"{e}")

    @commands.cooldown(1, 10, commands.BucketType.user)
    @has_char()
    @is_gm()
    @commands.command(name="generateweapon", brief=_("Generate a weapon"))
    @locale_doc
    async def generateweapon(self, ctx, amount: IntFromTo(1, 1000) = 1):
        _(
            """`[amount]` - the amount of weapons to generate, may be in range from 1 to 100 at once

            Generate weapons for 1144898209144127589. The stats of the generated weapons will be random."""
        )

        target_user_id = 1144898209144127589  # The user you want to generate items for

        async with self.bot.pool.acquire() as conn:
            items = []
            for _i in range(amount):
                # Randomly determine the weapon's stats between 5 and 50
                minstat, maxstat = (5, 50)

                item = await self.bot.create_random_item(
                    minstat=minstat,
                    maxstat=maxstat,
                    minvalue=1,
                    maxvalue=250,
                    owner=target_user_id,  # Changed this line to set the owner
                    conn=conn,
                )
                items.append(item)
                await self.bot.log_transaction(
                    ctx,
                    from_=1,
                    to=target_user_id,  # Updated this line to reflect the change
                    subject="generatedweapon ADMIN ONLY",
                    data={"Name": item["name"], "Value": item["value"]},
                    conn=conn,
                )

            if amount == 1:
                embed = discord.Embed(
                    title=_("You gained an item!"),
                    description=_("You generated a new weapon!"),
                    color=0xFF0000,
                )
                embed.set_thumbnail(url=ctx.author.display_avatar.url)
                embed.add_field(name=_("ID"), value=item["id"], inline=False)
                embed.add_field(name=_("Name"), value=item["name"], inline=False)
                embed.add_field(name=_("Type"), value=item["type"], inline=False)
                embed.add_field(name=_("Damage"), value=item["damage"], inline=True)
                embed.add_field(name=_("Armor"), value=item["armor"], inline=True)
                embed.add_field(
                    name=_("Value"), value=f"${item['value']}", inline=False
                )
                await ctx.send(embed=embed)
            else:
                stats_raw = [i["damage"] + i["armor"] for i in items]
                stats = Counter(stats_raw)
                types = Counter([i["type"] for i in items])
                most_common = "\n".join(
                    [f"- {i[0]} (x{i[1]})" for i in stats.most_common(5)]
                )
                most_common_types = "\n".join(
                    [f"- {i[0]} (x{i[1]})" for i in types.most_common()]
                )
                top = "\n".join([f"- {i}" for i in sorted(stats, reverse=True)[:5]])
                average_stat = round(sum(stats_raw) / amount, 2)
                await ctx.send(
                    _(
                        "Successfully generated {amount} weapons. Average stat:"
                        " {average_stat}\nMost common stats:\n```\n{most_common}\n```\nBest"
                        " stats:\n```\n{top}\n```\nTypes:\n```\n{most_common_types}\n```"
                    ).format(
                        amount=amount,
                        average_stat=average_stat,
                        most_common=most_common,
                        top=top,
                        most_common_types=most_common_types,
                    )
                )

    @is_class(SantasHelper)
    @has_char()
    @user_cooldown(21600)
    @commands.command(aliases=["giftuser"], brief=_("Gift crate to user"))
    @locale_doc
    async def gift(self, ctx, gift_user: discord.Member):
        _(
            """**[SANTA'S HELPER ONLY]**

            Embrace the joy of giving! As a Santa's Helper, you can send a gift crate containing a surprise weapon to 
            another user. The crate's contents vary in rarity, promising a festive and formidable addition to their 
            arsenal. Spread holiday cheer and equip your allies for epic battles!"""
        )
        try:
            """Bless a user by setting their blessing value in Redis."""

            grade = 0
            for class_ in ctx.character_data["class"]:
                c = class_from_string(class_)
                if c and c.in_class_line(SantasHelper):
                    grade = c.class_grade()

            # Check if the author is trying to bless themselves
            if ctx.author.id == gift_user.id:
                await ctx.send("You cannot give a gift to yourself!")
                return await self.bot.reset_cooldown(ctx)

            # Check if the user is already blessed
            discord_user_id = gift_user.id

            # Generate a unique identifier (e.g., UUID) for additional context
            unique_identifier = "Gift"

            # Combine the Discord ID with the unique identifier to create a complex key
            unique_key = f"gift_received:{discord_user_id}:{unique_identifier}"

            # Check if the user has received a gift recently
            current_gift_value = await self.bot.redis.get(unique_key)

            if current_gift_value:
                await ctx.send(f"{gift_user.mention} has received a gift recently!")
                return await self.bot.reset_cooldown(ctx)

            # Ask for confirmation
            # Create a visually appealing embed for the confirmation message
            embed = discord.Embed(
                title="🎁 Gift Confirmation 🎁",
                description=f"{gift_user.mention}, {ctx.author.mention} presents you a gift! Do you accept?",
                color=0x4CAF50
            )
            embed.add_field(name="User", value=gift_user.mention, inline=True)
            embed.set_footer(text=f"Requested by {ctx.author}")
            embed.timestamp = ctx.message.created_at

            embed_msg = await ctx.send(embed=embed)

            # Ask the user to confirm by reacting to the message
            confirmation_prompt = f"{gift_user.mention} Please react below to confirm or decline."
            try:
                if not await ctx.confirm(message=confirmation_prompt, user=gift_user):
                    await embed_msg.delete()
                    await ctx.send("Gifting cancelled.")
                    await self.bot.reset_cooldown(ctx)
                    return
            except Exception as e:
                await self.bot.reset_cooldown(ctx)
                await embed_msg.delete()

            # If confirmation received, proceed with the rest of the code
            await embed_msg.delete()  # delete the embed message

            # Assuming gift_user is an object with an 'id' attribute
            discord_user_id = gift_user.id

            # Generate a unique identifier (e.g., UUID) for additional context
            unique_identifier = "Gift"

            # Combine the Discord ID with the unique identifier to create a complex key
            unique_key = f"gift_received:{discord_user_id}:{unique_identifier}"

            # Check if the user has received a gift recently
            current_gift_value = await self.bot.redis.get(unique_key)

            if current_gift_value:
                await ctx.send(f"{gift_user.mention} has received a gift recently!")
                return await self.bot.reset_cooldown(ctx)

            # Set the value in Redis with a TTL of 24 hours (86400 seconds)
            await self.bot.redis.setex(unique_key, 600, 'Gift')

            rarities = ["common"] * 390 + ["uncommon"] * 310 + ["rare"] * 290 + ["magic"] * 40 + ["legendary"] + [
                "mystery"] * 100 + ["fortune"] * 10
            rarity1 = random.choice(rarities)

            async with self.bot.pool.acquire() as conn:
                await conn.execute(
                    f'UPDATE profile SET "crates_{rarity1}"="crates_{rarity1}"+1 WHERE'
                    ' "user"=$1;',
                    gift_user.id,
                )

            # Send a confirmation message
            emotes = {
                "common": "<:F_common:1139514874016309260>",
                "uncommon": "<:F_uncommon:1139514875828252702>",
                "rare": "<:F_rare:1139514880517484666>",
                "magic": "<:F_Magic:1139514865174720532>",
                "legendary": "<:F_Legendary:1139514868400132116>",
                "mystery": "<:F_mystspark:1139521536320094358>",
                "fortune": "<:f_money:1146593710516224090>"
            }
            await ctx.send(
                _(f"{gift_user.mention} has received a gift by {ctx.author.mention}. It is a {emotes[rarity1]} {rarity1}!")
            )


        except Exception as e:
            await self.bot.reset_cooldown(ctx)
            await ctx.send(f"Gifting timed out.")

    @has_char()
    @user_cooldown(43200)
    @commands.command(brief=_("Vote and get crates"))
    @locale_doc
    async def vote(self, ctx):
        _(
            """Vote and get crates.

            Vote and get 2 crates, with each crate having a chance of being common (89%), uncommon (6%), rare (4%), magic (0.9%) or legendary (0.1%).

            This command has a cooldown of 12 hours."""
        )
        rarities = ["common"] * 890 + ["uncommon"] * 60 + ["rare"] * 40 + ["magic"] * 6 + ["legendary"] + [
            "mystery"] * 50 + ["fortune"] * 2
        rarity1 = random.choice(rarities)
        rarity2 = random.choice(rarities)

        result = await self.bot.pool.fetchval('SELECT tier FROM profile WHERE "user" = $1;', ctx.author.id)

        if result is not None:
            if result >= 3:
                rarity1 = random.choice(rarities)
                rarity2 = random.choice(rarities)
                rarity3 = random.choice(rarities)
                rarity4 = random.choice(rarities)

                async with self.bot.pool.acquire() as conn:
                    await conn.execute(
                        f'UPDATE profile SET "crates_{rarity1}"="crates_{rarity1}"+1 WHERE'
                        ' "user"=$1;',
                        ctx.author.id,
                    )
                    await conn.execute(
                        f'UPDATE profile SET "crates_{rarity2}"="crates_{rarity2}"+1 WHERE'
                        ' "user"=$1;',
                        ctx.author.id,
                    )
                    await conn.execute(
                        f'UPDATE profile SET "crates_{rarity3}"="crates_{rarity3}"+1 WHERE'
                        ' "user"=$1;',
                        ctx.author.id,
                    )
                    await conn.execute(
                        f'UPDATE profile SET "crates_{rarity4}"="crates_{rarity4}"+1 WHERE'
                        ' "user"=$1;',
                        ctx.author.id,

                 )
                    await self.bot.log_transaction(
                        ctx,
                        from_=1,
                        to=ctx.author.id,
                        subject="vote",
                        data={"Rarity": rarity1, "Amount": 1},
                        conn=conn,
                    )
                    await self.bot.log_transaction(
                        ctx,
                        from_=1,
                        to=ctx.author.id,
                        subject="vote",
                        data={"Rarity": rarity2, "Amount": 1},
                        conn=conn,
                    )
                    await self.bot.log_transaction(
                        ctx,
                        from_=1,
                        to=ctx.author.id,
                        subject="vote",
                        data={"Rarity": rarity3, "Amount": 1},
                        conn=conn,
                    )
                    await self.bot.log_transaction(
                        ctx,
                        from_=1,
                        to=ctx.author.id,
                        subject="vote",
                        data={"Rarity": rarity4, "Amount": 1},
                        conn=conn,
                    )

                emotes = {
                    "common": "<:F_common:1139514874016309260>",
                    "uncommon": "<:F_uncommon:1139514875828252702>",
                    "rare": "<:F_rare:1139514880517484666>",
                    "magic": "<:F_Magic:1139514865174720532>",
                    "legendary": "<:F_Legendary:1139514868400132116>",
                    "mystery": "<:F_mystspark:1139521536320094358>",
                    "fortune": "<:f_money:1146593710516224090>"
                }

                if rarity1 == rarity2 == rarity3 == rarity4:
                    await ctx.send(
                        _("Yeah.. there is no voting, but hey you got 4 {emote} {rarity} crates for your efforts!").format(
                            emote=emotes[rarity1], rarity=rarity1
                        )
                    )
                else:
                    crate_counts = [
                        (rarity1, 1),
                        (rarity2, 1),
                        (rarity3, 1),
                        (rarity4, 1),
                    ]

                    crate_list = [
                        f"{count} {emotes[rarity]} {rarity} crate" if count > 0 else ""
                        for rarity, count in crate_counts
                    ]

                    crate_list = [crate for crate in crate_list if crate]  # Remove empty strings

                    await ctx.send(
                        _("Yeah.. there is no voting, but hey you got {crate_list} for your efforts!").format(
                            crate_list=", ".join(crate_list)
                        )
                    )

            else:
                rarity1 = random.choice(rarities)
                rarity2 = random.choice(rarities)


                async with self.bot.pool.acquire() as conn:
                    await conn.execute(
                        f'UPDATE profile SET "crates_{rarity1}"="crates_{rarity1}"+1 WHERE'
                        ' "user"=$1;',
                        ctx.author.id,
                    )
                    await conn.execute(
                        f'UPDATE profile SET "crates_{rarity2}"="crates_{rarity2}"+1 WHERE'
                        ' "user"=$1;',
                        ctx.author.id,
                    )
                    await self.bot.log_transaction(
                        ctx,
                        from_=1,
                        to=ctx.author.id,
                        subject="crates",
                        data={"Rarity": rarity1, "Amount": 1},
                        conn=conn,
                    )
                    await self.bot.log_transaction(
                        ctx,
                        from_=1,
                        to=ctx.author.id,
                        subject="crates",
                        data={"Rarity": rarity2, "Amount": 1},
                        conn=conn,
                    )

                emotes = {
                    "common": "<:F_common:1139514874016309260>",
                    "uncommon": "<:F_uncommon:1139514875828252702>",
                    "rare": "<:F_rare:1139514880517484666>",
                    "magic": "<:F_Magic:1139514865174720532>",
                    "legendary": "<:F_Legendary:1139514868400132116>",
                    "mystery": "<:F_mystspark:1139521536320094358>",
                    "fortune": "<:f_money:1146593710516224090>"
                }

                if rarity1 == rarity2:
                    await ctx.send(
                        _("Yeah.. there is no voting, but hey you got 2 {emote} {rarity} crates for your efforts!").format(
                            emote=emotes[rarity1], rarity=rarity1
                        )
                    )
                else:
                    await ctx.send(
                        _("Yeah.. there is no voting, but hey you got a {emote1} {rarity1} crate and a {emote2} {rarity2} "
                          "crate for your efforts!").format(
                            emote1=emotes[rarity1], rarity1=rarity1, emote2=emotes[rarity2], rarity2=rarity2
                        )
                    )

    @has_char()
    @commands.command(aliases=["tc"], brief=_("Give crates to someone"))
    @locale_doc
    async def tradecrate(
            self,
            ctx,
            other: MemberWithCharacter,
            amount: IntGreaterThan(0) = 1,
            rarity: CrateRarity = "common",
    ):
        _(
            """`<other>` - A user with a character
            `[amount]` - A whole number greater than 0; defaults to 1
            `[rarity]` - The crate's rarity to trade, can be common, uncommon, rare, magic or legendary; defaults to common

            Give your crates to another person.

            Players must combine this command with `{prefix}give` for a complete trade."""
        )
        if other == ctx.author:
            return await ctx.send(_("Very funny..."))
        elif other == ctx.me:
            return await ctx.send(
                _("For me? I'm flattered, but I can't accept this...")
            )
        if ctx.character_data[f"crates_{rarity}"] < amount:
            return await ctx.send(_("You don't have any crates of this rarity."))
        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                f'UPDATE profile SET "crates_{rarity}"="crates_{rarity}"-$1 WHERE'
                ' "user"=$2;',
                amount,
                ctx.author.id,
            )
            await conn.execute(
                f'UPDATE profile SET "crates_{rarity}"="crates_{rarity}"+$1 WHERE'
                ' "user"=$2;',
                amount,
                other.id,
            )
            await self.bot.log_transaction(
                ctx,
                from_=ctx.author.id,
                to=other.id,
                subject="crates trade",
                data={"Rarity": rarity, "Amount": amount},
                conn=conn,
            )

        await ctx.send(
            _("Successfully gave {amount} {rarity} crate(s) to {other}.").format(
                amount=amount, other=other.mention, rarity=rarity
            )
        )

    @has_char()
    @user_cooldown(30)
    @commands.command(
        aliases=["sellcrates", "sc"], brief=_("Sell crates to NPC for money")
    )
    @locale_doc
    async def sellcrate(
            self,
            ctx,
            quantity: IntGreaterThan(0),
            rarity: CrateRarity,
    ):
        _(
            """`<quantity>` - The quantity of crates to sell
            `<rarity>` - The rarity of crate to sell. First letter of the rarity is also accepted.

            Sell your crates to an NPC in exchange for money.
            Example:
            `{prefix}sellcrate 5 common`"""
        )
        sell_price_per_crate = {
            "common": 400,  # Adjust these values as needed
            "uncommon": 900,
            "rare": 3500,
            "magic": 25000,
            "mystery": 1500,
        }

        if rarity == "legendary":
            await ctx.send(_("Selling legendary crates is not allowed."))
            return

        if rarity not in sell_price_per_crate:
            await ctx.send(_("Invalid rarity specified."))
            return

        if ctx.character_data[f"crates_{rarity}"] < quantity:
            await ctx.send(
                _(
                    "You don't have {quantity} {rarity} crate(s). Check"
                    " `{prefix}crates`."
                ).format(quantity=quantity, rarity=rarity, prefix=ctx.clean_prefix)
            )
            return

        total_sell_price = sell_price_per_crate[rarity] * quantity

        if not await ctx.confirm(
                _(
                    "{author}, are you sure you want to sell **{quantity} {emoji}"
                    " {rarity}** crate(s) for **${total_price:,.0f}**?\n\n"
                    "You will receive **${total_price:,.0f}** for this transaction."
                ).format(
                    author=ctx.author.mention,
                    quantity=quantity,
                    emoji=getattr(self.emotes, rarity),
                    rarity=rarity,
                    total_price=total_sell_price,
                )
        ):
            await ctx.send(_("Sale cancelled."))
            return

        try:
            async with self.bot.pool.acquire() as conn:
                # Check if the user still has the required crates before performing the database update
                user_current_crates = await conn.fetchval(
                    f'SELECT "crates_{rarity}" FROM profile WHERE "user"=$1;', ctx.author.id
                )

                if user_current_crates >= quantity:
                    await conn.execute(
                        f'UPDATE profile SET "crates_{rarity}"="crates_{rarity}"-$1,'
                        ' "money"="money"+$2 WHERE "user"=$3;',
                        quantity,
                        total_sell_price,
                        ctx.author.id,
                    )
                    await self.bot.log_transaction(
                        ctx,
                        from_=ctx.author,
                        to=1,
                        subject="sellcrate",
                        data={
                            "Rarity": rarity,
                            "Quantity": quantity,
                            "Amount": total_sell_price,
                        },
                        conn=conn,
                    )
                else:
                    await ctx.send(_("You no longer have enough crates for this transaction. Sale cancelled."))
                    return

        except commands.CommandError as error:
            if "far too high for me to handle properly" in str(error):
                # Suppress the error message and handle it gracefully
                return

            # Re-raise the error if it's not the specific error you want to suppress
            raise

        await ctx.send(
            _(
                "{author}, you've successfully sold **{quantity} {emoji} {rarity}**"
                " crate(s) to the NPC for **${total_price:,.0f}**.\n\n"
                "You received **${total_price:,.0f}**."
            ).format(
                author=ctx.author.mention,
                quantity=quantity,
                emoji=getattr(self.emotes, rarity),
                rarity=rarity,
                total_price=total_sell_price,
            )
        )

    @has_char()
    @user_cooldown(180)
    @commands.command(
        aliases=["offercrates", "oc"], brief=_("Offer crates to another player")
    )
    @locale_doc
    async def offercrate(
            self,
            ctx,
            quantity: IntGreaterThan(0),
            rarity: CrateRarity,
            price: IntFromTo(0, 100_000_000),
            buyer: MemberWithCharacter,
    ):
        _(
            """`<quantity>` - The quantity of crates to offer
            `<rarity>` - The rarity of crate to offer. First letter of the rarity is also accepted.
            `<price>` - The price to be paid by the buyer, can be a number from 0 to 100000000
            `<buyer>` - Another IdleRPG player to offer the crates to

            Offer crates to another player. Once the other player accepts, they will receive the crates and you will receive their payment.
            Example:
            `{prefix}offercrate 5 common 75000 @buyer#1234`
            `{prefix}oc 5 c 75000 @buyer#1234`"""
        )
        if buyer == ctx.author:
            await ctx.send(_("You may not offer crates to yourself."))
            return await self.bot.reset_cooldown(ctx)
        elif buyer == ctx.me:
            await ctx.send(_("No, I don't want any crates."))
            return await self.bot.reset_cooldown(ctx)

        if ctx.character_data[f"crates_{rarity}"] < quantity:
            await ctx.send(
                _(
                    "You don't have {quantity} {rarity} crate(s). Check"
                    " `{prefix}crates`."
                ).format(quantity=quantity, rarity=rarity, prefix=ctx.clean_prefix)
            )
            return await self.bot.reset_cooldown(ctx)

        if not await ctx.confirm(
                _(
                    "{author}, are you sure you want to offer **{quantity} {emoji}"
                    " {rarity}** crate(s) for **${price:,.0f}**?"
                ).format(
                    author=ctx.author.mention,
                    quantity=quantity,
                    emoji=getattr(self.emotes, rarity),
                    rarity=rarity,
                    price=price,
                )
        ):
            await ctx.send(_("Offer cancelled."))
            return await self.bot.reset_cooldown(ctx)

        try:
            if not await ctx.confirm(
                    _(
                        "{buyer}, {author} offered you **{quantity} {emoji} {rarity}**"
                        " crate(s) for **${price:,.0f}!** React to buy it! You have **2"
                        " Minutes** to accept the trade or the offer will be cancelled."
                    ).format(
                        buyer=buyer.mention,
                        author=ctx.author.mention,
                        quantity=quantity,
                        emoji=getattr(self.emotes, rarity),
                        rarity=rarity,
                        price=price,
                    ),
                    user=buyer,
                    timeout=120,
            ):
                await ctx.send(
                    _("They didn't want to buy the crate(s). Offer cancelled.")
                )
                return await self.bot.reset_cooldown(ctx)
        except self.bot.paginator.NoChoice:
            await ctx.send(_("They couldn't make up their mind. Offer cancelled."))
            return await self.bot.reset_cooldown(ctx)

        async with self.bot.pool.acquire() as conn:
            if not await has_money(self.bot, buyer.id, price, conn=conn):
                await ctx.send(
                    _("{buyer}, you're too poor to buy the crate(s)!").format(
                        buyer=buyer.mention
                    )
                )
                return await self.bot.reset_cooldown(ctx)
            crates = await conn.fetchval(
                f'SELECT crates_{rarity} FROM profile WHERE "user"=$1;', ctx.author.id
            )
            if crates < quantity:
                return await ctx.send(
                    _(
                        "The seller traded/opened the crate(s) in the meantime. Offer"
                        " cancelled."
                    )
                )
            await conn.execute(
                f'UPDATE profile SET "crates_{rarity}"="crates_{rarity}"-$1,'
                ' "money"="money"+$2 WHERE "user"=$3;',
                quantity,
                price,
                ctx.author.id,
            )
            await conn.execute(
                f'UPDATE profile SET "crates_{rarity}"="crates_{rarity}"+$1,'
                ' "money"="money"-$2 WHERE "user"=$3;',
                quantity,
                price,
                buyer.id,
            )
            await self.bot.log_transaction(
                ctx,
                from_=ctx.author.id,
                to=buyer.id,
                subject="crates offercrate",
                data={
                    "Quantity": quantity,
                    "Rarity": rarity,
                    "Price": price,
                },
                conn=conn,
            )
            await self.bot.log_transaction(
                ctx,
                from_=buyer.id,
                to=ctx.author.id,
                subject="crates offercrate",
                data={
                    "Price": price,
                    "Quantity": quantity,
                    "Rarity": rarity,
                },
                conn=conn,
            )

        await ctx.send(
            _(
                "{buyer}, you've successfully bought **{quantity} {emoji} {rarity}**"
                " crate(s) from {seller}. Use `{prefix}crates` to view your updated"
                " crates."
            ).format(
                buyer=buyer.mention,
                quantity=quantity,
                emoji=getattr(self.emotes, rarity),
                rarity=rarity,
                seller=ctx.author.mention,
                prefix=ctx.clean_prefix,
            )
        )


async def setup(bot):
    await bot.add_cog(Crates(bot))
