from datetime import datetime, timezone
"""
The IdleRPG Discord Bot "Echoes of Olympus"
Copyright (C) 2018-2021 Diniboy and Gelbpunkt
Copyright (C) 2023-2024 Lunar (PrototypeX37)
Copyright (C) 2025 Danaelis

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
from datetime import datetime

import discord

from discord.ext import commands

from classes.converters import IntFromTo, MemberWithCharacter, UserWithCharacter
from cogs.help import chunks
from cogs.shard_communication import user_on_cooldown as user_cooldown
from utils import misc as rpgtools
from utils import random
from utils.checks import has_char
from utils.i18n import _, locale_doc


class Marriage(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        with open("assets/data/boynames.txt") as boy_names:
            self.boynames = boy_names.readlines()
        with open("assets/data/girlnames.txt") as girl_names:
            self.girlnames = girl_names.readlines()

    def get_max_kids(self, lovescore):
        max_, missing = divmod(lovescore, 250_000)
        return 10 + max_, 250_000 - missing

    @has_char()
    @commands.guild_only()
    @commands.command(aliases=["marry"], brief=_("Propose to a player"))
    @locale_doc
    async def propose(self, ctx, partner: MemberWithCharacter):
        _(
            """`<partner>` - A discord User with a character who is not yet married

            Propose to a player for marriage. Once they accept, you are married.

            When married, your partner will get bonuses from your adventures, you can have children, which can do different things (see `{prefix}help familyevent`) and increase your lovescore, which has an effect on the [adventure bonus](https://wiki.idlerpg.xyz/index.php?title=Family#Adventure_Bonus).
            If any of you has children, they will be brought together to one family.

            Only players who are not already married can use this command."""
        )
        if partner == ctx.author:
            return await ctx.send(
                _("You should have a better friend than only yourself.")
            )
        if ctx.character_data["marriage"] != 0 or ctx.user_data["marriage"] != 0:
            return await ctx.send(_("One of you is married."))
        msg = await ctx.send(
            embed=discord.Embed(
                title=_("{author} has proposed for a marriage!").format(
                    author=ctx.disp,
                ),
                description=_(
                    "{author} wants to marry you, {partner}! React with :heart: to"
                    " marry them!"
                ).format(author=ctx.author.mention, partner=partner.mention),
                colour=0xFF0000,
            )
            .set_image(url=ctx.author.display_avatar.url)
            .set_thumbnail(
                url="http://www.maasbach.com/wp-content/uploads/The-heart.png"
            )
        )
        await msg.add_reaction("\U00002764")

        def reactioncheck(reaction, user):
            return (
                str(reaction.emoji) == "\U00002764"
                and reaction.message.id == msg.id
                and user.id == partner.id
            )

        try:
            _reaction, _user = await self.bot.wait_for(
                "reaction_add", timeout=120.0, check=reactioncheck
            )
        except asyncio.TimeoutError:
            return await ctx.send(_("They didn't want to marry."))
        async with self.bot.pool.acquire() as conn:
            check1 = await conn.fetchval(
                'SELECT marriage FROM profile WHERE "user"=$1;', ctx.author.id
            )
            check2 = await conn.fetchval(
                'SELECT marriage FROM profile WHERE "user"=$1;', partner.id
            )
            if check1 or check2:
                return await ctx.send(
                    _(
                        "Either you or your lovee married in the meantime... :broken_heart:"
                    )
                )
            async with conn.transaction():
                await conn.execute(
                    'UPDATE profile SET "marriage"=$1 WHERE "user"=$2;',
                    partner.id,
                    ctx.author.id,
                )
                await conn.execute(
                    'UPDATE profile SET "marriage"=$1 WHERE "user"=$2;',
                    ctx.author.id,
                    partner.id,
                )
                await conn.execute(
                    'UPDATE children SET "father"=$1 WHERE "father"=0 AND "mother"=$2;',
                    partner.id,
                    ctx.author.id,
                )
                await conn.execute(
                    'UPDATE children SET "father"=$1 WHERE "father"=0 AND "mother"=$2;',
                    ctx.author.id,
                    partner.id,
                )
        # we give familyevent cooldown to the new partner to avoid exploitation
        await self.bot.set_cooldown(partner.id, 1800, "familyevent")
        await ctx.send(
            _("Aww! :heart: {author} and {partner} are now married!").format(
                author=ctx.author.mention, partner=partner.mention
            )
        )

    @has_char()
    @commands.command(brief=_("Break up with your partner"))
    @locale_doc
    async def divorce(self, ctx):
        _(
            """Divorce your partner, effectively un-marrying them.

            When divorcing, any kids you have will be split between you and your partner. Each partner will get the children born with their `{prefix}child` commands.
            You can marry another person right away, if you so choose. Divorcing has no negative consequences on gameplay.

            Both players' lovescore will be reset.

            Only married players can use this command."""
        )
        if not ctx.character_data["marriage"]:
            return await ctx.send(_("You are not married yet."))
        if not await ctx.confirm(
            _(
                "Are you sure you want to divorce your partner? Some of your children"
                " may be given to your partner and your lovescore will be reset."
            )
        ):
            return await ctx.send(
                _("Cancelled the divorce. I guess the marriage is safe for now?")
            )
        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                'UPDATE profile SET "marriage"=0, "lovescore"=0 WHERE "user"=$1;',
                ctx.author.id,
            )
            await conn.execute(
                'UPDATE profile SET "marriage"=0, "lovescore"=0 WHERE "user"=$1;',
                ctx.character_data["marriage"],
            )
            await conn.execute(
                'UPDATE children SET "father"=0 WHERE "mother"=$1;', ctx.author.id
            )
            await conn.execute(
                'UPDATE children SET "father"=0 WHERE "mother"=$1;',
                ctx.character_data["marriage"],
            )
        await ctx.send(_("You are now divorced."))

    @has_char()
    @commands.command(brief=_("Show your partner"))
    @locale_doc
    async def relationship(self, ctx):
        _(
            """Show your partner's Discord Tag. This works fine across server.

            Only married players can use this command."""
        )
        if not ctx.character_data["marriage"]:
            return await ctx.send(_("You are not married yet."))
        partner = await rpgtools.lookup(self.bot, ctx.character_data["marriage"])
        await ctx.send(
            _("You are currently married to **{partner}**.").format(partner=partner)
        )

    @has_char()
    @commands.command(brief=_("Show a player's lovescore"))
    @locale_doc
    async def lovescore(self, ctx, user: UserWithCharacter = None):
        _(
            """`[user]` - The user whose lovescore to show; defaults to oneself

            Show the lovescore a player has. Lovescore can be increased by their partner spoiling them or going on dates.

            Lovescore affects the [adventure bonus](https://wiki.idlerpg.xyz/index.php?title=Family#Adventure_Bonus) and the amount of children you can have."""
        )
        user = user or ctx.author
        data = ctx.character_data if user == ctx.author else ctx.user_data
        if data["marriage"]:
            partner = await rpgtools.lookup(self.bot, data["marriage"])
        else:
            partner = _("noone")
        await ctx.send(
            _(
                "{user}'s overall love score is **{score}**. {user} is married to"
                " **{partner}**."
            ).format(user=user.name, score=data["lovescore"], partner=partner)
        )

    @has_char()
    @commands.command(brief=_("Increase your partner's lovescore"))
    @locale_doc
    async def spoil(self, ctx, item: IntFromTo(1, 40) = None):
        _(
            """`[item]` - The item to buy, a whole number from 1 to 40; if not given, displays the list of items

            Buy something for your partner to increase *their* lovescore. To increase your own lovescore, your partner should spoil you.

            Please note that these items are not usable and do not have an effect on gameplay, beside increasing lovescore.

            Only players who are married can use this command."""
        )
        lovescore_multiplier = 1

        query = '''
            SELECT "user", "tier"
            FROM profile
            WHERE "user" = $1 AND "tier" >= $2;
        '''

        result = await self.bot.pool.fetchrow(query, ctx.author.id, 3)

        if result:
            lovescore_multiplier = 1
        items = [
            (_("Cerberus Pup :dog2:"), 50),
            (_("Hecate's Cat :cat2:"), 50),
            (_("Helios's Cow :cow2:"), 75),
            (_("Athena's Owl :owl:"), 100),
            (_("Godly Sweet potato :sweet_potato:"), 2),
            (_("Godly Potato :potato:"), 1),
            (_("Karpo's Peach :peach:"), 69),
            (_("Ambrosia :ice_cream:"), 10),
            (_("Feast of Dionysus :bento:"), 500),
            (_("Hermes' Casino :ticket:"), 7500),
            (_("Olympic Games :video_game:"), 10),
            (_("Glaucus' Catch :fishing_pole_and_fish:"), 15),
            (_("Lyre Concert :musical_keyboard:"), 100),
            (_("Divine Chariot :racehorse:"), 3000),
            (_("Pegasus :airplane:"), 6000),
            (_("Sun Chariot :sunny:"), 10000),
            (_("Obol :coin:"), 25),
            (_("Sundial :watch:"), 100),
            (_("Messenger’s Tablet :iphone:"),300),
            (_("Hypnos' Bed :bed:"), 500),
            (_("Epic Poem Recital :projector:"), 750),
            (_("Talaria :wing:"), 150),
            (_("Chiton :shirt:"), 200),
            (_("Ring of Hera :ring:"), 10000),
            (_("Balloon of Zephyrus :balloon:"), 100),
            (_("Hyacinths :bouquet:"), 250),
            (_("Eros Chocolates :chocolate_bar:"), 400),
            (_("Scroll of Devotion :love_letter:"), 25000),
            (_("Labyrinth Key :key2:"), 15000),
            (_("Pandora's Box :amphora:"), 50000),
            (_("Villa of Olympus :house:"), 250000),
            (_("Aristaeus' Cheese :cheese:"), 100000),
            (_("Automaton :computer:"), 500000),
            (_("Hesperides Gems :gem:"), 75000),
            (_("Gaia's Gift :earth_africa:"), 1_000_000),
        ]
        text = _("Price")
        items_str = "\n".join(
            [
                f"{idx + 1}.) {item} ... {text}: **${price}**"
                for idx, (item, price) in enumerate(items)
            ]
        )
        if not item:
            text = _(
                "To buy one of these items for your partner, use `{prefix}spoil shopid`"
            ).format(prefix=ctx.clean_prefix)
            return await ctx.send(f"{items_str}\n\n{text}")
        item = items[item - 1]
        if ctx.character_data["money"] < item[1]:
            return await ctx.send(_("You are too poor to buy this."))
        if not ctx.character_data["marriage"]:
            return await ctx.send(_("You're not married yet."))
        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                'UPDATE profile SET "lovescore"="lovescore"+$1 WHERE "user"=$2;',
                round(item[1] * lovescore_multiplier),
                ctx.character_data["marriage"],
            )
            await conn.execute(
                'UPDATE profile SET "money"="money"-$1 WHERE "user"=$2;',
                item[1],
                ctx.author.id,
            )
            await self.bot.log_transaction(
                ctx,
                from_=ctx.author.id,
                to=2,
                subject="spoil",
                data={"Gold": item[1]},
                conn=conn,
            )
        await ctx.send(
            _(
                "You bought a **{item}** for your partner and increased their love"
                " score by **{points}** points!"
            ).format(item=item[0], points=round(item[1] * lovescore_multiplier))
        )
        user = await self.bot.get_user_global(ctx.character_data["marriage"])
        if not user:
            return await ctx.send(
                _("Failed to DM your spouse, could not find their Discord account")
            )
        await user.send(
            "**{author}** bought you a **{item}** and increased your love score by"
            " **{points}** points!".format(
                author=ctx.author, item=item[0], points=item[1]
            )
        )

    @has_char()
    @commands.command(brief=_("Take your partner on a date"))
    @locale_doc
    @user_cooldown(43200)
    async def date(self, ctx):
        _(
            """Take your partner on a date to increase *their* lovescore. To increase your own lovescore, your partner should go on a date with you.

            The lovescore gained from dates can range from 10 to 150 in steps of 10.

            Only players who are married can use this command.
            (This command has a cooldown of 12 hours.)"""
        )

        num = random.randint(50, 600) * 10
        marriage = ctx.character_data["marriage"]
        if not marriage:
            await self.bot.reset_cooldown(ctx)
            return await ctx.send(_("You are not married yet."))
        await self.bot.pool.execute(
            'UPDATE profile SET "lovescore"="lovescore"+$1 WHERE "user"=$2;',
            num,
            marriage,
        )

        partner = await self.bot.get_user_global(marriage)
        scenario = random.choice(
            [
                _("You and {partner} shared a feast at Dionysus' temple."),
                _("You and {partner} released lanterns across the Aegean Sea."),
                _("You and {partner} stargazed atop Mount Olympus."),
                _("You and {partner} listened to Apollo’s lyre at a midnight concert."),
                _("You and {partner} sailed across the wine-dark sea to Ithaca."),
                _("You and {partner} left offerings at Aphrodite’s shrine."),
                _("You and {partner} shared figs and honey in a sacred garden."),
                _("You and {partner} told stories around a fire like wandering bards."),
                _("You and {partner} raced chariots through the olive groves."),
            ]
        ).format(partner=(partner.mention if partner else _("Unknown User")))
        text = _("This increased their lovescore by {num}").format(num=num)
        await ctx.send(f"{scenario} {text}")

    async def get_random_name(self, gender, avoid):
        if gender == "f":
            data = self.girlnames
        else:
            data = self.boynames
        name = random.choice(data).strip("\n")
        while name in avoid:
            name = random.choice(data)  # avoid duplicate names
        return name

    async def lovescore_up(self, ctx, marriage, max_, missing, toomany):
        additional = (
            ""
            if not toomany
            else _(
                "You already have {max_} children. You can increase this limit"
                " by increasing your lovescores to get {amount} more."
            ).format(max_=max_, amount=f"{missing:,}")
        )
        ls = random.randint(10, 50)
        await self.bot.pool.execute(
            'UPDATE profile SET "lovescore"="lovescore"+$1 WHERE "user"=$2 OR'
            ' "user"=$3;',
            ls,
            ctx.author.id,
            marriage,
        )
        return await ctx.send(
            _(
                "You had a lovely night and gained {ls} lovescore. 😏\n\n{additional}".format(
                    ls=ls, additional=additional
                )
            )
        )

    @has_char()
    @commands.guild_only()
    @user_cooldown(3600)
    @commands.command(
        aliases=["fuck", "sex", "breed"], brief=_("Have a child with your partner")
    )
    @locale_doc
    async def child(self, ctx):
        _(
            # xgettext: no-python-format
            """Have a child with your partner.

            Children on their own don't do much, but `{prefix}familyevent` can effect your money and crates.
            To have a child, your partner has to be on the server to accept the checkbox.

            There is a 50% chance that you will have a child, and a 50% chance to just *have fun* (if you know what I'm saying) and gain between 10 and 50 lovescore.
            When you have a child, there is a 50% chance for it to be a boy and a 50% chance to be a girl.

            Your partner and you can enter a name for your child once the bot prompts you to. (Do not include `{prefix}`)
            If you fail to choose a name in time, the bot will choose one for you from about 500 pre-picked ones.

            For identification purposes, you cannot have two children with the same name in your family, so make sure to pick a unique one.

            Only players who are married can use this command.
            (This command has a cooldown of 1 hour.)"""
        )
        marriage = ctx.character_data["marriage"]
        if not marriage:
            await self.bot.reset_cooldown(ctx)
            return await ctx.send(_("Can't produce a child alone, can you?"))
        async with self.bot.pool.acquire() as conn:
            names = await conn.fetch(
                'SELECT name FROM children WHERE "mother"=$1 OR "father"=$1;',
                ctx.author.id,
            )
            spouse = await conn.fetchval(
                'SELECT lovescore FROM profile WHERE "user"=$1;', marriage
            )
        max_, missing = self.get_max_kids(ctx.character_data["lovescore"] + spouse)
        names = [name["name"] for name in names]
        user = await self.bot.get_user_global(marriage)
        if not await ctx.confirm(
            _("{user}, do you want to make a child with {author}?").format(
                user=user.mention, author=ctx.author.mention
            ),
            user=user,
        ):
            return await ctx.send(_("O.o not in the mood today?"))

        if len(names) >= max_:
            return await self.lovescore_up(ctx, marriage, max_, missing, True)

        if random.choice([True, False]):
            return await self.lovescore_up(ctx, marriage, max_, missing, False)
        gender = random.choice(["m", "f"])
        if gender == "m":
            await ctx.send(
                _(
                    "It's a boy! Your night of love was successful! Please enter a name"
                    " for your child."
                )
            )
        elif gender == "f":
            await ctx.send(
                _(
                    "It's a girl! Your night of love was successful! Please enter a"
                    " name for your child."
                )
            )

        def check(msg):
            return (
                msg.author.id in [ctx.author.id, marriage]
                and 1 <= len(msg.content) <= 20
                and msg.channel.id == ctx.channel.id
            )

        name = None
        while not name:
            try:
                msg = await self.bot.wait_for("message", check=check, timeout=30)
                name = msg.content.replace("@", "@\u200b")
            except asyncio.TimeoutError:
                name = await self.get_random_name(gender, names)
                await ctx.send(
                    _("You didn't enter a name, so we chose {name} for you.").format(
                        name=name
                    )
                )
                break
            if name in names:
                await ctx.send(
                    _(
                        "One of your children already has that name, please choose"
                        " another one."
                    )
                )
                name = None
        now = datetime.now(timezone.utc)

        # Format the date as dd/mm/yyyy
        formatted_date = now.strftime("%d/%m/%Y")
        await self.bot.pool.execute(
            'INSERT INTO children ("mother", "father", "name", "age", "gender", "birth")'
            " VALUES ($1, $2, $3, $4, $5, $6);",
            ctx.author.id,
            marriage,
            name,
            0,
            gender,
            formatted_date,
        )
        await ctx.send(_("{name} was born.").format(name=name))

    @has_char()
    @commands.command(brief=_("View your children"))
    @locale_doc
    async def family(self, ctx):
        _("""View your children. This will display their name, age and gender.""")
        try:
            marriage = ctx.character_data["marriage"]
            children = await self.bot.pool.fetch(
                'SELECT * FROM children WHERE ("mother"=$1 AND "father"=$2) OR ("father"=$1'
                ' AND "mother"=$2);',
                ctx.author.id,
                marriage,
            )

            additional = (
                _("{amount} children").format(amount=len(children))
                if len(children) != 1
                else _("one child")
            )
            em = discord.Embed(
                title=_("Your family, {additional}.").format(additional=additional),
                description=_("{author}'s family").format(author=ctx.author.mention)
                if not marriage
                else _("Family of {author} and <@{marriage}>").format(
                    author=ctx.author.mention, marriage=marriage
                ),
            )
            if not children:
                em.add_field(
                    name=_("No children yet"),
                    value=_("Use `{prefix}child` to make one!").format(
                        prefix=ctx.clean_prefix
                    )
                    if marriage
                    else _(
                        "Get yourself a partner and use `{prefix}child` to make one!"
                    ).format(prefix=ctx.clean_prefix),
                )
            if len(children) <= 5:
                for child in children:
                    em.add_field(
                        name=child["name"],
                        value=_("Gender: {gender}, Age: {age}, Born: {born}").format(
                            gender=child["gender"], age=child["age"], born=child["birth"]
                        ),
                        inline=False,
                    )
                em.set_thumbnail(url=ctx.author.display_avatar.url)
                await ctx.send(embed=em)
            else:
                embeds = []
                children_lists = list(chunks(children, 9))
                for small_list in children_lists:
                    em = discord.Embed(
                        title=_("Your family, {additional}.").format(additional=additional),
                        description=_("{author}'s family").format(author=ctx.author.mention)
                        if not marriage
                        else _("Family of {author} and <@{marriage}>").format(
                            author=ctx.author.mention, marriage=marriage
                        ),
                    )
                    for child in small_list:
                        em.add_field(
                            name=child["name"],
                            value=_("Gender: {gender}, Age: {age}, Born: {born}").format(
                                gender=child["gender"], age=child["age"], born=child["birth"]
                            ),
                            inline=True,
                        )
                    em.set_footer(
                        text=_("Page {cur} of {max}").format(
                            cur=children_lists.index(small_list) + 1,
                            max=len(children_lists),
                        )
                    )
                    embeds.append(em)
                await self.bot.paginator.Paginator(extras=embeds).paginate(ctx)
        except Exception as e:
            import traceback
            error_message = f"Error occurred: {e}\n"
            error_message += traceback.format_exc()
            await ctx.send(error_message)
            print(error_message)

    @has_char()
    @user_cooldown(1800)
    @commands.command(aliases=["fe"], brief=_("Events happening to your family"))
    @locale_doc
    async def familyevent(self, ctx):
        _(
            """Allow your children to do something, this includes a multitude of events.

            Every time you or your partner uses this command, your children:
              - have an 8/23 chance to grow older by one year
              - have a 5/23 chance to be renamed
              - have a 2/23 chance to take up to 1/64 of your money
              - have a 5/23 chance to give you up to 1/64 of your current money extra
              - have a 2/23 chance to find a random crate for you:
                + 500/761 (65%) chance for a common crate
                + 200/761 (26%) chance for an uncommon crate
                + 50/761 (6%) chance for a rare crate
                + 10/761 (1%) chance for a magic crate
                + 1/761 (0.1%) chance for a legendary crate
                + 1/761 (0.1%) chance for a fortune crate
                + 1/761 (0.1%) chance for a divine crate
              - have a 1/23 chance to die

            In each event you will know what happened.

            Only players who are married and have children can use this command.
            (This command has a cooldown of 30 minutes.)"""
        )
        name = ctx.character_data["name"]
        children = await self.bot.pool.fetch(
            'SELECT * FROM children WHERE ("mother"=$1 AND "father"=$2) OR ("father"=$1'
            ' AND "mother"=$2);',
            ctx.author.id,
            ctx.character_data["marriage"],
        )
        if not children:
            await self.bot.reset_cooldown(ctx)
            return await ctx.send(_(f"{name}, you don't have kids yet."))
        target = random.choice(children)

        event = random.choice(
            ["death"]
            + ["age"] * 6
            + ["namechange"] * 6
            + ["crate"] * 2
            + ["moneylose"] * 4
            + ["moneygain"] * 4
        )
        if event == "death":
            cause = random.choice(
                [
                    _("They tried to give Cerberus belly rubs. All three heads disagreed on where."),
                    _("They challenged Zeus to a lightning-throwing contest. They got grounded — permanently."),
                    _("They asked Medusa if her snakes needed conditioner while making direct eye contact."),
                    _("They attempted to milk the Cretan Bull. It was not in the mood. *no cheese for Stalker*"),
                    _("They tried to steal nectar and ambrosia for Stalker's midnight snack. Hera found out."),
                    _("They insisted the Hydra could be house-trained. It disagreed eight times in a row."),
                    _("They thought riding Pegasus would be easy. Gravity thought otherwise."),
                    _("They tried to tickle Hades’ skeletal guard. Cerberus wasn’t laughing."),
                    _("They listened to Stalker and suggested to Ares that war could be solved by hugs. He tested that theory with a spear."),
                    _("They told Apollo his poetry needed better rhymes. The sun god burned them with criticism."),
                    _("They entered the Labyrinth without a ball of string. The Minotaur helped them find the exit — as lunch."),
                    _("They challenged Artemis to an archery contest. She didn’t miss."),
                    _("They tried to prank Poseidon by putting dye in the ocean. He made sure they sank with style."),
                    _("They thought the Sirens were just a band needing backup vocals. They drowned mid-audition."),
                    _("They attempted to outdrink Dionysus. Their liver wasn’t divine enough."),
                    _("They asked Hephaestus if his forge doubled as a barbecue. It did — once."),
                    _("They attempted to pet Charybdis, mistaking her for a whirlpool Jacuzzi."),
                    _("They told Persephone pomegranates are overrated. She dragged them to customer service in the Underworld."),
                    _("They thought the Furies were just edgy theater kids. The curtain closed on them quickly."),
                    _("They tried to braid Medusa’s snakes into pigtails. The snakes objected."),
                    _("They asked Zeus if he ever turned into something less weird, like a potato. Thunder answered."),
                    _("They mistook a harpy for one of Stalker's chicken and offered it crackers. The harpy preferred fingers."),
                    _("They challenged Athena to chess, then rage-quit. Athena doesn’t tolerate poor sportsmanship."),
                    _("They thought Icarus just needed ‘better wax.’ History repeated itself."),
                    _("They asked Charon if the ferry ride was free on weekends. He threw in a complimentary drowning."),
                    _("They entered a footrace against Hermes while wearing sandals of lead. It was not close."),
                    _("They poked Polyphemus in his other eye ‘just to check.’ There was no other eye."),
                    _("They suggested Hera should ‘lighten up a bit.’ She did — with lightning."),
                    _("They tried to tell Hades a 'Yo Mama' joke. He introduced them to her personally."),
                    _("They thought Dionysus’ wine was just grape juice and drank the entire amphora."),
                    _("They asked the Oracle of Delphi for stock tips. The prophecy killed their portfolio and them."),
                    _("They tried to wrestle Heracles for 'training purposes.' He obliged."),
                    _("They thought Pandora’s Box was just a fancy lunchbox."),
                    _("They told Aphrodite love was overrated. She broke their heart — literally."),
                    _("They tried to sneak into Olympus disguised as a cloud. Zeus saw through it immediately."),
                    _("They asked Ares if he had ever considered pacifism. He answered with a spear."),
                    _("They thought the Stymphalian Birds just needed birdseed. The birds thought otherwise."),
                    _("They entered the Underworld with fake coins. Charon was not amused."),
                    _("They asked Perseus if his shield was just a fancy mirror. Medusa handled the rest."),
                    _("They called Poseidon 'just a glorified fisherman.' He reeled them in."),
                    _("They tried to out-sing Orpheus. Their audience disagreed violently."),
                    _("They told Hera her marriage seemed unstable. She made their life equally unstable."),
                    _("They thought Atlas just needed a hug to feel better. He dropped them along with the sky."),
                ]
            )

            await self.bot.pool.execute(
                'DELETE FROM children WHERE "name"=$1 AND (("mother"=$2 AND'
                ' "father"=$4) OR ("father"=$2 AND "mother"=$4)) AND "age"=$3;',
                target["name"],
                ctx.author.id,
                target["age"],
                ctx.character_data["marriage"],
            )
            return await ctx.send(
                _("{name} died at the age of {age}! {cause}").format(
                    name=target["name"], age=target["age"], cause=cause
                )
            )
        elif event == "moneylose":
            cause = random.choice(
                [
                    _("invested in a satyr’s scheme to bottle and sell Dionysus’ finest wine. The satyr drank the profits."),
                    _("paid a fortune for 'authentic ambrosia' that turned out to be honey mixed with goat’s milk."),
                    _("bought a 'wing maintenance kit' from Daedalus’ apprentice. The wax melted instantly."),
                    _("paid Charon for a round-trip ticket. Return trips aren’t a thing."),
                    _("bought 'Zeus-proof armor' that was just a tin suit with scorch marks."),
                    _("invested in a centaur’s idea for half-stables, half-gymnasiums. The venture collapsed halfway through."),
                    _("purchased a 'Medusa’s Mirror' guaranteed to repel gorgons. It was just polished bronze."),
                    _("funded an expedition for Stalker to retrieve Prometheus’ fire. They got burned — financially and literally."),
                    _("bought a 'trident cleaning kit' said to be blessed by Poseidon. It was just seawater in a jar."),
                    _("paid for 'Pegasus insurance' in case their flying horse got stolen. They didn’t own a Pegasus."),
                    _("invested in Hermes’ 'lightning-fast delivery service.' Their coin purse vanished instantly."),
                    _("purchased 'nectar futures' on Olympus. The market crashed after Hera’s intervention."),
                    _("paid an oracle for financial guidance. The prophecy was 'stop spending money on oracles.'"),
                    _("bought 'Titan repellent' from a cyclops merchant. The label read 'just close your eyes and hope.'"),
                    _("invested in Hephaestus’ 'self-forging hammer.' It only forged receipts."),
                    _("paid a fortune for a 'genuine Trojan horse figurine.' It contained Stalker and termites."),
                    _("bought 'Hades-brand life insurance.' Payout guaranteed… in the Underworld."),
                    _("invested in Apollo’s 'Sunlight Bottling Company.' Their bottles were empty, but bright ideas weren’t."),
                    _("purchased 'Athena-approved owl feathers for wisdom.' The owl was just molting."),
                    _("paid for a minotaur maze tour. The guide vanished, and so did their money."),
                    _("bought 'Icarus Wax' for personal flight. The only thing that soared was the price."),
                    _("invested in a philosopher’s scheme to turn olives into gold coins. All they got was salad."),
                    _("paid a cyclops blacksmith for custom armor. He only made one boot."),
                    _("paid a fortune for a vial of 'Zeus’s lightning in a bottle.' It was just static shock."),
                    _("invested in a Spartan fitness program that guaranteed 'immortality through abs.' Refunds were not part of the training."),
                    _("bought 'Poseidon’s saltwater taffy' which was literally just salty seawater in wrappers."),
                    _("purchased 'Cyclops contact lenses.' The box contained one magnifying glass."),
                    _("invested in a Minotaur corn maze attraction located in a desert. Attendance was zero."),
                    _("paid for 'Hades’ fire insurance' that only worked if they were already dead."),
                    _("bought a 'winged sandal repair kit.' It was duct tape with feathers glued on."),
                    _("invested in a philosopher’s plan to monetize Socratic questioning. Revenue: zero, arguments: infinite."),
                    _("purchased 'Athena’s genuine owl feathers' from a suspicious pigeon merchant."),
                    _("bought 'Apollo’s instant sunburn cream.' It caused instant sunburns."),
                    _("funded a satyr’s 'Pan-flute streaming service.' The only subscriber was the satyr."),
                    _("paid a fortune for 'Trojan antivirus software.' It installed more horses instead."),
                    _("invested in 'Persephone’s seasonal produce box.' Half the year there were no deliveries."),
                    _("bought 'Hercules-brand protein powder.' It was just powdered goat milk."),
                    _("funded a harpy-run courier company. All the packages got shredded mid-flight."),
                    _("paid for 'Pegasus flight lessons' using a broom with fake wings strapped on."),
                    _("bought 'authentic Siren song recordings.' It was a seashell glued to a lyre."),
                    _("invested in 'Zeus-proof umbrellas.' First thunderstorm: total loss."),
                    _("paid for 'Achilles’ unbreakable sandals.' They broke instantly."),
                    _("purchased 'Mount Olympus real estate' from Hermes. They now own three square feet of cloud."),
                    _("purchased 'siren song recordings' that were just seashells labeled 'limited edition.'"),
                                        
                ]
            )
            money = random.randint(0, int(ctx.character_data["money"] / 64))
            async with self.bot.pool.acquire() as conn:
                await conn.execute(
                    'UPDATE profile SET "money"="money"-$1 WHERE "user"=$2;',
                    money,
                    ctx.author.id,
                )
                await self.bot.log_transaction(
                    ctx,
                    from_=ctx.author.id,
                    to=2,
                    subject="Family Event",
                    data={"Gold": -money},
                    conn=conn,
                )

            return await ctx.send(
                _("{nameuser}, you lost ${money} because {name} {cause}").format(
                    nameuser=name, money=money, name=target["name"], cause=cause
                )
            )
        elif event == "moneygain":
            cause = random.choice([
                _("convinced Zeus to fund their 'cloud rental business' for mortals. Rainy profits poured in."),
                _("sold 'blessed olives from Athena’s own grove' (actually just regular olives) to gullible nobles."),
                _("opened a tourist trap called 'Labyrinth Adventures' where guests paid to get lost on purpose."),
                _("won a fortune after tricking Apollo in a poetry contest by rhyming 'sun' with 'done'."),
                _("charged admission to watch Icarus try his new wings. Tickets sold out before the crash."),
                _("convinced a cyclops to buy a two-for-one monocle set. The markup was enormous."),
                _("started a ferry service competing with Charon at half price. The Underworld had never been busier."),
                _("marketed 'Hydra water bottles' with the slogan 'For every sip you take, two more remain.'"),
                _("convinced Poseidon to sponsor their 'surfing festival.' The waves were legendary."),
                _("wrote a bestselling book titled 'How to Survive a Sphinx Riddle Contest' (spoiler: run)."),
                _("began selling 'authentic Medusa selfies' carved on stone tablets. Business was petrifyingly good."),
                _("discovered ambrosia made an excellent salad dressing and sold it to mortals for a fortune."),
                _("started a wrestling league featuring Heracles’ discarded opponents. Pay-per-view sales skyrocketed."),
                _("tricked Hades into investing in a 'haunted mansion franchise.' Mortals lined up for tickets."),
                _("won big after inventing the board game 'Raiders of Olympus.'"),
                _("organized an annual 'Chariot Grand Prix' and kept all the betting fees."),
                _("sold 'Apollo-approved sun lamps' that were just shiny bronze mirrors."),
                _("convinced Dionysus to endorse their vineyard. Wine sales tripled overnight."),
                _("charged admission to watch Sirens in concert. Earplugs sold separately for a premium."),
                _("started a 'Hero Training Bootcamp' teaching Stalker and mortals how to 'look dramatic with a spear.'"),
                _("made a fortune bottling 'Olympus air' and selling it to nobles as a vitality tonic."),
                _("started a betting ring on whether Zeus would turn into a swan, bull, or shower of gold next."),
                _("sold 'official Labyrinth maps' that were just circles, and everyone still bought them."),
                _("convinced Dionysus to sponsor their tavern crawl. Profits flowed like wine."),
                _("published 'The Hero’s Diet: Twelve Labors to a Slimmer You' and it became a bestseller."),
                _("ran a pyramid scheme selling actual pyramids to Egyptians."),
                _("charged tourists to throw coins into 'Artemis’ sacred fountain.' The fountain was a bathtub."),
                _("opened the first 'Pegasus taxi service' and charged extra for motion sickness bags."),
                _("sold 'cursed stone statues' as modern art after visiting Medusa."),
                _("opened a wrestling school called 'Wrassle Like Heracles' and tripled their investment."),
                _("collected entry fees for 'Siren Karaoke Night.' Refunds not available."),
                _("sold 'hero starter kits' with capes, fake swords, and guaranteed disappointment."),
                _("charged mortals for 'VIP Underworld Tours.' Cerberus was included in the ticket price."),
                _("organized an 'Olympic Games' knockoff called the 'Almost-Olympics' and raked in sponsorships. Stalker won it btw."),
                _("sold 'autographed thunderbolts' allegedly signed by Zeus. Buyers didn’t dare question authenticity."),
                _("rented out 'haunted amphitheaters' to poets looking for dramatic inspiration."),
                _("sold 'Poseidon-approved seashell horns' to sailors. They were just conch shells."),
                _("started 'Harpies Anonymous' support meetings and charged for membership cards."),
                _("wrote a romance novel titled 'Fifty Shades of Hades' that mortals couldn’t put down."),
                _("convinced Athena to endorse their 'Battle Tactics for Dummies' scroll series."),
      
            ])

            money = random.randint(0, int(ctx.character_data["money"] / 64))
            async with self.bot.pool.acquire() as conn:
                await conn.execute(
                    'UPDATE profile SET "money"="money"+$1 WHERE "user"=$2;',
                    money,
                    ctx.author.id,
                )
                await self.bot.log_transaction(
                    ctx,
                    from_=1,
                    to=ctx.author.id,
                    subject="FamilyEvent Money",
                    data={"Gold": money},
                    conn=conn,
                )
            return await ctx.send(
                _("{name} gave you ${money}, they {cause}").format(
                    name=target["name"], money=money, cause=cause
                )
            )
        elif event == "crate":
            type_ = random.choice(
                ["common"] * 497
                + ["uncommon"] * 199
                + ["rare"] * 50
                + ["magic"] * 7
                + ["fortune"] * 3
                + ["legendary"]
                + ["divine"]
            )
            async with self.bot.pool.acquire() as conn:
                await conn.execute(
                    f'UPDATE profile SET "crates_{type_}"="crates_{type_}"+1 WHERE'
                    ' "user"=$1;',
                    ctx.author.id,
                )
                await self.bot.log_transaction(
                    ctx,
                    from_=ctx.author.id,
                    to=2,
                    subject="FamilyEvent Crate",
                    data={"Rarity": type_, "Amount": 1},
                    conn=conn,
                )
            emoji = getattr(self.bot.cogs["Crates"].emotes, type_)
            return await ctx.send(
                _("{name} found a {emoji} {type_} crate for you!").format(
                    name=target["name"], emoji=emoji, type_=type_
                )
            )
        elif event == "age":
            await self.bot.pool.execute(
                'UPDATE children SET "age"="age"+1 WHERE "name"=$1 AND (("mother"=$2'
                ' AND "father"=$4) OR ("father"=$2 AND "mother"=$4)) AND "age"=$3;',
                target["name"],
                ctx.author.id,
                target["age"],
                ctx.character_data["marriage"],
            )
            return await ctx.send(
                _("{name} is now {age} years old.").format(
                    name=target["name"], age=target["age"] + 1
                )
            )
        elif event == "namechange":

            names = [c["name"] for c in children]
            names.remove(target["name"])
            oldname = target["name"]

            try:
                if not await ctx.confirm(
                        _(
                            '{author} you can rename {old_name}! Would you like to rename them?'
                        ).format(
                            author=ctx.author.mention,
                            old_name=target["name"],

                        )
                ):
                    await self.bot.set_cooldown(ctx, 1800)
                    return await ctx.send(_(f"You chose not to rename {oldname}."))


            except self.bot.paginator.NoChoice:
                await ctx.send(_("You didn't confirm."))
                return

            def check(msg):
                return (
                        msg.author.id in [ctx.author.id, ctx.character_data["marriage"]]
                        and msg.channel.id == ctx.channel.id
                )

            name = None
            while not name:
                await self.bot.set_cooldown(ctx, 1800)
                await ctx.send(
                    _(
                        "{name} can be renamed! Within 30 seconds, enter a new"
                        " name:\nType `cancel` to leave the name unchanged."
                    ).format(name=target["name"])
                )
                try:
                    msg = await self.bot.wait_for("message", check=check, timeout=30)
                    name = msg.content.replace("@", "@\u200b")
                except asyncio.TimeoutError:
                    return await ctx.send(_(f"{name}, you didn't enter a name."))
                if name.lower() == "cancel":
                    return await ctx.send(_(f"{name}, you didn't want to rename."))
                if len(name) == 0 or len(name) > 20:
                    await ctx.send(_(f"{name}, you must be 1 to 20 characters only."))
                    name = None
                    continue
                if name in names:
                    await ctx.send(
                        _(
                            "One of your children already has that name, please choose"
                            " another one."
                        )
                    )
                    name = None
                    continue
                try:
                    if not await ctx.confirm(
                            _(
                                '{author} Are you sure you want to rename "{old_name}" to'
                                ' "{new_name}"?'
                            ).format(
                                author=ctx.author.mention,
                                old_name=target["name"],
                                new_name=name,
                            )
                    ):
                        await ctx.send(
                            _('You didn\'t change the name to "{new_name}".').format(
                                new_name=name
                            )
                        )
                        name = None

                except self.bot.paginator.NoChoice:
                    await ctx.send(_(f"{name}, you didn't confirm."))
                    name = None

            if name == target["name"]:
                return await ctx.send(_(f"{name}, you didn't change their name."))
            await self.bot.pool.execute(
                'UPDATE children SET "name"=$1 WHERE "name"=$2 AND (("mother"=$3 AND'
                ' "father"=$5) OR ("father"=$3 AND "mother"=$5)) AND "age"=$4;',
                name,
                target["name"],
                ctx.author.id,
                target["age"],
                ctx.character_data["marriage"],
            )
            return await ctx.send(
                _("{old_name} is now called {new_name}.").format(
                    old_name=target["name"], new_name=name
                )
            )


async def setup(bot):
    await bot.add_cog(Marriage(bot))
