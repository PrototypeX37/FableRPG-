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
import math
import random as randomm
import utils.misc as rpgtools
from decimal import Decimal, ROUND_HALF_UP

from collections import deque
from decimal import Decimal

import random as rnd

import discord

from discord.enums import ButtonStyle
from discord.ext import commands
from discord.ui.button import Button

from classes.converters import IntFromTo
from classes.classes import Raider
from classes.classes import from_string as class_from_string
from cogs.help import chunks
from cogs.shard_communication import user_on_cooldown as user_cooldown
from utils import random
from utils.checks import has_char, is_gm
from utils.i18n import _, locale_doc
from utils.joins import JoinView


class Tournament(commands.Cog):
    def __init__(self, bot):
        self.deffbuff = 1
        self.bot = bot
        self.dmgbuff = 1

    def get_dmgbuff(self):
        return self.dmgbuff

    def get_deffbuff(self):
        return self.deffbuff

    async def get_raidstatsjug(
            self,
            thing,
            atkmultiply=None,
            defmultiply=None,
            classes=None,
            race=None,
            guild=None,
            god=None,
            conn=None,
    ):
        """Generates the raidstats for a user"""
        v = thing.id if isinstance(thing, (discord.Member, discord.User)) else thing
        local = False
        if conn is None:
            conn = await self.bot.pool.acquire()
            local = True
        if (
                atkmultiply is None
                or defmultiply is None
                or classes is None
                or guild is None
        ):
            row = await conn.fetchrow('SELECT * FROM profile WHERE "user"=$1;', v)
            atkmultiply, defmultiply, classes, race, guild, user_god = (
                row["atkmultiply"],
                row["defmultiply"],
                row["class"],
                row["race"],
                row["guild"],
                row["god"],
            )
            if god is not None and god != user_god:
                raise ValueError()
        damage, armor = await self.bot.get_damage_armor_for(
            v, classes=classes, race=race, conn=conn
        )
        if buildings := await self.bot.get_city_buildings(guild, conn=conn):
            atkmultiply += buildings["raid_building"] * Decimal("0.1")
            defmultiply += buildings["raid_building"] * Decimal("0.1")
        classes = [class_from_string(c) for c in classes]
        tournament_instance = Tournament(self)
        dmgbuff = self.dmgbuff
        deffbuff = self.deffbuff

        atkmultiply = atkmultiply + dmgbuff
        defmultiply = defmultiply + deffbuff
        dmg = damage * atkmultiply
        deff = armor * defmultiply
        if local:
            await self.bot.pool.release(conn)
        return dmg, deff

    @has_char()
    @user_cooldown(1800)
    @commands.command(brief=_("Start a new tournament"))
    @locale_doc
    async def tournament(self, ctx, prize: IntFromTo(0, 100_000_000) = 0):
        _(
            """`[prize]` - The amount of money the winner will get

            Start a new tournament. Players have 30 seconds to join via the reaction.
            Tournament entries are free, only the tournament host has to pay the price.

            Only an exponent of 2 (2^n) users can join. If there are more than the nearest exponent, the last joined players will be disregarded.

            The match-ups will be decided at random, the battles themselves will be decided like regular battles (see `{prefix}help battle` for details).

            The winner of a match moves onto the next round, the losers get eliminated, until there is only one player left.
            Tournaments in IdleRPG follow the single-elimination principle.

            (This command has a cooldown of 30 minutes.)"""
        )
        if ctx.character_data["money"] < prize:
            await self.bot.reset_cooldown(ctx)
            return await ctx.send(_("You are too poor."))

        await self.bot.pool.execute(
            'UPDATE profile SET "money"="money"-$1 WHERE "user"=$2;',
            prize,
            ctx.author.id,
        )

        if (
                self.bot.config.game.official_tournament_channel_id
                and ctx.channel.id == self.bot.config.game.official_tournament_channel_id
        ):
            view = JoinView(
                Button(
                    style=ButtonStyle.primary,
                    label="Join the tournament!",
                    emoji="\U00002694",
                ),
                message=_("You joined the tournament."),
                timeout=60 * 10,
            )
            await ctx.send(
                "A mass-tournament has been started. The tournament starts in 10 minutes! The"
                f" prize is **${prize}**!",
                view=view,
            )
            await asyncio.sleep(60 * 10)
            view.stop()
            participants = []
            async with self.bot.pool.acquire() as conn:
                for u in view.joined:
                    if await conn.fetchrow(
                            'SELECT * FROM profile WHERE "user"=$1;', u.id
                    ):
                        participants.append(u)

        else:
            view = JoinView(
                Button(
                    style=ButtonStyle.primary,
                    label="Join the tournament!",
                    emoji="\U00002694",
                ),
                message=_("You joined the tournament."),
                timeout=60 * 5,
            )
            view.joined.add(ctx.author)
            msg = await ctx.send(
                _(
                    "{author} started a tournament! Free entries, prize is"
                    " **${prize}**. Starting in **5 Minutes!**"
                ).format(author=ctx.author.mention, prize=prize),
                view=view,
            )
            await asyncio.sleep(60 * 5)
            view.stop()
            participants = []
            async with self.bot.pool.acquire() as conn:
                for u in view.joined:
                    if await conn.fetchrow(
                            'SELECT * FROM profile WHERE "user"=$1;', u.id
                    ):
                        participants.append(u)

        if len(participants) < 2:
            await self.bot.reset_cooldown(ctx)
            await self.bot.pool.execute(
                'UPDATE profile SET "money"="money"+$1 WHERE "user"=$2;',
                prize,
                ctx.author.id,
            )
            return await ctx.send(
                _("Noone joined your tournament {author}.").format(
                    author=ctx.author.mention
                )
            )

        bye_recipients = []  # To keep track of participants who received a bye

        nearest_power_of_2 = 2 ** math.ceil(math.log2(len(participants)))
        byes_needed = nearest_power_of_2 - len(participants)

        if byes_needed > 0:
            bye_recipients = random.sample(participants, byes_needed)
            for recipient in bye_recipients:
                await ctx.send(
                    _("Participant {participant} received a bye for this round!").format(participant=recipient.mention))
                participants.remove(recipient)
            await ctx.send(
                _("Tournament started with **{num}** entries.").format(num=len(participants) + len(bye_recipients))
            )
        text = _("vs")
        while len(participants) > 1:
            participants = random.shuffle(participants)
            matches = list(chunks(participants, 2))

            for match in matches:
                await ctx.send(f"{match[0].mention} {text} {match[1].mention}")
                await asyncio.sleep(2)
                async with self.bot.pool.acquire() as conn:
                    val1 = sum(
                        await self.bot.get_damage_armor_for(match[0], conn=conn)
                    ) + random.randint(1, 7)
                    val2 = sum(
                        await self.bot.get_damage_armor_for(match[1], conn=conn)
                    ) + random.randint(1, 7)
                if val1 > val2:
                    winner = match[0]
                    looser = match[1]
                elif val2 > val1:
                    winner = match[1]
                    looser = match[0]
                else:
                    winner = random.choice(match)
                    looser = match[1 - match.index(winner)]
                participants.remove(looser)
                await ctx.send(
                    _("Winner of this match is {winner}!").format(winner=winner.mention)
                )
                await asyncio.sleep(2)

            await ctx.send(_("Round Done!"))
            participants.extend(bye_recipients)  # Add back participants who received a bye
            bye_recipients = []  # Reset the list for the next round

        msg = await ctx.send(
            _("Tournament ended! The winner is {winner}.").format(
                winner=participants[0].mention
            )
        )

        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                'UPDATE profile SET "money"="money"+$1 WHERE "user"=$2;',
                prize,
                participants[0].id,
            )
            await self.bot.log_transaction(
                ctx,
                from_=ctx.author.id,
                to=participants[0].id,
                subject="Torunament Winner",
                data={"Gold": prize},
                conn=conn,
            )
        await msg.edit(
            content=_(
                "Tournament ended! The winner is {winner}.\nMoney was given!"
            ).format(winner=participants[0].mention)
        )

    @has_char()
    @user_cooldown(300)
    @commands.command()
    @locale_doc
    async def juggernaut(self, ctx, prize: IntFromTo(0, 100_000_000) = 0, hp: int = 250, juggernaut_hp: int = 7500):
        _(
            """`[prize]` - The amount of money the winner will get.
        `[hp]` - The HP for each player; default is 250.
        `[juggernaut_hp]` - The HP for the Juggernaut; default is 7500.

        Start a Juggernaut game mode where one player becomes the Juggernaut, and others attempt to defeat them.

        Usage:
          `$juggernaut [prize] [hp] [juggernaut_hp]`

        In this game mode:
        - Players have 3 minutes to join via the button.
        - A random player is chosen as the Juggernaut.
        - The Juggernaut fights against all other players.
        - If the Juggernaut defeats all players, the players receive buffs and attempt again.
        - The game continues until the Juggernaut is defeated or all players are eliminated.
        - The prize money is split between the Juggernaut and the player who deals the finishing blow.

        Note:
        - You must have a character to use this command.
        - This command has a cooldown of 5 minutes.
        """
        )

        if ctx.character_data["money"] < prize:
            await self.bot.reset_cooldown(ctx)
            return await ctx.send(_("You are too poor."))

        # if ctx.author.id != 457096185333940237:
        # return await ctx.send("Access Denied: Being reworked")

        await self.bot.pool.execute(
            'UPDATE profile SET "money"="money"-$1 WHERE "user"=$2;',
            prize,
            ctx.author.id,
        )

        if (
                self.bot.config.game.official_tournament_channel_id
                and ctx.channel.id == self.bot.config.game.official_tournament_channel_id
        ):
            view = JoinView(
                Button(
                    style=ButtonStyle.primary,
                    label="Join Juggernaut!",
                    emoji="\U00002694",
                ),
                message=_("You joined the Juggernaut Gamemode."),
                timeout=60 * 3,
            )
            if hp == 250:
                await ctx.send(
                    "A Juggernaut gamemode has been started. The gamemode starts in 3 minutes! The"
                    f" prize pool is **${prize}**!",
                    view=view,
                )
            else:
                await ctx.send(
                    f"A Juggernaut gamemode has been started. Custom HP set to {hp}! The Juggernaut gamemode starts in 3 minutes! The"
                    f" prize is **${prize}**!",
                    view=view,
                )
            await asyncio.sleep(60 * 3)
            view.stop()
            participants = []
            async with self.bot.pool.acquire() as conn:
                for u in view.joined:
                    if await conn.fetchrow(
                            'SELECT * FROM profile WHERE "user"=$1;', u.id
                    ):
                        participants.append(u)

        else:
            view = JoinView(
                Button(
                    style=ButtonStyle.primary,
                    label="Join juggernaut!",
                    emoji="\U00002694",
                ),
                message=_("You joined the juggernaut gamemode."),
                timeout=60 * 3,
            )
            view.joined.add(ctx.author)
            msg = await ctx.send(
                _(
                    "{author} started a juggernaut gamemode! Free entries, prize pool is"
                    " **${prize}**!"
                ).format(author=ctx.author.mention, prize=prize),
                view=view,
            )
            await asyncio.sleep(60 * 3)
            view.stop()
            participants = []
            async with self.bot.pool.acquire() as conn:
                for u in view.joined:
                    if await conn.fetchrow(
                            'SELECT * FROM profile WHERE "user"=$1;', u.id
                    ):
                        participants.append(u)

        if len(participants) < 3:
            await self.bot.reset_cooldown(ctx)
            await self.bot.pool.execute('UPDATE profile SET "money"="money"+$1 WHERE "user"=$2;', prize, ctx.author.id)
            return await ctx.send(
                _("Not enough participants to start the game, {author}.").format(author=ctx.author.mention))
        try:
            await ctx.send(f"There are {len(participants)} participants in the game.")
            # 2. Select a juggernaut
            juggernaut = random.choice(participants)

            # Get and double the juggernaut's stats
            async with self.bot.pool.acquire() as conn:
                juggernaut_dmg, juggernaut_deff = await self.bot.get_raidstats(juggernaut, conn=conn)
            juggernaut_deff *= 3

            await ctx.send(_(f"{juggernaut.mention} has been chosen as the juggernaut with **{juggernaut_hp}** HP!"))
            participants.remove(juggernaut)

            self.dmgbuff = 0
            self.deffbuff = 0

        except Exception as e:
            await ctx.send(f"{e}")

        def buff_stats(stats):
            def round_to_nearest(x, base=0.1):
                return round(x / base) * base

            self.deffbuff += Decimal(round_to_nearest(rnd.uniform(0.1, 0.2)))
            self.dmgbuff += Decimal(round_to_nearest(rnd.uniform(0.1, 0.2)))

        juggernaut_tracker_HP = juggernaut_hp
        TurnCounter = 0
        juggernaut_killer = None
        all_player_stats = {}
        defeated = []
        turnpass = False
        turn = False
        battle_ongoing = True  # Initialize a variable to track the battle state

        while participants and battle_ongoing:
            random.shuffle(participants)
            for player in participants:
                # Get player's stats
                if battle_ongoing:

                    try:
                        async with self.bot.pool.acquire() as conn:
                            dmg, deff = await self.get_raidstatsjug(player, conn=conn)
                            dmg = round(dmg, 2)
                            deff = round(deff, 2)

                        async with self.bot.pool.acquire() as conn:
                            dmgt, defft = await self.bot.get_raidstats(player, conn=conn)
                            dmgt = round(dmgt, 2)
                            defft = round(defft, 2)

                        await ctx.send(f"Normie: ATK {dmgt}, DEF {defft}. Modified: {dmg}, DEF {deff}")

                    except Exception as e:
                        await ctx.send(f"{e}")

                    player_stats = {
                        "user": player,
                        "hp": hp,  # This hp needs to be defined elsewhere
                        "armor": deff,
                        "damage": dmg,
                    }
                    all_player_stats[player.id] = player_stats
                    # Set up the battle participants
                    try:
                        players = [player_stats, {
                            "user": juggernaut,
                            "hp": juggernaut_tracker_HP,
                            "damage": juggernaut_dmg,
                            "armor": juggernaut_deff
                        }]
                    except Exception as e:
                        await ctx.send(f"An error occurred: {e}")

                    battle_log = deque(
                        [
                            (
                                0,
                                _("Raidbattle {p1} vs. {p2} started!").format(
                                    p1=players[0]["user"].mention, p2=players[1]["user"].mention
                                ),
                            )
                        ],
                        maxlen=3,
                    )

                    embed = discord.Embed(
                        description=battle_log[0][1],
                        color=self.bot.config.game.primary_colour,
                    )

                    log_message = await ctx.send(embed=embed)
                    await asyncio.sleep(4)

                    start = datetime.datetime.utcnow()
                    attacker, defender = players
                    while (
                            attacker["hp"] > 0
                            and defender["hp"] > 0
                            and datetime.datetime.utcnow()
                            < start + datetime.timedelta(minutes=5)
                    ):
                        dmg = (
                                attacker["damage"]
                                + Decimal(random.randint(0, 100))
                                - defender["armor"]
                        )
                        dmg = 1 if dmg <= 0 else dmg  # make sure no negative damage happens
                        if defender["user"] != juggernaut and TurnCounter >= 6:
                            await ctx.send("The Juggernaut charges their weapon")
                            dmg = dmg + 1000
                        defender["hp"] -= dmg
                        if defender["hp"] < 0:
                            defender["hp"] = 0
                        battle_log.append(
                            (
                                battle_log[-1][0] + 1,
                                _(
                                    "{attacker} attacks! {defender} takes **{dmg}HP**"
                                    " damage."
                                ).format(
                                    attacker=attacker["user"].mention,
                                    defender=defender["user"].mention,
                                    dmg=dmg,
                                ),
                            )
                        )

                        embed = discord.Embed(
                            description=_(
                                "{p1} - {hp1} HP left\n{p2} - {hp2} HP left"
                            ).format(
                                p1=players[0]["user"].mention,
                                hp1=players[0]["hp"],
                                p2=players[1]["user"].mention,
                                hp2=players[1]["hp"],
                            ),
                            color=self.bot.config.game.primary_colour,
                        )

                        for line in battle_log:
                            embed.add_field(
                                name=_("Action #{number}").format(number=line[0]),
                                value=line[1],
                            )
                        TurnCounter = TurnCounter + 1
                        await log_message.edit(embed=embed)
                        await asyncio.sleep(4)
                        juggernaut_tracker_HP = players[1]["hp"]
                        if juggernaut_tracker_HP <= 0:
                            await ctx.send(_("Juggernaut has been defeated!"))
                            juggernaut_killer = attacker["user"].id
                            await ctx.send(
                                _("{attacker} has dealt the finishing blow to the juggernaut and is the winner!").format(
                                    attacker=attacker["user"].mention))
                            battle_ongoing = False
                            self.deffbuff = 0
                            self.dmgbuff = 0
                            break
                        if players[0]["hp"] <= 0:
                            defeated.append(player)
                            await ctx.send(_(f"Juggernaut has defeated {player.name}!"))
                            TurnCounter = 0
                            juggernaut_tracker_HP = players[1]["hp"]
                            await asyncio.sleep(2)
                        attacker, defender = defender, attacker  # This line swaps attacker and defender

            # If all players are defeated, buff their stats and go for another round
            if battle_ongoing:
                if set(defeated) == set(participants):

                    for player in participants:
                        player_stats = all_player_stats[player.id]
                        random.shuffle(participants)
                        try:
                            buff_stats(player_stats)

                        except Exception as e:
                            await ctx.send(f"An error occurred: {e}")
                            continue  # move to the next player if there was an issue buffing this one

                        # Revive the player by resetting their HP to the original value
                        player_stats["hp"] = hp

                    # Clear the list of defeated players for the next round
                    turn = True
                    await ctx.send(
                        _(f"Juggernaut has defeated all participants! The party raid stats grown to am additional x{round(self.deffbuff, 2)} DEF and x{round(self.dmgbuff, 2)} ATK.")
                    )

                    defeated.clear()

        if battle_ongoing == False:
            totalprize = prize
            prizejug = prize * 0.2
            prize = prize * 0.8
            prize = round(prize)  # Rounds to the nearest whole number
            prizejug = round(prizejug)
            async with self.bot.pool.acquire() as conn:
                await conn.execute(
                    'UPDATE profile SET "money"="money"+$1 WHERE "user"=$2;',
                    prize,
                    juggernaut_killer,
                )
                await conn.execute(
                    'UPDATE profile SET "money"="money"+$1 WHERE "user"=$2;',
                    prizejug,
                    juggernaut.id,
                )
                await self.bot.log_transaction(
                    ctx,
                    from_=ctx.author.id,
                    to=participants[0].id,
                    subject="Juggernaut",
                    data={"Gold": prize},
                    conn=conn,
                )
            if prize > 0:
                await ctx.send(
                    f"Juggernaut received **${prizejug}** and {juggernaut_killer.mention} has received **${prize}** of the total prize of **{totalprize}**")

    import asyncio
    import random as randomm  # Keeping your original import
    from decimal import Decimal
    import discord
    from discord.ext import commands
    from discord.ui import Button, View
    from discord import ButtonStyle

    # Ensure that JoinView is defined elsewhere and remains unchanged
    # from your_views_module import JoinView

    @has_char()
    @user_cooldown(300)  # 5-minute cooldown
    @commands.command()
    @locale_doc
    async def juggernaut2(
            self,
            ctx,
            prize: IntFromTo(0, 100_000_000) = 0,
            hp: int = 250,
            juggernaut_hp: int = 7500,
    ):
        """
        `[prize]` - The amount of money the winner will get

        Start a new Juggernaut game mode. Players have 3 minutes to join via the button.
        Tournament entries are free; only the tournament host has to pay the prize.

        In this mode, all players team up to defeat the Juggernaut. If they fail, they get buffs and try again.
        The game continues until the Juggernaut is defeated or all players give up.

        (This command has a cooldown of 30 minutes.)
        """

        # Check if the user has enough money
        if ctx.character_data["money"] < prize:
            await self.bot.reset_cooldown(ctx)
            return await ctx.send("You don't have enough money to start the game.")

        # Deduct the prize from the host's money
        await self.bot.pool.execute(
            'UPDATE profile SET "money"="money" - $1 WHERE "user" = $2;',
            prize,
            ctx.author.id,
        )

        # Preserve your original JoinView implementation
        view = JoinView(
            Button(
                style=ButtonStyle.primary,
                label="Join Juggernaut!",
                emoji="⚔️",
            ),
            message="You joined the Juggernaut game mode.",
            timeout=300,  # 3 minutes timeout
        )

        view.joined.add(ctx.author)

        # Start the join phase
        initial_message = await ctx.send(
            f"{ctx.author.mention} started a Juggernaut game mode! Free entries, prize pool is **${prize}**! The game starts in 5 minutes!",
            view=view,
        )
        await asyncio.sleep(300)  # Wait for 3 minutes
        view.stop()

        # Gather valid participants from the database
        participants = []
        async with self.bot.pool.acquire() as conn:
            for u in view.joined:
                if await conn.fetchrow('SELECT * FROM profile WHERE "user"=$1;', u.id):
                    participants.append(u)

        # Check if there are enough participants
        if len(participants) < 3:
            await self.bot.reset_cooldown(ctx)
            await self.bot.pool.execute(
                'UPDATE profile SET "money" = "money" + $1 WHERE "user" = $2;',
                prize,
                ctx.author.id,
            )
            return await ctx.send(
                f"Not enough participants to start the game, {ctx.author.mention}."
            )

        await ctx.send(
            f"There are {len(participants)} participants in the game."
        )

        # Select a Juggernaut randomly
        juggernaut = randomm.choice(participants)
        participants.remove(juggernaut)

        # Get Juggernaut's stats without applying triple defense
        async with self.bot.pool.acquire() as conn:
            juggernaut_dmg, juggernaut_def = await self.bot.get_raidstats(juggernaut, conn=conn)

        # Set Juggernaut's HP based on its defense
        if juggernaut_def > 350:
            juggernaut_hp = 10000
        elif 200 <= juggernaut_def <= 350:
            juggernaut_hp = 20000
        elif 100 <= juggernaut_def < 200:
            juggernaut_hp = 50000
        else:
            juggernaut_hp = 75000

        try:
            await ctx.send(
                f"{juggernaut.mention} has been chosen as the Juggernaut with **{juggernaut_hp}** HP!"
            )

            # Initialize player stats
            player_stats = {}
            for player in participants:
                async with self.bot.pool.acquire() as conn:
                    dmg, deff = await self.get_raidstatsjug(player, conn=conn)
                player_stats[player.id] = {
                    "user": player,
                    "hp": Decimal(hp),
                    "damage": Decimal(dmg),
                    "defense": Decimal(deff),
                }

            juggernaut_stats = {
                "user": juggernaut,
                "hp": Decimal(juggernaut_hp),
                "damage": Decimal(juggernaut_dmg),
                "defense": Decimal(juggernaut_def),
                "base_damage": Decimal(juggernaut_dmg)  # To reset after revival
            }

            # Battle variables
            battle_round = 1
            battle_turn = 1
            buff_amount = Decimal("0.3")  # Increased buff amount for better scaling
            MAX_ROUNDS = 10  # Fixed maximum rounds for Juggernaut victory

            # Initialize the embed with battle status
            embed = discord.Embed(
                title=f"Juggernaut Battle - Round {battle_round}",
                description="The battle begins!",
                color=self.bot.config.game.primary_colour,
            )
            embed.add_field(
                name=f"{juggernaut_stats['user'].display_name}'s HP",
                value=f"{juggernaut_stats['hp']:.2f}/{juggernaut_hp}",
                inline=False,
            )
            player_statuses = []
            for stats in player_stats.values():
                status = (
                        f"{stats['user'].mention}: {stats['hp']:.2f}/{hp} HP"
                        + (" 💀" if stats["hp"] <= 0 else "")
                )
                player_statuses.append(status)
            embed.add_field(
                name="Players' Status",
                value="\n".join(player_statuses),
                inline=False,
            )
            battle_message = await ctx.send(embed=embed)

            # Battle loop
            while battle_round <= MAX_ROUNDS:
                battle_log = []
                embed.title = f"Juggernaut Battle - Round {battle_round}"
                embed.description = f"*Battle Round {battle_round} - Turn {battle_turn} begins!*\n"

                # Players' Turns
                for player_id, stats in player_stats.items():
                    if stats["hp"] <= 0:
                        continue  # Skip defeated players

                    # Determine if the attack hits
                    hit_chance = randomm.random()
                    if hit_chance < 0.1:
                        # Missed attack: No damage and skip logging
                        continue
                    elif hit_chance > 0.9:
                        # Critical hit
                        damage = stats["damage"] * Decimal("2") + Decimal("40")  # +40 for both abilities
                        battle_log.append(
                            f"{stats['user'].mention} landed a critical hit! 🔥"
                        )
                    else:
                        # Normal hit
                        damage = stats["damage"] + Decimal("40")  # +40 for both abilities

                    # Calculate damage to Juggernaut
                    dmg = max(Decimal("1"), damage - juggernaut_stats["defense"])
                    juggernaut_stats["hp"] -= dmg
                    juggernaut_stats["hp"] = max(juggernaut_stats["hp"], Decimal("0"))  # Ensure HP doesn't go below 0

                    # Log the damage if dmg > 0
                    if dmg > 0:
                        battle_log.append(
                            f"{stats['user'].mention} deals {dmg:.2f} damage to the Juggernaut. ✅"
                        )

                    # Update the embed after each attack
                    if battle_log:
                        embed.description += "\n".join(battle_log) + "\n"
                        embed.set_field_at(
                            0,
                            name=f"{juggernaut_stats['user'].display_name}'s HP",
                            value=f"{juggernaut_stats['hp']:.2f}/{juggernaut_hp}",
                            inline=False,
                        )
                        await battle_message.edit(embed=embed)
                        await asyncio.sleep(2)  # 2-second delay per attack
                        battle_log.clear()  # Clear the log for the next entry

                    # Check if Juggernaut is defeated
                    if juggernaut_stats["hp"] <= 0:
                        break

                # Check if Juggernaut is defeated
                if juggernaut_stats["hp"] <= 0:
                    battle_log.append("The Juggernaut has been defeated by the players! 🏆")
                    embed.description += "\n".join(battle_log) + "\n"
                    embed.set_field_at(
                        0,
                        name=f"{juggernaut_stats['user'].display_name}'s HP",
                        value=f"{juggernaut_stats['hp']:.2f}/{juggernaut_hp}",
                        inline=False,
                    )
                    await battle_message.edit(embed=embed)
                    # Set winner_ids to all players still alive
                    winner_ids = [pid for pid, stats in player_stats.items() if stats["hp"] > 0]
                    break  # Exit the battle loop

                # Juggernaut's Turn to Attack
                battle_log = []
                # Increment Juggernaut's damage by +40 for the new turn
                juggernaut_stats["current_damage"] = juggernaut_stats.get("current_damage",
                                                                          juggernaut_stats["base_damage"])
                juggernaut_stats["current_damage"] += Decimal("40")

                # Determine if Juggernaut uses Smash ability
                smash_chance = randomm.random()
                if smash_chance < 0.4:
                    # Smash ability: Attack all players with increased damage (+40)
                    battle_log.append("The Juggernaut uses Smash! 💥")
                    for stats in player_stats.values():
                        if stats["hp"] > 0:
                            dmg = max(
                                Decimal("1"),
                                (juggernaut_stats["current_damage"] + Decimal("40")) - stats["defense"]
                            )
                            stats["hp"] -= dmg
                            stats["hp"] = max(stats["hp"], Decimal("0"))  # Ensure HP doesn't go below 0
                            battle_log.append(
                                f"{stats['user'].mention} takes {dmg:.2f} damage from Smash. ⚔️"
                            )
                            if stats["hp"] <= 0:
                                battle_log.append(
                                    f"{stats['user'].mention} has been defeated! 💀"
                                )
                else:
                    # Normal attack: Attack one random alive player with increased damage (+40)
                    alive_players = [stats for stats in player_stats.values() if stats["hp"] > 0]
                    if alive_players:
                        target_stats = randomm.choice(alive_players)
                        dmg = max(
                            Decimal("1"), (juggernaut_stats["current_damage"] + Decimal("40")) - target_stats["defense"]
                        )
                        target_stats["hp"] -= dmg
                        target_stats["hp"] = max(target_stats["hp"], Decimal("0"))  # Ensure HP doesn't go below 0
                        battle_log.append(
                            f"The Juggernaut attacks {target_stats['user'].mention} for {dmg:.2f} damage. ⚔️"
                        )
                        if target_stats["hp"] <= 0:
                            battle_log.append(
                                f"{target_stats['user'].mention} has been defeated! 💀"
                            )

                # Announce the Juggernaut's attack results
                if battle_log:
                    embed.description += "\n".join(battle_log) + "\n"
                    embed.set_field_at(
                        0,
                        name=f"{juggernaut_stats['user'].mention}'s HP",
                        value=f"{juggernaut_stats['hp']:.2f}/{juggernaut_hp}",
                        inline=False,
                    )
                    # Update player statuses
                    player_statuses = []
                    for stats in player_stats.values():
                        status = (
                                f"{stats['user'].mention}: {stats['hp']:.2f}/{hp} HP"
                                + (" 💀" if stats["hp"] <= 0 else "")
                        )
                        player_statuses.append(status)
                    embed.set_field_at(
                        1,
                        name="Players' Status",
                        value="\n".join(player_statuses),
                        inline=False,
                    )
                    await battle_message.edit(embed=embed)
                    await asyncio.sleep(5)  # 5-second delay after Juggernaut's attack

                # Check if all players are defeated
                if all(stats["hp"] <= 0 for stats in player_stats.values()):
                    battle_log = ["All players have been defeated! They receive buffs and try again. 🔄"]
                    # Buff players' stats
                    for stats in player_stats.values():
                        stats["hp"] = Decimal(hp)
                        stats["damage"] *= (Decimal("1") + buff_amount)
                        stats["defense"] *= (Decimal("1") + buff_amount)
                    # Announce the buff
                    battle_log.append("Players have been buffed! Their damage and defense have increased. 📈")
                    # Increment the round
                    battle_round += 1
                    # Reset turns for the new round
                    battle_turn = 1
                    # Reset Juggernaut's damage to base_damage
                    juggernaut_stats["current_damage"] = juggernaut_stats["base_damage"]
                    # Update the embed with buff information
                    embed = discord.Embed(
                        title=f"Juggernaut Battle - Round {battle_round}",
                        description="\n".join(battle_log),
                        color=self.bot.config.game.primary_colour,
                    )
                    embed.add_field(
                        name=f"{juggernaut_stats['user'].mention}'s HP",
                        value=f"{juggernaut_stats['hp']:.2f}/{juggernaut_hp}",
                        inline=False,
                    )
                    player_statuses = []
                    for stats in player_stats.values():
                        status = (
                                f"{stats['user'].mention}: {stats['hp']:.2f}/{hp} HP"
                                + (" 💀" if stats["hp"] <= 0 else "")
                        )
                        player_statuses.append(status)
                    embed.add_field(
                        name="Players' Status",
                        value="\n".join(player_statuses),
                        inline=False,
                    )
                    await battle_message.edit(embed=embed)
                    await asyncio.sleep(3)  # 3-second wait between rounds
                    continue  # Continue to the next round

                # Increment the turn after Juggernaut's attack
                battle_turn += 1

                # Check if maximum rounds have been reached
                if battle_round > MAX_ROUNDS:
                    # Juggernaut wins
                    battle_log = ["Maximum number of rounds reached. The Juggernaut wins! ⚔️"]
                    embed.description += "\n".join(battle_log) + "\n"
                    embed.set_field_at(
                        0,
                        name=f"{juggernaut_stats['user'].mention}'s HP",
                        value=f"{juggernaut_stats['hp']:.2f}/{juggernaut_hp}",
                        inline=False,
                    )
                    # Update player statuses
                    player_statuses = []
                    for stats in player_stats.values():
                        status = (
                                f"{stats['user'].mention}: {stats['hp']:.2f}/{hp} HP"
                                + (" 💀" if stats["hp"] <= 0 else "")
                        )
                        player_statuses.append(status)
                    embed.set_field_at(
                        1,
                        name="Players' Status",
                        value="\n".join(player_statuses),
                        inline=False,
                    )
                    await battle_message.edit(embed=embed)
                    break  # Exit the battle loop

            # Distribute prizes
            juggernaut_prize = round(prize * 0.2)
            winner_prize = round(prize * 0.8)
            winners = [player_stats[pid]["user"] for pid in (winner_ids if 'winner_ids' in locals() else [])]

            async with self.bot.pool.acquire() as conn:
                # Give prize to Juggernaut
                await conn.execute(
                    'UPDATE profile SET "money" = "money" + $1 WHERE "user" = $2;',
                    juggernaut_prize,
                    juggernaut.id,
                )
                # Split prize among winners
                if winners:
                    prize_per_winner = winner_prize // len(winners)
                    for winner in winners:
                        await conn.execute(
                            'UPDATE profile SET "money" = "money" + $1 WHERE "user" = $2;',
                            prize_per_winner,
                            winner.id,
                        )
                        # Log transaction for each winner
                        await self.bot.log_transaction(
                            ctx,
                            from_=ctx.author.id,
                            to=winner.id,
                            subject="Juggernaut Game",
                            data={"Gold": prize_per_winner},
                            conn=conn,
                        )
                else:
                    prize_per_winner = 0  # No winners

            # Announce the results
            if winners:
                winner_mentions = ", ".join(winner.mention for winner in winners)
                await ctx.send(
                    f"Congratulations to {winner_mentions} for defeating the Juggernaut! Each winner receives **${prize_per_winner}**!"
                )
            else:
                await ctx.send(
                    "No winners this round. Better luck next time!"
                )
            await ctx.send(
                f"{juggernaut.mention} receives **${juggernaut_prize}** for participating as the Juggernaut."
            )
        except Exception as e:
            import traceback
            error_message = f"Error occurred: {e}\n{traceback.format_exc()}"
            await ctx.send(error_message)
            print(error_message)

    @has_char()
    @user_cooldown(1800)
    @commands.command()
    @locale_doc
    async def raidtournament(self, ctx, prize: IntFromTo(0, 100_000_000) = 0, hp: int = 250):
        _(
            """`[prize]` - The amount of money the winner will get

            Start a new raid tournament. Players have 30 seconds to join via the reaction.
            Tournament entries are free, only the tournament host has to pay the price.

            Only an exponent of 2 (2^n) users can join. If there are more than the nearest exponent, the last joined players will be disregarded.

            The match-ups will be decided at random, the battles themselves will be decided like raid battles (see `{prefix}help raidbattle` for details).

            The winner of a match moves onto the next round, the losers get eliminated, until there is only one player left.
            Tournaments in IdleRPG follow the single-elimination principle.

            (This command has a cooldown of 30 minutes.)"""
        )
        try:
            author_chance = 0
            enemy_chance = 0
            lifestealauth = 0
            lifestealopp = 0
            authorchance = 0
            enemychance = 0
            cheated = False
            if ctx.character_data["money"] < prize:
                await self.bot.reset_cooldown(ctx)
                return await ctx.send(_("You are too poor."))

            await self.bot.pool.execute(
                'UPDATE profile SET "money"="money"-$1 WHERE "user"=$2;',
                prize,
                ctx.author.id,
            )

            if (
                    self.bot.config.game.official_tournament_channel_id
                    and ctx.channel.id == self.bot.config.game.official_tournament_channel_id
            ):
                view = JoinView(
                    Button(
                        style=ButtonStyle.primary,
                        label="Join the raid tournament!",
                        emoji="\U00002694",
                    ),
                    message=_("You joined the raid tournament."),
                    timeout=300,
                )
                if hp == 250:
                    await ctx.send(
                        "A mass-raidtournament has been started. The tournament starts in 5 minutes! The"
                        f" prize is **${prize}**!",
                        view=view,
                    )
                else:
                    await ctx.send(
                        f"A mass-raidtournament has been started. Custom HP set to {hp}! The tournament starts in 5 minutes! The"
                        f" prize is **${prize}**!",
                        view=view,
                    )
                await asyncio.sleep(60*5)
                view.stop()
                participants = []
                async with self.bot.pool.acquire() as conn:
                    for u in view.joined:
                        if await conn.fetchrow(
                                'SELECT * FROM profile WHERE "user"=$1;', u.id
                        ):
                            participants.append(u)

            else:
                view = JoinView(
                    Button(
                        style=ButtonStyle.primary,
                        label="Join the raid tournament!",
                        emoji="\U00002694",
                    ),
                    message=_("You joined the raid tournament."),
                    timeout=300,
                )
                view.joined.add(ctx.author)
                msg = await ctx.send(
                    _(
                        "{author} started a raid tournament! Free entries, prize is"
                        " **${prize}**!"
                    ).format(author=ctx.author.mention, prize=prize),
                    view=view,
                )
                await asyncio.sleep(60*5)


            # Process the users as before
            view.stop()
            participants = []
            async with self.bot.pool.acquire() as conn:
                for u in view.joined:
                    if await conn.fetchrow(
                            'SELECT * FROM profile WHERE "user"=$1;', u.id
                    ):
                        participants.append(u)
            if len(participants) < 2:
                await self.bot.reset_cooldown(ctx)
                await self.bot.pool.execute(
                    'UPDATE profile SET "money"="money"+$1 WHERE "user"=$2;',
                    prize,
                    ctx.author.id,
                )
                return await ctx.send(
                    _("Noone joined your raid tournament {author}.").format(
                        author=ctx.author.mention
                    )
                )

            bye_recipients = []  # To keep track of participants who received a bye

            nearest_power_of_2 = 2 ** math.ceil(math.log2(len(participants)))
            byes_needed = nearest_power_of_2 - len(participants)

            if byes_needed > 0:
                bye_recipients = random.sample(participants, byes_needed)
                for recipient in bye_recipients:
                    await ctx.send(
                        _("Participant {participant} received a bye for this round!").format(
                            participant=recipient.mention))
                    participants.remove(recipient)

            await ctx.send(
                _("Tournament started with **{num}** entries.").format(num=len(participants) + len(bye_recipients)))

            text = _("vs")
            while len(participants) > 1:
                participants = random.shuffle(participants)
                matches = list(chunks(participants, 2))

                for match in matches:
                    await ctx.send(f"{match[0].mention} {text} {match[1].mention}")

                    players = []
                    async with self.bot.pool.acquire() as conn:
                        for player in match:
                            author_chance = 0  # Initialize the variable inside the loop
                            lifestealauth = 0  # Initialize the variable inside the loop
                            specified_words_values = {
                                "Deathshroud": 20,
                                "Soul Warden": 30,
                                "Reaper": 40,
                                "Phantom Scythe": 50,
                                "Soul Snatcher": 60,
                                "Deathbringer": 70,
                                "Grim Reaper": 80,
                            }

                            life_steal_values = {
                                "Little Helper": 7,
                                "Gift Gatherer": 14,
                                "Holiday Aide": 21,
                                "Joyful Jester": 28,
                                "Yuletide Guardian": 35,
                                "Festive Enforcer": 40,
                                "Festive Champion": 60,
                            }
                            # User ID you want to check
                            user_id = ctx.author.id

                            try:


                                # Define common queries
                                query_class = 'SELECT "class" FROM profile WHERE "user" = $1;'
                                query_xp = 'SELECT "xp" FROM profile WHERE "user" = $1;'

                                # Query data for ctx.author.id
                                result_author = await self.bot.pool.fetch(query_class, player.id)
                                auth_xp = await self.bot.pool.fetch(query_xp, player.id)

                                # Convert XP to level for ctx.author.id
                                auth_level = rpgtools.xptolevel(auth_xp[0]['xp'])

                                # Query data for enemy_.id
                                result_opp = await self.bot.pool.fetch(query_class, player.id)
                                opp_xp = await self.bot.pool.fetch(query_xp, player.id)

                                # Convert XP to level for enemy_.id
                                opp_level = rpgtools.xptolevel(opp_xp[0]['xp'])

                                # Initialize chance

                                # await ctx.send(f"{author_chance}")
                                if result_author:
                                    author_classes = result_author[0]["class"]  # Assume it's a list of classes
                                    for class_name in author_classes:
                                        if class_name in specified_words_values:
                                            author_chance += specified_words_values[class_name]
                                        if class_name in life_steal_values:
                                            lifestealauth += life_steal_values[class_name]

                                if result_opp:
                                    opp_classes = result_opp[0]["class"]  # Assume it's a list of classes
                                    for class_name in opp_classes:
                                        if class_name in life_steal_values:
                                            lifestealopp += life_steal_values[class_name]
                                        if class_name in specified_words_values:
                                            enemy_chance += specified_words_values[class_name]
                                            # await ctx.send(f"{author_chance}")
                            except Exception as e:
                                await ctx.send(f"{e}")

                            if author_chance != 0:
                                authorchance = author_chance

                            user_id = player.id

                            luck_booster = await self.bot.get_booster(player, "luck")

                            query = 'SELECT "luck", "health", "stathp" FROM profile WHERE "user" = $1;'
                            result = await conn.fetchrow(query, user_id)

                            if result:
                                # Extract the health value from the result
                                base_health = 250
                                health = result['health'] + base_health
                                stathp = result['stathp'] * 50

                                # Calculate total health based on level and add to current health
                                level = rpgtools.xptolevel(
                                    auth_xp[0]['xp']) if player == ctx.author else rpgtools.xptolevel(opp_xp[0]['xp'])
                                total_health = health + (level * 5)
                                total_health = total_health + stathp

                            dmg, deff = await self.bot.get_raidstats(player, conn=conn)
                            u = {
                                "user": player,
                                "hp": total_health,
                                "armor": deff,
                                "damage": dmg,
                                "deathchance": author_chance,
                                "lifesteal": lifestealauth,
                            }
                            players.append(u)

                        #await ctx.send(f"DEBUG {players[0]} {players[1]}")

                    battle_log = deque(
                        [
                            (
                                0,
                                _("Raidbattle {p1} vs. {p2} started!").format(
                                    p1=players[0]["user"], p2=players[1]["user"]
                                ),
                            )
                        ],
                        maxlen=3,
                    )

                    embed = discord.Embed(
                        description=battle_log[0][1],
                        color=self.bot.config.game.primary_colour,
                    )

                    log_message = await ctx.send(embed=embed)
                    await asyncio.sleep(4)

                    start = datetime.datetime.utcnow()
                    attacker, defender = random.shuffle(players)

                    while (
                            players[0]["hp"] > 0
                            and players[1]["hp"] > 0
                            and datetime.datetime.utcnow() < start + datetime.timedelta(minutes=5)
                    ):
                        # this is where the fun begins
                        dmg = (
                                attacker["damage"] + Decimal(random.randint(0, 100)) - defender["armor"]
                        )
                        dmg = 1 if dmg <= 0 else dmg  # make sure no negative damage happens
                        defender["hp"] -= dmg
                        if defender["hp"] < 0:
                            defender["hp"] = 0

                        if defender["hp"] <= 0:
                            # Calculate the chance of cheating death for the defender (enemy)

                            chance = defender["deathchance"]

                            # Generate a random number between 1 and 100
                            random_number = random.randint(1, 100)

                            if not cheated:
                                # The player cheats death and survives with 50 HP
                                # await ctx.send(
                                # f"{authorchance}, {enemychance}, rand {random_number} (ignore this) ")  # -- Debug Line
                                if random_number <= chance:
                                    defender["hp"] = 75
                                    battle_log.append(
                                        (
                                            battle_log[-1][0] + 1,
                                            _("{defender} cheats death and survives with 75HP!").format(
                                                defender=defender["user"].mention,
                                            ),
                                        )
                                    )
                                    cheated = True
                                else:
                                    battle_log.append(
                                        (
                                            battle_log[-1][0] + 1,
                                            _("{attacker} deals **{dmg}HP** damage. {defender} is defeated!").format(
                                                attacker=attacker["user"].mention,
                                                defender=defender["user"].mention,
                                                dmg=dmg,
                                            ),
                                        )
                                    )
                            else:
                                # The player is defeated
                                battle_log.append(
                                    (
                                        battle_log[-1][0] + 1,
                                        _("{attacker} deals **{dmg}HP** damage. {defender} is defeated!").format(
                                            attacker=attacker["user"].mention,
                                            defender=defender["user"].mention,
                                            dmg=dmg,
                                        ),
                                    )
                                )
                        else:

                            if attacker["lifesteal"] > 0:
                                lifesteal_percentage = Decimal(lifestealauth) / Decimal(100)
                                heal = lifesteal_percentage * Decimal(dmg)
                                attacker["hp"] += heal.quantize(Decimal('0.00'), rounding=ROUND_HALF_UP)

                            if attacker["lifesteal"] > 0:

                                battle_log.append(
                                    (
                                        battle_log[-1][0] + 1,
                                        _("{attacker} attacks! {defender} takes **{dmg}HP** damage. Lifesteals: **{heal}**").format(
                                            attacker=attacker["user"].mention,
                                            defender=defender["user"].mention,
                                            dmg=dmg,
                                            heal=heal,
                                        ),
                                    )
                                )
                            else:
                                battle_log.append(
                                    (
                                        battle_log[-1][0] + 1,
                                        _("{attacker} attacks! {defender} takes **{dmg}HP** damage.").format(
                                            attacker=attacker["user"].mention,
                                            defender=defender["user"].mention,
                                            dmg=dmg,
                                        ),
                                    )
                                )

                        embed = discord.Embed(
                            description=_(
                                "{p1} - {hp1} HP left\n{p2} - {hp2} HP left").format(
                                p1=players[0]["user"],
                                hp1=players[0]["hp"],
                                p2=players[1]["user"],
                                hp2=players[1]["hp"],
                            ),
                            color=self.bot.config.game.primary_colour,
                        )

                        for line in battle_log:
                            embed.add_field(
                                name=_("Action #{number}").format(number=line[0]), value=line[1]
                            )

                        await log_message.edit(embed=embed)
                        await asyncio.sleep(4)
                        attacker, defender = defender, attacker  # switch places
                    if players[0]["hp"] == 0:
                        winner = match[1]
                        looser = match[0]
                    else:
                        winner = match[0]
                        looser = match[1]
                    participants.remove(looser)
                    await ctx.send(
                        _("Winner of this match is {winner}!").format(winner=winner.mention)
                    )
                    await asyncio.sleep(2)

                    await ctx.send(_("Round Done!"))
                    lifestealauth = 0
                    lifestealopp = 0
                    authorchance = 0
                    enemychance = 0
                    cheated = False
                    participants.extend(bye_recipients)  # Add back participants who received a bye
                    bye_recipients = []  # Reset the list for the next round
        except Exception as e:
            await ctx.send(e)

        msg = await ctx.send(
            _("Raid Tournament ended! The winner is {winner}.").format(
                winner=participants[0].mention
            )
        )

        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                'UPDATE profile SET "money"="money"+$1 WHERE "user"=$2;',
                prize,
                participants[0].id,
            )
            await self.bot.log_transaction(
                ctx,
                from_=ctx.author.id,
                to=participants[0].id,
                subject="Tournament Prize",
                data={"Gold": prize},
                conn=conn,
            )
        await msg.edit(
            content=_(
                "Raid Tournament ended! The winner is {winner}.\nMoney was given!"
            ).format(winner=participants[0].mention)
        )


async def setup(bot):
    await bot.add_cog(Tournament(bot))
