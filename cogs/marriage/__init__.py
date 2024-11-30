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
            (_("Dog :dog2:"), 50),
            (_("Cat :cat2:"), 50),
            (_("Cow :cow2:"), 75),
            (_("Penguin :penguin:"), 100),
            (_("Unicorn :unicorn:"), 1000),
            (_("Potato :potato:"), 1),
            (_("Sweet potato :sweet_potato:"), 2),
            (_("Peach :peach:"), 5),
            (_("Ice Cream :ice_cream:"), 10),
            (_("Bento Box :bento:"), 50),
            (_("Movie Night :ticket:"), 75),
            (_("Video Game Night :video_game:"), 10),
            (_("Camping Night :fishing_pole_and_fish:"), 15),
            (_("Couple Competition :trophy:"), 30),
            (_("Concert Night :musical_keyboard:"), 100),
            (_("Bicycle :bike:"), 100),
            (_("Motorcycle :motorcycle:"), 250),
            (_("Car :red_car:"), 300),
            (_("Private Jet :airplane:"), 1000),
            (_("Space Rocket :rocket:"), 10000),
            (_("Credit Card :credit_card:"), 20),
            (_("Watch :watch:"), 100),
            (_("Phone :iphone:"), 100),
            (_("Bed :bed:"), 500),
            (_("Home films :projector:"), 750),
            (_("Satchel :school_satchel:"), 25),
            (_("Purse :purse:"), 30),
            (_("Shoes :athletic_shoe:"), 150),
            (_("Casual Attire :shirt:"), 200),
            (_("Ring :ring:"), 1000),
            (_("Balloon :balloon:"), 10),
            (_("Flower Bouquet :bouquet:"), 25),
            (_("Expensive Chocolates :chocolate_bar:"), 40),
            (_("Declaration of Love :love_letter:"), 50),
            (_("Key to Heart :key2:"), 100),
            (_("Ancient Vase :amphora:"), 15000),
            (_("House :house:"), 25000),
            (_("Super Computer :computer:"), 50000),
            (_("Precious Gemstone Collection :gem:"), 75000),
            (_("Planet :earth_americas:"), 1_000_000),
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
        if ctx.author.id == 292893909384822786:
            num = random.randint(1, 100) * 10

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
                _("You and {partner} went on a nice candlelit dinner."),
                _("You and {partner} had stargazed all night."),
                _("You and {partner} went to a circus that was in town."),
                _("You and {partner} went out to see a romantic movie."),
                _("You and {partner} went out to get ice cream."),
                _("You and {partner} had an anime marathon."),
                _("You and {partner} went for a spontaneous hiking trip."),
                _("You and {partner} decided to visit Paris."),
                _("You and {partner} went ice skating together."),
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
        now = datetime.now()

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
        children = await self.bot.pool.fetch(
            'SELECT * FROM children WHERE ("mother"=$1 AND "father"=$2) OR ("father"=$1'
            ' AND "mother"=$2);',
            ctx.author.id,
            ctx.character_data["marriage"],
        )
        if not children:
            await self.bot.reset_cooldown(ctx)
            return await ctx.send(_("You don't have kids yet."))
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
                    _("They died because of a shampoo overdose!"),
                    _("They died of lovesickness..."),
                    _("They've died of age."),
                    _("They died of loneliness."),
                    _("A horde of goblins got them."),
                    _(
                        "They have finally decided to move out after all these years,"
                        " but couldn't survive a second alone."
                    ),
                    _("Spontaneous combustion removed them from existence."),
                    _("While exploring the forest, they have gotten lost."),
                    _("They've left through a portal into another dimension..."),
                    _(
                        r"The unbearable pain of stepping on a Lego\© brick killed them."  # noqa
                    ),
                    _("You heard a landmine going off nearby..."),
                    _("They have been abducted by aliens!"),
                    _("The Catholic Church got them..."),
                    _("They starved after becoming a communist."),
                    _("A rogue rubber chicken slapped them to oblivion."),
                    _("They laughed too hard at their own joke."),
                    _("They choked on air... it's more common than you think."),
                    _("They were squashed flat by a runaway pancake."),
                    _("Drowned in a sea of glitter."),
                    _("Tried to high-five a unicorn and missed."),
                    _("An unexpected pineapple uprising was the cause."),
                    _("Got sucked into a giant tea cup during a mad tea party."),
                    _("Was hugged a tad too tight by an overzealous teddy bear."),
                    _("Suffocated in a room filled with bubble wrap pops."),
                    _("A mime trapped them inside an invisible box."),
                    _("Attacked by a savage troop of giggling baby ducks."),
                    _("Slipped on a banana peel... in space."),
                    _("A surprise guacamole flood took them away."),
                    _("They tried to smell what the Rock was cooking."),
                    _("Mistakenly joined a squirrel flash mob."),
                    _("Failed to resist the urge to touch a big red button labeled 'Do Not Press'."),
                    _("Taken out by a rogue frisbee."),
                    _("Squirted to oblivion by a malfunctioning water gun."),
                    _("Defeated in an epic dance-off by an elderly sloth."),
                    _("The cookies they tried to steal from the cookie jar rebelled."),
                    _("Lost in a particularly challenging corn maze."),
                    _("They took the term 'sleeping with the fishes' too literally."),
                    _("Got tangled in an infinite loop of shoelaces."),
                    _("Died waiting for a very slow sloth to finish its joke."),
                    _("Dragged into the depths by an angry rubber ducky."),
                    _("Mistook quicksand for a comfy beanbag."),
                    _("Their pet rock turned on them."),
                    _("Tried to take on a sparrow in a chirping contest."),
                    _("Bounced to oblivion on a particularly springy trampoline."),
                    _("Bitten by a voracious and very hangry vegetarian vampire."),
                    _("Died trying to prove chickens can indeed fly."),
                    _("Engulfed by a rogue tidal wave of chocolate milk."),
                    _("Misjudged the trajectory during a moonwalk dance move."),
                    _("Was playing hide-and-seek. Never found."),
                    _("Caught in a marshmallow avalanche while camping."),
                    _("Accidentally turned into a frog while learning magic. Couldn’t ribbit back."),
                    _("Entangled in an intense yodeling competition."),
                    _("Went in search of the end of the rainbow. It was a slippery slope."),
                    _("Struck by a shooting star while making a wish."),
                    _("Lost in a tickle war against a feather duster."),
                    _("Eaten by a ferocious, cookie-craving cookie monster."),
                    _("Got trapped inside a runaway hamster ball."),
                    _("Taken out by an aggressive hoard of manic garden gnomes."),
                    _("Disappeared after attempting to milk a particularly stubborn cow."),
                    _("Swallowed by a giant venus flytrap while attempting to take a selfie."),
                    _("Caught up in a wild stampede of fluffy bunnies."),
                    _("Took a detour through a wormhole while going to the grocery store."),
                    _("Got caught in the crossfire during a furious pillow fight."),
                    _("Buried under a mountain of out-of-control spaghetti."),
                    _("Distracted by a cat video and never returned."),
                    _("Stuck forever trying to get a particularly stubborn song out of their head."),
                    _("Vanished after trying to tame a rebellious vacuum cleaner."),
                    _("Died of laughter during a mime’s performance."),
                    _("Attempted to bungee jump using spaghetti. It wasn’t al dente enough."),
                    _("Locked in an eternal dance with a spirited disco ball."),
                    _("Last seen chasing a very determined and fast tortoise."),
                    _("Caught in a sudden downpour of molten fondue."),
                    _("Died of sheer surprise when their plant actually grew."),
                    _("Lost in a deep philosophical debate with a parrot."),
                    _("Met their end in a fierce battle with a sentient vacuum cleaner. Dust bunnies were the casualties."),
                    _("Tragically drowned in a sea of unopened takeout menus. Their final meal remains a mystery."),
                    _("Succumbed to 'Extreme Procrastination Syndrome'. The to-do list outlived them."),
                    _("Lost a debate with a houseplant. Turns out, ferns are surprisingly convincing."),
                    _("Met their match in a thumb war with a particularly competitive thumb wrestler."),
                    _("Was outwitted by a cunning coffee mug in a game of chess. The king got checkmated by caffeine."),
                    _("Challenged a banana peel to a duel. Slipped on their own terms."),
                    _("Engaged in a karaoke battle with a tone-deaf parrot. The parrot emerged victorious."),
                    _("Met their fate in a duel against a rogue spaghetti noodle. Carb combat is unpredictable."),
                    _("Dared to defy gravity while attempting a moonwalk on an escalator. Lost the rhythm."),
                    _("Engaged in a heated staring contest with a computer screen. Screen blinked first."),
                    _("Challenged a rubber chicken to a stand-up comedy showdown. The chicken's punchlines were eggstraordinary."),
                    _("Lost a battle against a self-assembling furniture kit. The instructions remained an enigma."),
                    _("Fell victim to an ambush by killer puns. The puns were armed and dadly."),
                    _("Met their demise while trying to tame a rebellious GPS. The coordinates led to chaos."),
                    _("Engaged in a rap battle with a malfunctioning printer. The printer dropped the beats."),
                    _("Lost a thumb war against a robotic hand. The hand was too 'digit'-ally advanced."),
                    _("Succumbed to 'Extreme Sarcasm Overdose'. Their last words were eye-rolling."),
                    _("Challenged a rubber duck to a staring contest. Quack stared back."),
                    _("Met an unfortunate end in a 'Jumping Jacks' competition with a kangaroo. The kangaroo had the hops."),
                    _("Engaged in a pillow fight with a ninja pillow. The fluff was deadly."),
                    _("Lost a race against time in a 'Speed Typing' competition. Auto-correct mocked their haste."),
                    _("Succumbed to laughter while trying to teach a cat to laugh. The cat remained unamused."),
                    _("Challenged a mirror to a duel of wits. The mirror reflected their lack of wisdom."),
                    _("Met their untimely end in a thumb wrestling match with a thumb wrestling champion. The thumb was too formidable."),
                    _("Engaged in a hot sauce tasting contest. It was a spicy demise."),
                    _("Lost a game of 'Hide and Seek' with an invisible friend. The friend remained unseen."),
                    _("Succumbed to the chaos of a 'Rock, Paper, Scissors, Lizard, Spock' marathon. The lizard was the ultimate victor."),
                    _("Challenged a rubber tree to a 'Flexibility Showdown'. The tree outbent them."),
                    _("Met their match in a 'Who Can Roll Their Eyes the Most' competition. Eyeballs were exhausted."),
                    _("Engaged in a fierce thumb war with a smartphone. The touchscreen prevailed."),
                    _("Lost a debate with a wise-cracking refrigerator. The fridge's cool logic was unbeatable."),
                    _("Succumbed to 'Extreme Marshmallow Roasting'. The marshmallows were too toasty."),
                    _("Challenged a rubber band to a 'Stretching Showdown'. The rubber band snapped back."),
                    _("Met their match in a 'Who Can Make the Most Annoying Sound' contest. The winner was ear-resistible."),
                    _("Engaged in a 'Quietest Whistle' competition. The silence was deafening."),
                    _("Lost a staring contest against a mirror ball. Disco dazzled them into submission."),
                    _("Succumbed to the allure of a 'Tickle Me Elmo' rampage. Laughter was the cause."),
                    _("Challenged a magic 8-ball to a fortune-telling duel. The responses were mysteriously unfavorable."),
                    _("Met their end in a 'Balancing Act' with a stack of pancakes. The syrupy collapse was tragic."),
                    _("Engaged in a 'Bubble Wrap Popping' contest. The pops were their final symphony."),
                    _("Lost a chess match against a pigeon. The pigeon played fowl."),
                    _("Succumbed to an epic 'Battle of the Air Guitars'. The imaginary riff was too electrifying."),
                    _("Challenged a rubber chicken to a 'Dad Joke Duel'. The chicken's jokes were eggsquisite."),
                    _("Met their match in a 'Who Can Whisper the Loudest' competition. Silence spoke volumes."),
                    _("Engaged in a 'Most Dramatic Sigh' contest. The sighs were tragically profound."),
                    _("Lost a 'Silent Scream' competition. The quietest scream was hauntingly muted."),
                    _("Succumbed to the mystery of a 'Disappearing Act' gone wrong. The reappearing was elusive."),
                    _("Challenged a mirror to a 'Who Can Reflect the Most' contest. Reflections were overwhelming."),
                    _("Met their end in a 'Tightrope Walk' over a puddle of spilled coffee. The balance was too caffeinated."),
                    _("Engaged in a 'Paper Airplane' dogfight. The paper cuts were airborne."),
                    _("Lost a 'Gum Bubble' inflating competition. The bubble burst was gumtastic."),
                    _("Succumbed to a 'Thumb Wrestling' match with a sticky note. The adhesive was unbeatable."),
                    _("Challenged a rubber duck to a 'Quack Off'. The duck quacked them up."),
                    _("Met their match in a 'Who Can Blink the Slowest' contest. Blinking was defeated."),
                    _("Engaged in a 'Bubblegum Bubble Popping' marathon. The gum exploded."),
                    _("Lost a 'Funny Face' competition with a mirror. The mirror cracked up."),
                    _("Succumbed to a 'Finger Snap' duel. The snaps were too snappy."),
                    _("Challenged a rubber chicken to a 'Dance Off'. The chicken had killer moves."),
                    _("Met their end in a 'Whistle While You Work' competition. The work whistled back."),
                    _("Engaged in a 'Duct Tape Sculpture' showdown. The tape was too sticky."),
                    _("Lost a 'Thumb War' with a stapler. The stapler was unyielding."),
                    _("Succumbed to a 'Balancing Act' on a seesaw. The seesaw saw their downfall."),
                    _("Challenged a rubber band to a 'Ping Pong' match. The band pinged them off the table."),
                    _("Met their match in a 'Who Can Juggle Water Balloons' contest. The balloons burst."),
                    _("Engaged in a 'Pillow Fight' with a marshmallow pillow. The fluff was fierce."),
                    _("Lost a 'Tongue Twister' battle with a parrot. The parrot twisted tongues."),
                    _("Succumbed to an 'Epic Eyebrow Raise' competition. The brows reached new heights."),
                    _("Challenged a rubber chicken to a 'Knee Slapping' contest. The chicken's slaps were knee-slappers."),
                    _("Met their end in a 'Hula Hoop' duel. The hoop hooped them out of existence."),
                    _("Engaged in a 'Spatula Flip' showdown. The flip was spectacular."),
                    _("Lost a 'Who Can Whistle the Loudest Without Whistling' competition. Silence was deafening."),
                    _("Succumbed to a 'Staring Contest' with a mirror ball. The disco dazzled them."),
                    _("Challenged a rubber duck to a 'Synchronized Quacking' competition. The duck quacked in harmony."),
                    _("Met their match in a 'Who Can Tie the Most Confusing Knots' contest. Knots were too tangled."),
                    _("Engaged in a 'Marshmallow Roasting' competition with a dragon. The dragon's breath was fiery."),
                    _("Lost a 'Balloon Animal' battle with a balloon octopus. The octopus ballooned out of control."),
                    _("Succumbed to an 'Epic Pillow Fort Collapse'. The fort crumbled."),
                    _("Challenged a rubber band to a 'Rubber Band Guitar' showdown. The band played them out."),
                    _("Met their end in a 'Who Can Hula Hoop the Longest' competition. The hoop outlasted them."),
                    _("Engaged in a 'Bubble Wrap' popping marathon. The pops were poppin'."),
                    _("Lost a 'Who Can Balance a Teacup on Their Head' contest. The teacup toppled."),
                    _("Succumbed to a 'Duct Tape Fashion Showdown'. The tape was too fashionable."),
                    _("Challenged a rubber chicken to a 'Staring Contest'. The chicken blinked them away."),
                    _("Met their match in a 'Who Can Eat the Most Jellybeans with Chopsticks' contest. Jellybeans rolled away."),
                    _("Engaged in a 'Spoon Balancing' showdown. The spoons were too spoonish."),
                    _("Lost a 'Bubblegum Bubble Blowing' competition. The bubble burst was bubbly."),
                    _("Succumbed to a 'Thumb Wrestling' match with a thumbtack. The thumbtack was pointy."),
                    _("Challenged a rubber duck to a 'Dance-Off'. The duck had quacktastic moves."),
                    _("Met their end in a 'Who Can Tangle Christmas Lights the Most' contest. Lights were too festive."),
                    _("Engaged in a 'Who Can Whisper the Loudest' competition. The whispers were deafening."),
                    _("Lost a 'Who Can Hug a Cactus the Longest' contest. The cactus was prickly."),
                    _("Succumbed to a 'Potato Sack Race' with a kangaroo. The sack was too sacky."),
                    _("Challenged a rubber band to a 'Tug-of-War'. The band snapped back."),
                    _("Met their match in a 'Who Can Juggle the Most Water Balloons' contest. Balloons burst."),
                    _("Engaged in a 'Thumb War' with a stapler. The stapler was unyielding."),
                    _("Lost a 'Who Can Balance a Teacup on Their Head' contest. The teacup toppled."),
                    _("Succumbed to a 'Duct Tape Fashion Showdown'. The tape was too fashionable."),
                    _("Challenged a rubber chicken to a 'Staring Contest'. The chicken blinked them away."),
                    _("Met their match in a 'Who Can Eat the Most Jellybeans with Chopsticks' contest. Jellybeans rolled away."),
                    _("Engaged in a 'Spoon Balancing' showdown. The spoons were too spoonish."),
                    _("Lost a 'Bubblegum Bubble Blowing' competition. The bubble burst was bubbly."),
                    _("Succumbed to a 'Thumb Wrestling' match with a thumbtack. The thumbtack was pointy."),
                    _("Challenged a rubber duck to a 'Dance-Off'. The duck had quacktastic moves."),
                    _("Met their end in a 'Who Can Tangle Christmas Lights the Most' contest. Lights were too festive."),
                    _("Engaged in a 'Who Can Whisper the Loudest' competition. The whispers were deafening."),
                    _("Lost a 'Who Can Hug a Cactus the Longest' contest. The cactus was prickly."),
                    _("Succumbed to a 'Potato Sack Race' with a kangaroo. The sack was too sacky."),
                    _("Challenged a rubber band to a 'Tug-of-War'. The band snapped back."),
                    _("Met their match in a 'Who Can Juggle the Most Water Balloons' contest. Balloons burst."),
                    _("Engaged in a 'Staring Contest' with a chameleon. The chameleon blended in, and they never saw it coming."),
                    _("Lost a 'Who Can Hold Their Breath the Longest' contest underwater. Forgot they weren't amphibious."),
                    _("Succumbed to a 'Marshmallow Sword Fight' with a marshmallow ninja. The marshmallow katana was unbeatable."),
                    _("Challenged a rubber chicken to a 'Pillow Fight'. The chicken fluffed them out of existence."),
                    _("Met their end in a 'Who Can Drink the Most Invisible Potion' contest. Forgot they were participating."),
                    _("Engaged in a 'Paper Airplane' dogfight with a paper airplane pilot. The paper cuts were aerial."),
                    _("Lost a 'Who Can Mime the Longest' competition. The invisible box became their eternal stage."),
                    _("Succumbed to a 'Duct Tape Escapade'. Tried to break free but got stuck in a sticky situation."),
                ])


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
                    _(
                        "fell in love with a woman on the internet, but the woman was a"
                        " man and stole their money."
                    ),
                    _("has been arrested and had to post bail."),
                    _("bought fortnite skins with your credit card."),
                    _("decided to become communist and gave the money to others."),
                    _("was caught pickpocketing and you had to pay the fine."),
                    _("gave it to a beggar."),
                    _("borrowed it to attend the local knights course."),
                    _("spent it in the shop."),
                    _("bought some toys."),
                    _("has gambling addiction and lost the money..."),
                    _("they trusted Honey to gamble for them..."),
                    _("tried to invest in 'underwater basket weaving' classes."),
                    _("backed a Kickstarter for 'invisible socks'. Guess they were *too* invisible."),
                    _("thought they found a unicorn breeding farm and invested heavily."),
                    _("bought the Brooklyn Bridge from a very 'trustworthy' salesman."),
                    _("enrolled in a 'How to Grow Money Trees' seminar."),
                    _("tried to bribe a squirrel for its 'magic' acorns."),
                    _("paid to become a certified ninja... at 'Shady's Ninja School'."),
                    _("invested in a 'lunar real estate' opportunity."),
                    _("purchased a DIY teleportation kit online. Still waiting for it."),
                    _("got a premium subscription to 'Whale Whisperers Monthly'."),
                    _("bought a rare painting. Turns out it was just modern art drawn by a toddler."),
                    _("funded a time travel startup. Apparently, it's coming 'any day now'."),
                    _("acquired an iceberg believing it to be a diamond mine."),
                    _("bought tickets for the 'Annual Invisible Circus'. Still trying to find the venue."),
                    _("paid a mime to speak."),
                    _("invested in bottled air. Turns out it wasn't a breath of fresh air."),
                    _("bought a pet rock's luxury mansion."),
                    _("tried to buy magic beans. Just got regular beans."),
                    _("funded a movie titled 'Watching Paint Dry'. Critics called it 'riveting'."),
                    _("purchased an 'autographed' picture of Bigfoot."),
                    _("enrolled in a school for wizards. The headmaster? Larry Botter."),
                    _("ordered a potion to become a mermaid. Now they have glittery bathwater."),
                    _("invested in sandcastles thinking they were beachfront property."),
                    _("bought stocks in 'Canned Unicorn Laughter'. Turns out, it's just regular air."),
                    _("purchased an all-access pass to Cloud Nine. Waiting for the ladder."),
                    _("bankrolled a snail racing league. It's... progressing... slowly."),
                    _("hired a personal trainer for their pet fish."),
                    _("got VIP tickets to a 'Whack-a-Mole Championship'. There was no 'hole' lot of action."),
                    _("took a gourmet course titled '50 Ways to Boil Water'."),
                    _("commissioned a portrait of their shadow."),
                    _("paid for a 'Haunted Toaster'. It only spooks the bread."),
                    _("sponsored an expedition to find the edge of their flat globe."),
                    _("bought a DIY kit: 'Build Your Own Air Guitar'."),
                    _("financed a documentary on the wild life of sock puppets."),
                    _("ordered gourmet diet water for their new health regimen."),
                    _("bought a bridge in the Sahara. Claims it's a 'hot' property."),
                    _("invested in 'Penguin Flying Lessons'. The penguins still prefer to waddle."),
                    _("purchased exclusive rights to a mime's podcast."),
                    _("hired a detective to find out where the sun goes at night."),
                    _("got an e-book on 'How to Learn Telepathy'. Still waiting for it to download to their brain."),
                    _("bought a magic carpet. It doesn't fly, but vacuums itself."),
                    _("purchased a rare, invisible pet. Keeps forgetting where they put it."),
                    _("financed the creation of a chocolate teapot."),
                    _("ordered 'Low Fat Water' from a TV infomercial."),
                    _("paid to watch a 3-day marathon of 'The Grass Growing Channel'."),
                    _("invested in a 'Whiskey Fountain' startup but it only poured regrets."),
                    _("bought a 'Chocolate Jacuzzi' thinking it would be sweet, ended up with a sticky mess."),
                    _("sponsored a 'Synchronized Wine Tasting' team; they synchronized stumbling instead."),
                    _("tried to patent a 'Mind-Reading Pillow' for dream analysis; it just snores."),
                    _("enrolled in 'Advanced Potato Photography' hoping for spud glamour shots."),
                    _("invested in 'Wearable Blanket Stocks' for a cozy financial future."),
                    _("ordered a 'DIY Love Potion' online; now the cat won't stop following them."),
                    _("tried to buy 'Intergalactic Real Estate'; turns out, extraterrestrials don't do mortgages."),
                    _("funded a study on 'Romantic Chemistry'; results were more explosions than sparks."),
                    _("bought a 'DIY Romance Novel' kit but ended up with a steamy plot twist."),
                    _("invested in 'Personalized Pick-Up Lines'; delivery guy just handed them a pizza."),
                    _("ordered a 'Love Spell Candle'; it only attracted moths."),
                    _("sponsored a 'Matchmaking Fortune Cookie' company; all fortunes said 'try another cookie'."),
                    _("tried to patent 'Flirting in Morse Code' but only attracted confused bees."),
                    _("enrolled in a class on 'Whispering Sweet Nothings to Succulents' for platonic relationships."),
                    _("bought a 'DIY Massage Chair'; it just vibrates with disappointment."),
                    _("invested in a startup that promised 'Relationship GPS'; it led to the friend zone."),
                    _("ordered a 'Candlelit Dinner for One'; the candle burned out before the microwave beeped."),
                    _("sponsored a seminar on 'Finding Your Soulmate in a Haystack'; ended up with a needle."),
                    _("tried to patent a 'Hug Subscription Service'; got tangled in the fine print."),
                    _("bought a 'DIY Compliment Generator'; it only says 'nice try' repeatedly."),
                    _("invested in 'Virtual High-Five Stocks'; market crashed with a low slap."),
                    _("ordered a 'Love Potion Perfume'; now the neighbors' dogs won't stop following."),
                    _("tried to patent 'Emoji Flirting'; just confused everyone with eggplants."),
                    _("enrolled in 'Advanced Hugging Techniques'; turns out, tight squeezes are just awkward."),
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
                _("You lost ${money} because {name} {cause}").format(
                    money=money, name=target["name"], cause=cause
                )
            )
        elif event == "moneygain":
            cause = random.choice([
                _("discovered a loophole in the space-time continuum and cashed in on future earnings."),
                _("trained squirrels to pickpocket for them. Acorns aren't the only nuts they're collecting now!"),
                _("became a professional procrastinator and delayed getting rich until the last possible moment."),
                _("mastered the art of selling virtual real estate in their dreams. The market is imaginary, but the profits are real!"),
                _("invented 'inflatable money' - because who needs real currency when you can have bounceable bills?"),
                _("started a business selling 'dehydrated water'. Just add water to experience the wetness!"),
                _("organized a 'Hide and Seek' championship in a mirrored maze. Still waiting for someone to win."),
                _("offered 'Thought Delivery' services. Just think about what you want, and they'll send it to you... eventually."),
                _("became a professional mime for introverted cats. The applause is silent, but the tuna treats are real."),
                _("invented 'reverse psychology fortune cookies'. They tell you your future, but it's always wrong, so you prove them otherwise."),
                _("taught cats how to use smartphones and started an Instagram account for them. #PurrfectSelfies"),
                _("marketed 'invisible ink' for e-books. Now you can see exactly what you're not reading!"),
                _("offered a course on 'How to Win Arguments with a Goldfish'. Spoiler: They always forget the point."),
                _("became a time-traveling therapist for stressed-out dinosaurs. The past has never felt so present."),
                _("started a 'Telepathic Karaoke' club. It's all in your head, but the reviews are out of this world!"),
                _("sold 'DIY Cloning Kits'. Now everyone can have a twin, even if it's just a potted plant."),
                _("became a life coach for philosophical robots. Helping them find meaning in binary."),
                _("organized a 'World's Shortest Marathon' – the finish line is just a step away!"),
                _("invented 'silent fireworks'. Explosive colors, zero noise – perfect for introverted celebrations!"),
                _("started a 'Reverse Escape Room' where you pay to let others lock you in. It's oddly liberating."),
                _("trained hamsters as motivational speakers. Their motto: 'Run the wheel of life with enthusiasm!'"),
                _("became a professional 'Napper's Delight' consultant. Helping you achieve the perfect siesta."),
                _("invented 'self-igniting candles'. Because sometimes you just need a little spark."),
                _("started a 'Reverse Diet Plan'. You eat more, and the scale shows less."),
                _("organized a 'Wink-and-a-Nod' club. Membership is implied."),
                _("sold 'Invisible Ink Tattoos'. Keeping your secrets skin-deep."),
                _("became a 'Cupid's Sidekick'. Assisting in love, one arrow at a time."),
                _("invented 'Whispering Yoga'. Because relaxation should be hush-hush."),
                _("started a 'Grown-Up Blanket Fort' business. Building walls of sophistication."),
                _("offered a 'Sassy Fortune Cookie' service. Sarcasm, but make it prophetic."),
                _("became a 'Pillow Fight Referee'. Ensuring fluff and fair play."),
                _("invented 'Adulthood Amnesia Pills'. Forget bills, remember fun."),
                _("organized a 'Subtle Pickup Line' seminar. Flirting without the cringe."),
                _("sold 'Invisible Handcuffs'. Commitment, but make it incognito."),
                _("started a 'Pro-level Hide and Seek' league. Seeking is optional."),
                _("trained cats as 'Therapists with Fur'. Purring heals all wounds."),
                _("invented 'Silent Movie Karaoke'. Mime along to your favorite scenes."),
                _("opened a 'Confidential Compliments' agency. Complimenting you discreetly."),
                _("offered 'Adulting Excuse Cards'. Because sometimes you just need a break."),
                _("became a 'Nightstand Comedian'. Jokes that won't wake the neighbors."),
                _("invented 'Serious Whoopee Cushions'. Because maturity needs humor."),
                _("started a 'Wine Tasting for Beginners' class. Sip, don't spill."),
                _("sold 'Invisible Ties'. Formality without the fuss."),
                _("organized a 'Grown-Up Treasure Hunt'. The prize? A good bottle of wine."),
                _("offered 'Customized Sarcasm Lessons'. Tailored snark for every occasion."),
                _("became a 'Professional Secret Agent'. Keeping your secrets, well, secret."),
                _("invented 'Reverse Aging Cream'. Embrace the wisdom, keep the looks."),
                _("started a 'Whispered Jazzercise' class. Burning calories in hushed tones."),
                _("sold 'Unspoken Promises'. No commitments, just unspoken intentions."),
                _("trained squirrels as 'Relationship Therapists'. Nutty problems, serious solutions."),
                _("offered 'Low-Key Life Coaching'. Because not every goal needs to be shouted."),
                _("became a 'Mime Life Coach'. Actions speak louder than words, silently.")
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

            def check(msg):
                return (
                    msg.author.id in [ctx.author.id, ctx.character_data["marriage"]]
                    and msg.channel.id == ctx.channel.id
                )

            name = None
            while not name:
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
                    return await ctx.send(_("You didn't enter a name."))
                if name.lower() == "cancel":
                    return await ctx.send(_("You didn't want to rename."))
                if len(name) == 0 or len(name) > 20:
                    await ctx.send(_("Name must be 1 to 20 characters only."))
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
                        await self.bot.set_cooldown(ctx, 1800)
                except self.bot.paginator.NoChoice:
                    await ctx.send(_("You didn't confirm."))
                    name = None

            if name == target["name"]:
                return await ctx.send(_("You didn't change their name."))
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
