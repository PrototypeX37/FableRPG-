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
import decimal

import utils.misc as rpgtools
from collections import deque
from collections import deque
from decimal import Decimal
from decimal import Decimal, ROUND_HALF_UP

import discord
import random as randomm
from discord.enums import ButtonStyle
from discord.ext import commands
from discord.ui.button import Button

from classes.classes import Ranger, Reaper
from classes.classes import from_string as class_from_string
from classes.converters import IntGreaterThan
from cogs.shard_communication import user_on_cooldown as user_cooldown
from utils import random
from utils.checks import has_char, has_money, is_gm
from utils.i18n import _, locale_doc
from utils.joins import SingleJoinView


class Battles(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.emoji_to_element = {
            "<:f_corruption:1170192253256466492>": "Corrupted",
            "<:f_water:1170191321571545150>": "Water",
            "<:f_electric:1170191219926777936>": "Electric",
            "<:f_light:1170191258795376771>": "Light",
            "<:f_dark:1170191180164771920>": "Dark",
            "<:f_nature:1170191149802213526>": "Wind",
            "<:f_earth:1170191288361033806>": "Nature",
            "<:f_fire:1170192046632468564>": "Fire"
        }
        self.fighting_players = {}

        self.levels = {
            1: {
                "minion1_name": "Imp",
                "minion2_name": "Shadow Spirit",
                "boss_name": "Abyssal Guardian",
                "minion1": {"hp": 65, "armor": 15, "damage": 35},
                "minion2": {"hp": 75, "armor": 20, "damage": 55},
                "boss": {"hp": 150, "armor": 30, "damage": 65}
            },
            2: {
                "minion1_name": "Wraith",
                "minion2_name": "Soul Eater",
                "boss_name": "Vile Serpent",
                "minion1": {"hp": 80, "armor": 35, "damage": 50},
                "minion2": {"hp": 90, "armor": 55, "damage": 70},
                "boss": {"hp": 250, "armor": 30, "damage": 80}
            },
            3: {
                "minion1_name": "Goblin",
                "minion2_name": "Orc",
                "boss_name": "Warlord Grakthar",
                "minion1": {"hp": 100, "armor": 5, "damage": 70},
                "minion2": {"hp": 120, "armor": 80, "damage": 50},
                "boss": {"hp": 270, "armor": 95, "damage": 95}
            },
            4: {
                "minion1_name": "Skeleton",
                "minion2_name": "Zombie",
                "boss_name": "Necromancer Voss",
                "minion1": {"hp": 130, "armor": 20, "damage": 70},
                "minion2": {"hp": 150, "armor": 30, "damage": 70},
                "boss": {"hp": 190, "armor": 110, "damage": 115}
            },
            5: {
                "minion1_name": "Bandit",
                "minion2_name": "Highwayman",
                "boss_name": "Blackblade Marauder",
                "minion1": {"hp": 130, "armor": 30, "damage": 75},
                "minion2": {"hp": 150, "armor": 30, "damage": 80},
                "boss": {"hp": 250, "armor": 117, "damage": 119}
            },
            6: {
                "minion1_name": "Spiderling",
                "minion2_name": "Venomous Arachnid",
                "boss_name": "Arachnok Queen",
                "minion1": {"hp": 150, "armor": 36, "damage": 79},
                "minion2": {"hp": 170, "armor": 37, "damage": 85},
                "boss": {"hp": 275, "armor": 122, "damage": 127}
            },
            7: {
                "minion1_name": "Wisp",
                "minion2_name": "Specter",
                "boss_name": "Lich Lord Moros",
                "minion1": {"hp": 155, "armor": 38, "damage": 83},
                "minion2": {"hp": 175, "armor": 43, "damage": 89},
                "boss": {"hp": 280, "armor": 127, "damage": 132}
            },
            8: {
                "minion1_name": "Frost Imp",
                "minion2_name": "Ice Elemental",
                "boss_name": "Frostfire Behemoth",
                "minion1": {"hp": 155, "armor": 42, "damage": 87},
                "minion2": {"hp": 175, "armor": 47, "damage": 93},
                "boss": {"hp": 285, "armor": 132, "damage": 137}
            },
            9: {
                "minion1_name": "Lizardman",
                "minion2_name": "Dragonkin",
                "boss_name": "Dragonlord Zaldrak",
                "minion1": {"hp": 160, "armor": 45, "damage": 90},
                "minion2": {"hp": 180, "armor": 52, "damage": 95},
                "boss": {"hp": 295, "armor": 138, "damage": 140}
            },
            10: {
                "minion1_name": "Haunted Spirit",
                "minion2_name": "Phantom Wraith",
                "boss_name": "Soulreaver Lurkthar",
                "minion1": {"hp": 160, "armor": 48, "damage": 93},
                "minion2": {"hp": 185, "armor": 55, "damage": 97},
                "boss": {"hp": 315, "armor": 150, "damage": 150}
            },
            11: {
                "minion1_name": "Gnoll Raider",
                "minion2_name": "Hyena Pack",
                "boss_name": "Ravengaze Alpha",
                "minion1": {"hp": 170, "armor": 52, "damage": 97},
                "minion2": {"hp": 185, "armor": 101, "damage": 70},
                "boss": {"hp": 330, "armor": 153, "damage": 155}
            },
            12: {
                "minion1_name": "Gloomhound",
                "minion2_name": "Nocturne Stalker",
                "boss_name": "Nightshade Serpentis",
                "minion1": {"hp": 170, "armor": 82, "damage": 139},
                "minion2": {"hp": 190, "armor": 87, "damage": 144},
                "boss": {"hp": 335, "armor": 157, "damage": 160}
            },
            13: {
                "minion1_name": "Magma Elemental",
                "minion2_name": "Inferno Imp",
                "boss_name": "Ignis Inferno",
                "minion1": {"hp": 175, "armor": 85, "damage": 141},
                "minion2": {"hp": 190, "armor": 90, "damage": 148},
                "boss": {"hp": 335, "armor": 160, "damage": 163}
            },
            14: {
                "minion1_name": "Cursed Banshee",
                "minion2_name": "Spectral Harbinger",
                "boss_name": "Wraithlord Maroth",
                "minion1": {"hp": 180, "armor": 89, "damage": 145},
                "minion2": {"hp": 225, "armor": 93, "damage": 152},
                "boss": {"hp": 340, "armor": 163, "damage": 166}
            },
            15: {
                "minion1_name": "Demonic Imp",
                "minion2_name": "Hellspawn Reaver",
                "boss_name": "Infernus, the Infernal",
                "minion1": {"hp": 182, "armor": 145, "damage": 89},
                "minion2": {"hp": 250, "armor": 152, "damage": 93},
                "boss": {"hp": 350, "armor": 170, "damage": 170}
            },
            16: {
                "minion1_name": "Tainted Ghoul",
                "minion2_name": "Necrotic Abomination",
                "boss_name": "Master Shapeshifter",
                "minion1": {"hp": 400, "armor": 122, "damage": 199},
                "minion2": {"hp": 400, "armor": 127, "damage": 204},
                "boss": {"hp": 360, "armor": 180, "damage": 180}
            },
            17: {
                "minion1_name": "Chaos Fiend",
                "minion2_name": "Voidborn Horror",
                "boss_name": "Eldritch Devourer",
                "minion1": {"hp": 186, "armor": 149, "damage": 92},
                "minion2": {"hp": 250, "armor": 156, "damage": 95},
                "boss": {"hp": 355, "armor": 175, "damage": 175}
            },
            18: {
                "minion1_name": "Blood Warden",
                "minion2_name": "Juzam Djinn",
                "boss_name": "Dreadlord Vortigon",
                "minion1": {"hp": 190, "armor": 153, "damage": 95},
                "minion2": {"hp": 250, "armor": 159, "damage": 99},
                "boss": {"hp": 360, "armor": 180, "damage": 175}
            },
            19: {
                "minion1_name": "Specter",
                "minion2_name": "Phantom Wraith",
                "boss_name": "Spectral Overlord",
                "minion1": {"hp": 200, "armor": 153, "damage": 95},
                "minion2": {"hp": 250, "armor": 159, "damage": 99},
                "boss": {"hp": 250, "armor": 0, "damage": 350}
            },
            20: {
                "minion1_name": "Ice Elemental",
                "minion2_name": "Frozen Horror",
                "boss_name": "Frostbite, the Ice Tyrant",
                "minion1": {"hp": 205, "armor": 155, "damage": 99},
                "minion2": {"hp": 250, "armor": 161, "damage": 102},
                "boss": {"hp": 365, "armor": 210, "damage": 140}
            },
            21: {
                "minion1_name": "Dragonkin",
                "minion2_name": "Chromatic Wyrm",
                "boss_name": "Chromaggus the Flamebrand",
                "minion1": {"hp": 210, "armor": 160, "damage": 99},
                "minion2": {"hp": 250, "armor": 161, "damage": 102},
                "boss": {"hp": 365, "armor": 210, "damage": 140}
            },
            22: {
                "minion1_name": "Phantom Banshee",
                "minion2_name": "Wailing Apparition",
                "boss_name": "Banshee Queen Shriekara",
                "minion1": {"hp": 205, "armor": 155, "damage": 99},
                "minion2": {"hp": 250, "armor": 161, "damage": 102},
                "boss": {"hp": 365, "armor": 210, "damage": 140}
            },
            23: {
                "minion1_name": "Abyssal Imp",
                "minion2_name": "Voidbringer Fiend",
                "boss_name": "Voidlord Malgros",
                "minion1": {"hp": 205, "armor": 155, "damage": 99},
                "minion2": {"hp": 250, "armor": 161, "damage": 102},
                "boss": {"hp": 370, "armor": 200, "damage": 130}
            },
            24: {
                "minion1_name": "Dreadshade Specter",
                "minion2_name": "Soulreaver Harbinger",
                "boss_name": "Soulshredder Vorath",
                "minion1": {"hp": 250, "armor": 140, "damage": 99},
                "minion2": {"hp": 250, "armor": 140, "damage": 115},
                "boss": {"hp": 360, "armor": 225, "damage": 125}
            },
            25: {
                "minion1_name": "Inferno Aberration",
                "minion2_name": "Brimstone Fiend",
                "boss_name": "Pyroclasmic Overfiend",
                "minion1": {"hp": 250, "armor": 140, "damage": 99},
                "minion2": {"hp": 250, "armor": 140, "damage": 115},
                "boss": {"hp": 360, "armor": 190, "damage": 150}
            },
            26: {
                "minion1_name": "Crimson Serpent",
                "minion2_name": "Sanguine Horror",
                "boss_name": "Sangromancer Malroth",
                "minion1": {"hp": 250, "armor": 140, "damage": 99},
                "minion2": {"hp": 250, "armor": 140, "damage": 115},
                "boss": {"hp": 360, "armor": 250, "damage": 100}
            },
            27: {
                "minion1_name": "Doombringer Abomination",
                "minion2_name": "Chaosspawn Horror",
                "boss_name": "Chaosforged Leviathan",
                "minion1": {"hp": 250, "armor": 140, "damage": 99},
                "minion2": {"hp": 250, "armor": 140, "damage": 115},
                "boss": {"hp": 360, "armor": 110, "damage": 250}
            },
            28: {
                "minion1_name": "Nethersworn Aberration",
                "minion2_name": "Eldritch Behemoth",
                "boss_name": "Abyssal Enderark",
                "minion1": {"hp": 250, "armor": 140, "damage": 99},
                "minion2": {"hp": 250, "armor": 140, "damage": 115},
                "boss": {"hp": 400, "armor": 180, "damage": 100}
            },
            29: {
                "minion1_name": "Darktide Kraken",
                "minion2_name": "Abyssal Voidlord",
                "boss_name": "Tidal Terror Abaddon",
                "minion1": {"hp": 250, "armor": 140, "damage": 99},
                "minion2": {"hp": 250, "armor": 140, "damage": 115},
                "boss": {"hp": 390, "armor": 230, "damage": 150}
            },
            30: {
                "minion1_name": "Elder Voidfiend",
                "minion2_name": "Abyssal Voidreaver",
                "boss_name": "Eldritch Archdemon",
                "minion1": {"hp": 250, "armor": 110, "damage": 110},
                "minion2": {"hp": 250, "armor": 140, "damage": 140},
                "boss": {"hp": 600, "armor": 200, "damage": 190}
            }
        }

    @has_char()
    @user_cooldown(90)
    @commands.command(brief=_("Battle against another player"))
    @locale_doc
    async def battle(
            self, ctx, money: IntGreaterThan(-1) = 0, enemy: discord.Member = None
    ):
        _(
            """`[money]` - A whole number that can be 0 or greater; defaults to 0
            `[enemy]` - A user who has a profile; defaults to anyone

            Fight against another player while betting money.
            To decide the fight, the players' items, race and class bonuses and an additional number from 1 to 7 are evaluated, this serves as a way to give players with lower stats a chance at winning.

            The money is removed from both players at the start of the battle. Once a winner has been decided, they will receive their money, plus the enemy's money.
            The battle lasts 30 seconds, after which the winner and loser will be mentioned.

            If both players' stats + random number are the same, the winner is decided at random.
            The battle's winner will receive a PvP win, which shows on their profile.
            (This command has a cooldown of 90 seconds.)"""
        )
        if enemy == ctx.author:
            return await ctx.send(_("You can't battle yourself."))
        if ctx.character_data["money"] < money:
            return await ctx.send(_("You are too poor."))

        await self.bot.pool.execute(
            'UPDATE profile SET "money"="money"-$1 WHERE "user"=$2;',
            money,
            ctx.author.id,
        )

        if not enemy:
            text = _("{author} seeks a battle! The price is **${money}**.").format(
                author=ctx.author.mention, money=money
            )
        else:
            text = _(
                "{author} seeks a battle with {enemy}! The price is **${money}**."
            ).format(author=ctx.author.mention, enemy=enemy.mention, money=money)

        async def check(user: discord.User) -> bool:
            return await has_money(self.bot, user.id, money)

        future = asyncio.Future()
        view = SingleJoinView(
            future,
            Button(
                style=ButtonStyle.primary,
                label=_("Join the battle!"),
                emoji="\U00002694",
            ),
            allowed=enemy,
            prohibited=ctx.author,
            timeout=60,
            check=check,
            check_fail_message=_("You don't have enough money to join the battle."),
        )

        await ctx.send(text, view=view)

        try:
            enemy_ = await future
        except asyncio.TimeoutError:
            await self.bot.reset_cooldown(ctx)
            await self.bot.pool.execute(
                'UPDATE profile SET "money"="money"+$1 WHERE "user"=$2;',
                money,
                ctx.author.id,
            )
            return await ctx.send(
                _("Noone wanted to join your battle, {author}!").format(
                    author=ctx.author.mention
                )
            )

        await self.bot.pool.execute(
            'UPDATE profile SET "money"="money"-$1 WHERE "user"=$2;', money, enemy_.id
        )

        await ctx.send(
            _(
                "Battle **{author}** vs **{enemy}** started! 30 seconds of fighting"
                " will now start!"
            ).format(author=ctx.disp, enemy=enemy_.display_name)
        )

        stats = [
            sum(await self.bot.get_damage_armor_for(ctx.author)) + random.randint(1, 7),
            sum(await self.bot.get_damage_armor_for(enemy_)) + random.randint(1, 7),
        ]
        players = [ctx.author, enemy_]
        if stats[0] == stats[1]:
            winner = random.choice(players)
        else:
            winner = players[stats.index(max(stats))]
        looser = players[players.index(winner) - 1]

        await asyncio.sleep(30)

        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                'UPDATE profile SET "pvpwins"="pvpwins"+1, "money"="money"+$1 WHERE'
                ' "user"=$2;',
                money * 2,
                winner.id,
            )
            await self.bot.log_transaction(
                ctx,
                from_=looser.id,
                to=winner.id,
                subject="Battle Bet",
                data={"Gold": money},
                conn=conn,
            )
        await ctx.send(
            _("{winner} won the battle vs {looser}! Congratulations!").format(
                winner=winner.mention, looser=looser.mention
            )
        )

    @commands.group(aliases=["bt"])
    async def battletower(self, ctx):
        print("hello world")
        if ctx.invoked_subcommand is None:
            await ctx.invoke(self.progress)

    @is_gm()
    @commands.command(hidden=True)
    async def setbtlevel(self, ctx, user_id: int, level: int):
        # Check if the user invoking the command is allowed
        if ctx.author.id != 295173706496475136:
            await ctx.send("You are not authorized to use this command.")
            return

        try:
            async with self.bot.pool.acquire() as conn:
                await conn.execute(
                    'UPDATE battletower SET "level"=$1 WHERE "id"=$2;',
                    level,
                    user_id,
                )
            await ctx.send(f"Successfully updated level for user {user_id} to {level}.")
        except Exception as e:
            await ctx.send(f"An error occurred while updating the level: {e}")

    async def find_opponent(self, ctx):
        count = 0
        score = 0
        author_hp = 250  # Setting the author's initial HP
        while author_hp > 0:
            players = []

            async with self.bot.pool.acquire() as connection:
                while True:
                    query = 'SELECT "user" FROM profile WHERE "user" != $1 ORDER BY RANDOM() LIMIT 1'
                    random_opponent_id = await connection.fetchval(query, ctx.author.id)

                    if not random_opponent_id or random_opponent_id != 730276802316206202:
                        break  # Exit the loop if a suitable opponent ID is found

                if not random_opponent_id:
                    return None  # Couldn't find a suitable opponent at the moment

                enemy = await self.bot.fetch_user(random_opponent_id)

            if not enemy:
                return None  # Failed to fetch opponent information. Please try again later.

            async with self.bot.pool.acquire() as conn:
                for player in (ctx.author, enemy):
                    dmg, deff = await self.bot.get_raidstats(player, conn=conn)
                    if player == ctx.author:
                        hp_value = author_hp
                    else:
                        hp_value = 250  # Set the default hp for the enemy

                    u = {"user": player, "hp": hp_value, "armor": deff, "damage": dmg}
                    players.append(u)

            battle_log = deque(
                [
                    (
                        0,
                        _("Raidbattle {p1} vs. {p2} started!").format(
                            p1=players[0]["user"].display_name, p2=players[1]["user"].display_name
                        ),
                    )
                ],
                maxlen=3,
            )

            embed = discord.Embed(
                description=battle_log[0][1],
                color=self.bot.config.game.primary_colour
            )

            if count == 0:
                log_message = await ctx.send(embed=embed)  # To avoid spam, we'll edit this message later
            else:
                await log_message.edit(embed=embed)

            await asyncio.sleep(4)

            start = datetime.datetime.utcnow()
            attacker, defender = random.sample(players, k=2)

            # Battle logic
            while players[0]["hp"] > 0 and players[1][
                "hp"] > 0 and datetime.datetime.utcnow() < start + datetime.timedelta(minutes=5):
                dmg = attacker["damage"] + Decimal(random.randint(0, 100)) - defender["armor"]
                dmg = max(1, dmg)  # Ensure no negative damage

                defender["hp"] -= dmg
                if defender["hp"] < 0:
                    defender["hp"] = 0

                battle_log.append(
                    (
                        battle_log[-1][0] + 1,
                        _("{attacker} attacks! {defender} takes **{dmg}HP** damage.").format(
                            attacker=attacker["user"].display_name,
                            defender=defender["user"].display_name,
                            dmg=dmg,
                        ),
                    )
                )

                embed = discord.Embed(
                    description=_("{p1} - {hp1} HP left\n{p2} - {hp2} HP left").format(
                        p1=players[0]["user"].display_name,
                        hp1=players[0]["hp"],
                        p2=players[1]["user"].display_name,
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
                attacker, defender = defender, attacker  # Switch places

            players = sorted(players, key=lambda x: x["hp"])
            winner = players[1]["user"]
            loser = players[0]["user"]

            if winner.id != ctx.author.id:
                await ctx.send(
                    _("{winner} won the raidbattle vs {loser}!").format(
                        winner=winner.display_name, loser=loser.display_name
                    )
                )
            count = 1

            if winner == ctx.author:
                author_hp = players[1]["hp"]
                score = score + 1
                # If the winner is the ctx.author, continue battling
                await asyncio.sleep(3)  # A delay before finding the next opponent
            else:
                await ctx.send(f"{ctx.author.mention}, were defeated. Your final score was {score}")

                try:

                    highscore = await self.bot.pool.fetchval('SELECT whored FROM profile WHERE "user" = $1',
                                                             ctx.author.id)

                    # Updating the highscore
                    if score > highscore:
                        async with self.bot.pool.acquire() as conn:
                            await conn.execute(
                                'UPDATE profile SET "whored"=$1 WHERE "user"=$2;',
                                score,
                                ctx.author.id,
                            )
                    break
                except Exception as e:
                    await ctx.send(f"An error occurred: {e}")

    async def find_opponentcust(self, ctx):
        count = 0
        score = 0
        player = ctx.author
        user_id = player.id
        luck_booster = await self.bot.get_booster(player, "luck")
        query_class = 'SELECT "class" FROM profile WHERE "user" = $1;'
        query_xp = 'SELECT "xp" FROM profile WHERE "user" = $1;'
        result_author = await self.bot.pool.fetch(query_class, ctx.author.id)
        auth_xp = await self.bot.pool.fetch(query_xp, ctx.author.id)
        base_health = 250
        query = 'SELECT "luck", "health", "stathp" FROM profile WHERE "user" = $1;'

        # Fetch initial stats for the author outside the loop
        async with self.bot.pool.acquire() as conn:
            result = await conn.fetchrow(query, user_id)
            health = result['health'] + base_health
            stathp = result['stathp'] * 50
            dmg_author, deff_author = await self.bot.get_raidstats(player, conn=conn)
            level = rpgtools.xptolevel(auth_xp[0]['xp'])
            total_health_author = health + (level * 5) + stathp
            author_current_hp = total_health_author  # Initialize author's current HP

        while author_current_hp > 0:
            players = []

            # Find a random opponent
            async with self.bot.pool.acquire() as connection:
                while True:
                    query = (
                        'SELECT "user" FROM profile WHERE "user" != $1 '
                        'ORDER BY RANDOM() LIMIT 1'
                    )
                    random_opponent_id = await connection.fetchval(query, ctx.author.id)
                    if random_opponent_id and random_opponent_id != 730276802316206202:
                        break

                if not random_opponent_id:
                    return None  # Couldn't find a suitable opponent

                enemy = await self.bot.fetch_user(random_opponent_id)

            if not enemy:
                return None  # Failed to fetch opponent information

            async with self.bot.pool.acquire() as conn:
                for current_player in (ctx.author, enemy):
                    try:
                        # Define class-related values
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

                        mage_evolution_levels = {
                            "Witcher": 1,
                            "Enchanter": 2,
                            "Mage": 3,
                            "Warlock": 4,
                            "Dark Caster": 5,
                            "White Sorcerer": 6,
                        }

                        user_id = current_player.id
                        query_class = 'SELECT "class" FROM profile WHERE "user" = $1;'
                        query_xp = 'SELECT "xp" FROM profile WHERE "user" = $1;'

                        # Fetch class and XP data
                        result_player = await self.bot.pool.fetch(query_class, user_id)
                        xp_player = await self.bot.pool.fetch(query_xp, user_id)

                        level_player = rpgtools.xptolevel(xp_player[0]['xp'])

                        chance = 0
                        lifesteal = 0
                        mage_evolution = None

                        if result_player:
                            player_classes = result_player[0]["class"]
                            if not isinstance(player_classes, list):
                                player_classes = [player_classes]

                            def get_mage_evolution(classes):
                                max_evolution = None
                                for class_name in classes:
                                    if class_name in mage_evolution_levels:
                                        level = mage_evolution_levels[class_name]
                                        if max_evolution is None or level > max_evolution:
                                            max_evolution = level
                                return max_evolution

                            mage_evolution = get_mage_evolution(player_classes)

                            for class_name in player_classes:
                                if class_name in specified_words_values:
                                    chance += specified_words_values[class_name]
                                if class_name in life_steal_values:
                                    lifesteal += life_steal_values[class_name]
                        else:
                            await ctx.send(f"User with ID {user_id} not found in the profile table.")
                            continue

                        luck_booster = await self.bot.get_booster(current_player, "luck")
                        query = 'SELECT "luck", "health", "stathp" FROM profile WHERE "user" = $1;'
                        result = await conn.fetchrow(query, user_id)

                        if result:
                            luck_value = float(result['luck'])
                            if luck_value <= 0.3:
                                Luck = 20
                            else:
                                Luck = ((luck_value - 0.3) / (1.5 - 0.3)) * 80 + 20
                            Luck = float(round(Luck, 2))

                            if luck_booster:
                                Luck += Luck * 0.25
                                Luck = float(min(Luck, 100))

                            base_health = 250
                            health1 = result['health'] + base_health
                            stathp2 = result['stathp'] * 50

                            level = rpgtools.xptolevel(xp_player[0]['xp'])
                            total_health2 = health1 + (level * 5)
                            total_health3 = total_health2 + stathp2

                            dmg_current, deff_current = await self.bot.get_raidstats(current_player, conn=conn)

                            if current_player == ctx.author:
                                # Use author's current HP
                                u = {
                                    "user": current_player,
                                    "hp": author_current_hp,  # Persist HP across battles
                                    "armor": deff_author,
                                    "damage": dmg_author,
                                    "luck": Luck,
                                    "mage_evolution": mage_evolution
                                }
                            else:
                                # For enemy, use full health
                                u = {
                                    "user": current_player,
                                    "hp": total_health3,
                                    "armor": deff_current,
                                    "damage": dmg_current,
                                    "luck": Luck,
                                    "mage_evolution": mage_evolution
                                }

                            players.append(u)
                        else:
                            await ctx.send(f"User with ID {user_id} not found in the profile table.")
                    except Exception as e:
                        await ctx.send(f"An error occurred: {e}")


            battle_log = deque(
                [
                    (
                        0,
                        _("Raidbattle {p1} vs. {p2} started!").format(
                            p1=players[0]["user"].display_name,
                            p2=players[1]["user"].display_name
                        ),
                    )
                ],
                maxlen=3,
            )

            embed = discord.Embed(
                description=battle_log[0][1],
                color=self.bot.config.game.primary_colour
            )

            if count == 0:
                log_message = await ctx.send(embed=embed)
            else:
                await log_message.edit(embed=embed)

            await asyncio.sleep(4)

            start = datetime.datetime.utcnow()
            attacker, defender = random.sample(players, k=2)

            # Battle logic
            while players[0]["hp"] > 0 and players[1][
                "hp"] > 0 and datetime.datetime.utcnow() < start + datetime.timedelta(minutes=5):
                dmg = attacker["damage"] + Decimal(random.randint(0, 100)) - defender["armor"]
                dmg = max(1, dmg)  # Ensure no negative damage

                defender["hp"] -= dmg
                if defender["hp"] < 0:
                    defender["hp"] = 0

                battle_log.append(
                    (
                        battle_log[-1][0] + 1,
                        _("{attacker} attacks! {defender} takes **{dmg}HP** damage.").format(
                            attacker=attacker["user"].display_name,
                            defender=defender["user"].display_name,
                            dmg=dmg,
                        ),
                    )
                )

                embed = discord.Embed(
                    description=_("{p1} - {hp1} HP left\n{p2} - {hp2} HP left").format(
                        p1=players[0]["user"].display_name,
                        hp1=players[0]["hp"],
                        p2=players[1]["user"].display_name,
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
                attacker, defender = defender, attacker  # Switch roles

            # Determine winner and loser
            players = sorted(players, key=lambda x: x["hp"])
            winner = players[1]["user"]
            loser = players[0]["user"]

            if winner.id != ctx.author.id:
                await ctx.send(
                    _("{winner} won the raidbattle vs {loser}!").format(
                        winner=winner.display_name, loser=loser.display_name
                    )
                )
            count = 1

            if winner == ctx.author:
                author_current_hp = players[1]["hp"]  # Update author's HP for next battle
                score += 1
                await asyncio.sleep(3)  # Delay before next opponent
            else:
                await ctx.send(f"{ctx.author.mention}, you were defeated. Your final score was {score}")

                try:
                    highscore = await self.bot.pool.fetchval(
                        'SELECT whored2 FROM profile WHERE "user" = $1', ctx.author.id
                    )

                    # Update the highscore if the current score is higher
                    if score > highscore:
                        async with self.bot.pool.acquire() as conn:
                            await conn.execute(
                                'UPDATE profile SET "whored2"=$1 WHERE "user"=$2;',
                                score,
                                ctx.author.id,
                            )
                    break
                except Exception as e:
                    await ctx.send(f"An error occurred: {e}")

    # Usage within a command
    @commands.command(aliases=["hd"], brief=_("Battle against players till you drop! (includes raidstats)"))
    @user_cooldown(300)
    @locale_doc
    async def horde(self, ctx, modetype = "normal"):
        _(
            """
        Initiates the 'Horde' mode where a player engages in battles against other players randomly until they are defeated.
        The player's health points (HP) are retained after each battle, making it an endurance challenge.
        """
        )
        try:
            if modetype == "normal":
                opponent = await self.find_opponent(ctx)

            elif modetype == "custom":
                opponent = await self.find_opponentcust(ctx)
        except Exception as e:
            await ctx.send(e)

    @battletower.command()
    async def start(self, ctx):

        # Check if the user exists in the database
        try:
            async with self.bot.pool.acquire() as connection:
                user_exists = await connection.fetchval('SELECT 1 FROM battletower WHERE id = $1', ctx.author.id)

            if not user_exists:
                # User doesn't exist in the database
                prologue_embed = discord.Embed(
                    title="Welcome to the Battle Tower",
                    description=(
                        "You stand at the foot of the imposing Battle Tower, a colossal structure that pierces the heavens. "
                        "It is said that the tower was once a place of valor, but it has since fallen into darkness. "
                        "Now, it is a domain of malevolence, home to powerful bosses and their loyal minions."
                    ),
                    color=0xFF5733  # Custom color
                )

                prologue_embed.set_image(url="https://i.ibb.co/s1xx83h/download-3-1.jpg")

                await ctx.send(embed=prologue_embed)

                confirm = await ctx.confirm(
                    message="Do you want to enter the Battle Tower and face its challenges?", timeout=60)

                if confirm is not None:
                    if confirm:
                        # User confirmed to enter the tower
                        async with self.bot.pool.acquire() as connection:
                            await connection.execute('INSERT INTO battletower (id) VALUES ($1)', ctx.author.id)

                        await ctx.send("You have entered the Battle Tower. Good luck on your quest!")
                        return
                    else:
                        await ctx.send("You chose not to enter the Battle Tower. Perhaps another time.")
                        return
                else:
                    # User didn't make a choice within the specified time
                    await ctx.send("You didn't respond in time. Please try again when you're ready.")
                    return

        except Exception as e:
            await ctx.send(f"You didn't respond in time.")

    @has_char()
    @battletower.command()
    async def progress(self, ctx):
        try:
            async with self.bot.pool.acquire() as connection:
                user_exists = await connection.fetchval('SELECT 1 FROM battletower WHERE id = $1', ctx.author.id)

                if not user_exists:
                    await ctx.send("You have not started Battletower. You can start by using `$battletower start`")
                    return

                try:
                    user_level = await connection.fetchval('SELECT level FROM battletower WHERE id = $1', ctx.author.id)

                    # Create a list of levels with challenging names
                    level_names_1 = [
                        "The Tower's Foyer",
                        "Shadowy Staircase",
                        "Chamber of Whispers",
                        "Serpent's Lair",
                        "Halls of Despair",
                        "Crimson Abyss",
                        "Forgotten Abyss",
                        "Dreadlord's Domain",
                        "Gates of Twilight",
                        "Twisted Reflections",
                        "Voidforged Sanctum",
                        "Nexus of Chaos",
                        "Eternal Torment Halls",
                        "Abyssal Desolation",
                        "Cursed Citadel",
                        "The Spire of Shadows",
                        "Tempest's Descent",
                        "Roost of Doombringers",
                        "The Endless Spiral",
                        "Malevolent Apex",
                        "Apocalypse's Abyss",
                        "Chaosborne Throne",
                        "Supreme Darkness",
                        "The Tower's Heart",
                        "The Ultimate Test",
                        "Realm of Annihilation",
                        "Lord of Despair",
                        "Abyssal Overlord",
                        "The End of All",
                        "The Final Confrontation"
                    ]

                    level_names_2 = [
                        "Illusion's Prelude",
                        "Ephemeral Mirage",
                        "Whispers of Redemption",
                        "Veil of Hope",
                        "Specter's Glimmer",
                        "Echoes of Salvation",
                        "Shattered Illusions",
                        "Cacophony of Betrayal",
                        "Doomed Resurgence",
                        "Fading Luminescence",
                        "Despair's Embrace",
                        "Ill-Fated Reverie",
                        "Spectral Deception",
                        "Bittersweet Resonance",
                        "Lament of Broken Dreams",
                        "Puppeteer's Triumph",
                        "Shattered Redemption",
                        "Eternal Betrayal",
                        "Crimson Remorse",
                        "Last breath"
                    ]

                    # Function to generate the formatted level list
                    def generate_level_list(levels, start_level=1):
                        result = "```\n"
                        for level, level_name in enumerate(levels, start=start_level):
                            checkbox = "❌" if level == user_level else "✅" if level < user_level else "❌"
                            result += f"Level {level:<2} {checkbox} {level_name}\n"
                        result += "```"
                        return result

                    # Create embed for levels 1-30
                    prestige_level = await connection.fetchval('SELECT prestige FROM battletower WHERE id = $1',
                                                               ctx.author.id)

                    embed_1 = discord.Embed(
                        title="Battle Tower Progress (Levels 1-30)",
                        description=f"Level: {user_level}\nPrestige Level: {prestige_level}",
                        color=0x0000FF  # Blue color
                    )
                    embed_1.add_field(name="Level Progress", value=generate_level_list(level_names_1), inline=False)
                    embed_1.set_footer(text="**Rewards are granted every 5 levels**")

                    # Send the embeds to the current context (channel)
                    await ctx.send(embed=embed_1)


                except Exception as e:
                    # Handle the exception related to fetching user_level, print or log the error for debugging
                    print(f"Error fetching user_level: {e}")
                    await ctx.send(f"An error occurred while fetching your level {e}.")

        except Exception as e:
            # Handle any exceptions related to database connection, print or log the error for debugging
            print(f"Error accessing the database: {e}")
            await ctx.send("An error occurred while accessing the database.")

    def create_dialogue_page(self, page, level, ctx, name_value, entry_fee_dialogue, dialogue, face_image_url):
        if level == 0:
            # Define settings for level 0 dialogue
            titles = ["Guard", name_value, "Guard", name_value, "Guard"]
            # Check for the first, third, and fifth pages to show the specific avatar
            thumbnails = [
                face_image_url if p in [0, 2, 4]
                else str(ctx.author.avatar.url) if (p in [1, 3] and hasattr(ctx.author, 'avatar'))
                else None
                for p in range(len(entry_fee_dialogue))
            ]
        elif level == 1:
            # Define settings for level 1 dialogue
            titles = ["Abyssal Guardian", name_value, "Abyssal Guardian", "Imp", name_value]
            thumbnails = [face_image_url if p in [0, 2, 4] else "https://i.ibb.co/vYBdn7j/download-7.jpg" for p in
                          range(len(dialogue))]

        dialogue_embed = discord.Embed(
            title=titles[page],
            color=0x003366,
            description=entry_fee_dialogue[page] if level == 0 else dialogue[page]
        )

        if thumbnails[page]:
            dialogue_embed.set_thumbnail(url=thumbnails[page])

        return dialogue_embed

    async def is_player_in_fight(self, player_id):
        # Check if the player is in a fight based on the dictionary
        return player_id in self.fighting_players

    async def add_player_to_fight(self, player_id):
        # Add the player to the fight dictionary with a lock
        self.fighting_players[player_id] = asyncio.Lock()
        await self.fighting_players[player_id].acquire()

    async def remove_player_from_fight(self, player_id):
        # Release the lock and remove the player from the fight dictionary
        if player_id in self.fighting_players:
            self.fighting_players[player_id].release()
            del self.fighting_players[player_id]

    @has_char()
    @user_cooldown(600)
    @battletower.command(brief=_("Battle against the floors protectors for amazing rewards (includes raidstats)"))
    @locale_doc
    async def fight(self, ctx):

        authorchance = 0
        enemychance = 0
        cheated = False
        level = rpgtools.xptolevel(ctx.character_data["xp"])

        emotes = {
            "common": "<:F_common:1139514874016309260>",
            "uncommon": "<:F_uncommon:1139514875828252702>",
            "rare": "<:F_rare:1139514880517484666>",
            "magic": "<:F_Magic:1139514865174720532>",
            "legendary": "<:F_Legendary:1139514868400132116>",
            "mystery": "<:F_mystspark:1139521536320094358>",
            "fortune": "<:f_money:1146593710516224090>",
            "divine": "<:f_divine:1169412814612471869>"
        }

        # Check if a lock exists for the player
        if await self.is_player_in_fight(ctx.author.id):
            await ctx.send("You are already in a battle.")
            return

        try:

            async with self.bot.pool.acquire() as connection:
                user_exists = await connection.fetchval('SELECT 1 FROM battletower WHERE id = $1', ctx.author.id)

                if not user_exists:
                    await ctx.send("You have not started Battletower. You can start by using `$battletower start`")
                    await self.bot.reset_cooldown(ctx)
                    return

            async with self.bot.pool.acquire() as connection:
                level = await connection.fetchval('SELECT level FROM battletower WHERE id = $1', ctx.author.id)
                player_balance = await connection.fetchval('SELECT money FROM profile WHERE "user" = $1',
                                                           ctx.author.id)
                god_value = await connection.fetchval('SELECT god FROM profile WHERE "user" = $1',
                                                      ctx.author.id)
                name_value = await connection.fetchval('SELECT name FROM profile WHERE "user" = $1',
                                                       ctx.author.id)

            try:
                level_data = self.levels[level]
            except Exception as e:
                pass

            if level >= 31:
                egg = True

                if egg:
                    confirm_message = "Are you sure you want to prestige? This action will reset your level. Your next run rewards will be completely randomized."
                    try:
                        confirm = await ctx.confirm(confirm_message)
                        if confirm:
                            async with self.bot.pool.acquire() as connection:
                                await connection.execute(
                                    'UPDATE battletower SET level = 1, prestige = prestige + 1 WHERE id = $1',
                                    ctx.author.id)
                                await ctx.send(
                                    "You have prestiged. Your level has been reset to 1. The rewards for your next run will be completely randomized.")
                                await self.bot.reset_cooldown(ctx)
                                return
                        else:
                            await ctx.send("Prestige canceled.")
                            return await self.bot.reset_cooldown(ctx)
                    except asyncio.TimeoutError:
                        await ctx.send("Prestige canceled due to timeout.")
                        return await self.bot.reset_cooldown(ctx)
                else:
                    await self.bot.reset_cooldown(ctx)
                    await ctx.send("More coming soon.")
                    return

            if level == 2:
                level_data = self.levels[level]
                face_image_url = "https://i.ibb.co/G3V00YL/download-2.png"
                level_2_dialogue = [
                    f"Vile Serpent: (Emerges from the shadows) You dare trespass upon the Shadowy Staircase, {name_value}? We, the Wraith and the Soul Eater, will be your tormentors.",
                    f"{name_value}: (With unwavering determination) I've come to conquer this tower. What sadistic challenges do you have for me now?",
                    "Wraith: (With a chilling whisper) Sadistic is an understatement. We're here to break your spirit, to watch you crumble.",
                    f"Soul Eater: (With malevolence in its voice) Your bravery will be your undoing, {name_value}. We'll feast on your despair."
                ]

                # Create an embed for the Abyssal Guardian's dialogue
                embed = discord.Embed(title="Vile Serpent", color=0x003366)
                embed.set_thumbnail(url=face_image_url)

                # Function to create dialogue pages with specified titles, avatars, and thumbnails
                def create_dialogue_page(page):
                    dialogue_embed = discord.Embed(
                        title="Vile Serpent" if page == 0 else name_value if page == 1 else "Wraith" if page == 2 else "Soul Eater",
                        color=0x003366,
                        description=level_2_dialogue[page]
                    )

                    # Set the Imp's thumbnail for the 4th dialogue
                    if page == 0:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/LJcM38s/download-2.png")
                    elif page == 1:
                        if ctx.author.avatar is not None:
                            dialogue_embed.set_thumbnail(url=ctx.author.avatar.url)
                        else:
                            dialogue_embed.set_thumbnail(url=ctx.author.default_avatar.url)

                    elif page == 2:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/NC2kHpz/download-3.png")
                    elif page == 3:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/BchZsDh/download-7.png")

                    return dialogue_embed

                pages = [create_dialogue_page(page) for page in range(len(level_2_dialogue))]

                current_page = 0
                dialogue_message = await ctx.send(embed=pages[current_page])

                # Define reactions for pagination
                reactions = ["⬅️", "➡️"]

                for reaction in reactions:
                    await dialogue_message.add_reaction(reaction)

                def check(reaction, user):
                    return user == ctx.author and str(reaction.emoji) in reactions

                while current_page < len(pages) - 1:  # Check the length of the 'pages' list
                    try:
                        reaction, user = await self.bot.wait_for("reaction_add", check=check, timeout=60)

                        if str(reaction.emoji) == "⬅️":
                            current_page = max(0, current_page - 1)
                        elif str(reaction.emoji) == "➡️":
                            current_page = min(len(pages) - 1, current_page + 1)

                        await dialogue_message.edit(embed=pages[current_page])
                        if ctx.guild and ctx.guild.me.guild_permissions.manage_messages:
                            await reaction.remove(user)

                    except asyncio.TimeoutError:
                        break
                # Start the battle after all dialogue
                await ctx.send("The battle begins!")  # Include the current dialogue page as the embed

            if level == 3:
                level_data = self.levels[level]
                face_image_url = "https://i.ibb.co/wLYrp17/download-8.png"
                level_3_dialogue = [
                    f"Warlord Grakthar: (Roaring with fury) {name_value}, you've entered the Chamber of Whispers, but it is I, Warlord Grakthar, who commands this chamber. You will bow before me!",
                    f"{name_value}: (Unyielding) I've come to conquer this tower. What twisted game are you playing, Warlord?",
                    f"Goblin: (With a wicked cackle) Our game is one of torment and despair. You are our plaything, {name_value}.",
                    f"Orc: (With a thunderous roar) Your strength won't save you from our might."
                ]

                # Create an embed for the Abyssal Guardian's dialogue
                embed = discord.Embed(title="Warlord Grakthar", color=0x003366)
                embed.set_thumbnail(url=face_image_url)

                # Function to create dialogue pages with specified titles, avatars, and thumbnails
                def create_dialogue_page(page):
                    dialogue_embed = discord.Embed(
                        title="Warlord Grakthar" if page == 0 else name_value if page == 1 else "Goblin" if page == 2 else "Orc",
                        color=0x003366,
                        description=level_3_dialogue[page]
                    )

                    # Set the Imp's thumbnail for the 4th dialogue
                    if page == 0:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/wLYrp17/download-8.png")
                    elif page == 1:
                        if ctx.author.avatar is not None:
                            dialogue_embed.set_thumbnail(url=ctx.author.avatar.url)
                        else:
                            dialogue_embed.set_thumbnail(url=ctx.author.default_avatar.url)

                    elif page == 2:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/nfMcsry/download-10.png")
                    elif page == 3:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/30HY5Jx/download-9.png")

                    return dialogue_embed

                pages = [create_dialogue_page(page) for page in range(len(level_3_dialogue))]

                current_page = 0
                dialogue_message = await ctx.send(embed=pages[current_page])

                # Define reactions for pagination
                reactions = ["⬅️", "➡️"]

                for reaction in reactions:
                    await dialogue_message.add_reaction(reaction)

                def check(reaction, user):
                    return user == ctx.author and str(reaction.emoji) in reactions

                while current_page < len(pages) - 1:  # Check the length of the 'pages' list
                    try:
                        reaction, user = await self.bot.wait_for("reaction_add", check=check, timeout=60)

                        if str(reaction.emoji) == "⬅️":
                            current_page = max(0, current_page - 1)
                        elif str(reaction.emoji) == "➡️":
                            current_page = min(len(pages) - 1, current_page + 1)

                        await dialogue_message.edit(embed=pages[current_page])
                        if ctx.guild and ctx.guild.me.guild_permissions.manage_messages:
                            await reaction.remove(user)

                    except asyncio.TimeoutError:
                        break
                # Start the battle after all dialogue
                await ctx.send("The battle begins!")  # Include the current dialogue page as the embed

            if level == 4:
                level_data = self.levels[level]
                face_image_url = "https://i.ibb.co/wLYrp17/download-8.png"
                level_4_dialogue = [
                    f"Necromancer Voss: (Raising his staff, emitting an eerie aura) Welcome to the Serpent's Lair, {name_value}. I am the Necromancer Voss, and this is my domain. Prepare for your doom.",
                    f"{name_value}: (With unwavering resolve) I've come to conquer the tower. What relentless nightmare do you have in store, Voss?",
                    f"Skeleton: (With a malevolent laugh) Nightmares are our specialty. You won't escape our grasp, {name_value}.",
                    f"Zombie: (With an eerie moan) We will feast upon your despair."
                ]

                # Create an embed for the Abyssal Guardian's dialogue
                embed = discord.Embed(title="Necromancer Voss", color=0x003366)
                embed.set_thumbnail(url=face_image_url)

                # Function to create dialogue pages with specified titles, avatars, and thumbnails
                def create_dialogue_page(page):
                    dialogue_embed = discord.Embed(
                        title="Necromancer Voss" if page == 0 else name_value if page == 1 else "Skeleton" if page == 2 else "Zombie",
                        color=0x003366,
                        description=level_4_dialogue[page]
                    )

                    # Set the Imp's thumbnail for the 4th dialogue
                    if page == 0:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/G5DrFfv/download-13.jpg")
                    elif page == 1:
                        if ctx.author.avatar is not None:
                            dialogue_embed.set_thumbnail(url=ctx.author.avatar.url)
                        else:
                            dialogue_embed.set_thumbnail(url=ctx.author.default_avatar.url)

                    elif page == 2:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/zS26jYD/download-12.jpg")
                    elif page == 3:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/5L6V446/download-11.jpg")

                    return dialogue_embed

                pages = [create_dialogue_page(page) for page in range(len(level_4_dialogue))]

                current_page = 0
                dialogue_message = await ctx.send(embed=pages[current_page])

                # Define reactions for pagination
                reactions = ["⬅️", "➡️"]

                for reaction in reactions:
                    await dialogue_message.add_reaction(reaction)

                def check(reaction, user):
                    return user == ctx.author and str(reaction.emoji) in reactions

                while current_page < len(pages) - 1:  # Check the length of the 'pages' list
                    try:
                        reaction, user = await self.bot.wait_for("reaction_add", check=check, timeout=60)

                        if str(reaction.emoji) == "⬅️":
                            current_page = max(0, current_page - 1)
                        elif str(reaction.emoji) == "➡️":
                            current_page = min(len(pages) - 1, current_page + 1)

                        await dialogue_message.edit(embed=pages[current_page])
                        if ctx.guild and ctx.guild.me.guild_permissions.manage_messages:
                            await reaction.remove(user)

                    except asyncio.TimeoutError:
                        break
                # Start the battle after all dialogue
                await ctx.send("The battle begins!")  # Include the current dialogue page as the embed

            if level == 5:
                level_data = self.levels[level]
                face_image_url = "https://i.ibb.co/wLYrp17/download-8.png"
                level_5_dialogue = [
                    f"Blackblade Marauder: (Drawing a wicked blade) You've reached the Halls of Despair, {name_value}, but it is I, the Blackblade Marauder, who governs this realm. Prepare for annihilation.",
                    f"{name_value}: (Unyielding) I've come this far, and I won't be deterred. What torment do you have for me, Marauder?",
                    f"Bandit: (With a sinister laugh) Torment is our art. You'll crumble under our assault, {name_value}.",
                    f"Highwayman: (With malevolence in his eyes) We'll break you, one way or another."
                ]

                # Create an embed for the Abyssal Guardian's dialogue
                embed = discord.Embed(title="Blackblade Marauder", color=0x003366)
                embed.set_thumbnail(url=face_image_url)

                # Function to create dialogue pages with specified titles, avatars, and thumbnails
                def create_dialogue_page(page):
                    dialogue_embed = discord.Embed(
                        title="Blackblade Marauder" if page == 0 else name_value if page == 1 else "Bandit" if page == 2 else "Highwayman",
                        color=0x003366,
                        description=level_5_dialogue[page]
                    )

                    # Set the Imp's thumbnail for the 4th dialogue
                    if page == 0:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/0BdGZBn/download-14.jpg")
                    elif page == 1:
                        if ctx.author.avatar is not None:
                            dialogue_embed.set_thumbnail(url=ctx.author.avatar.url)
                        else:
                            dialogue_embed.set_thumbnail(url=ctx.author.default_avatar.url)

                    elif page == 2:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/gzsJR55/download-15.jpg")
                    elif page == 3:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/zX0rXsP/download-18.jpg")

                    return dialogue_embed

                pages = [create_dialogue_page(page) for page in range(len(level_5_dialogue))]

                current_page = 0
                dialogue_message = await ctx.send(embed=pages[current_page])

                # Define reactions for pagination
                reactions = ["⬅️", "➡️"]

                for reaction in reactions:
                    await dialogue_message.add_reaction(reaction)

                def check(reaction, user):
                    return user == ctx.author and str(reaction.emoji) in reactions

                while current_page < len(pages) - 1:  # Check the length of the 'pages' list
                    try:
                        reaction, user = await self.bot.wait_for("reaction_add", check=check, timeout=60)

                        if str(reaction.emoji) == "⬅️":
                            current_page = max(0, current_page - 1)
                        elif str(reaction.emoji) == "➡️":
                            current_page = min(len(pages) - 1, current_page + 1)

                        await dialogue_message.edit(embed=pages[current_page])
                        if ctx.guild and ctx.guild.me.guild_permissions.manage_messages:
                            await reaction.remove(user)

                    except asyncio.TimeoutError:
                        break
                # Start the battle after all dialogue
                await ctx.send("The battle begins!")  # Include the current dialogue page as the embed

            if level == 6:
                level_data = self.levels[level]
                face_image_url = "https://i.ibb.co/0rGtfC9/3d-illustration-dark-purple-spider-260nw-2191752107.png"
                level_6_dialogue = [
                    f"Arachnok Queen: (Emerges from a web of silk) {name_value}, you have ventured into the Crimson Abyss. I am the Arachnok Queen, and this is my web. Tremble before my fangs.",
                    f"{name_value}: (With unwavering determination) Enough of your games, Arachnok Queen. My journey continues, and I'll crush your illusions beneath my heel.",
                    f"Spiderling: (With skittering legs) Illusions that shroud your path in darkness.",
                    f"Venomous Arachnid: (With a poisonous hiss) We'll savor the moment your courage wanes."
                ]

                # Create an embed for the Abyssal Guardian's dialogue
                embed = discord.Embed(title="Arachnok Queen", color=0x003366)
                embed.set_thumbnail(url=face_image_url)

                # Function to create dialogue pages with specified titles, avatars, and thumbnails
                def create_dialogue_page(page):
                    dialogue_embed = discord.Embed(
                        title="Arachnok Queen" if page == 0 else name_value if page == 1 else "Spiderling" if page == 2 else "Venomous Arachnid",
                        color=0x003366,
                        description=level_6_dialogue[page]
                    )

                    # Set the Imp's thumbnail for the 4th dialogue
                    if page == 0:
                        dialogue_embed.set_thumbnail(
                            url="https://i.ibb.co/0rGtfC9/3d-illustration-dark-purple-spider-260nw-2191752107.png")
                    elif page == 1:
                        if ctx.author.avatar is not None:
                            dialogue_embed.set_thumbnail(url=ctx.author.avatar.url)
                        else:
                            dialogue_embed.set_thumbnail(url=ctx.author.default_avatar.url)

                    elif page == 2:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/RDXvXcD/download-19.jpg")
                    elif page == 3:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/XZPcqCY/download-20.jpg")

                    return dialogue_embed

                pages = [create_dialogue_page(page) for page in range(len(level_6_dialogue))]

                current_page = 0
                dialogue_message = await ctx.send(embed=pages[current_page])

                # Define reactions for pagination
                reactions = ["⬅️", "➡️"]

                for reaction in reactions:
                    await dialogue_message.add_reaction(reaction)

                def check(reaction, user):
                    return user == ctx.author and str(reaction.emoji) in reactions

                while current_page < len(pages) - 1:  # Check the length of the 'pages' list
                    try:
                        reaction, user = await self.bot.wait_for("reaction_add", check=check, timeout=60)

                        if str(reaction.emoji) == "⬅️":
                            current_page = max(0, current_page - 1)
                        elif str(reaction.emoji) == "➡️":
                            current_page = min(len(pages) - 1, current_page + 1)

                        await dialogue_message.edit(embed=pages[current_page])
                        if ctx.guild and ctx.guild.me.guild_permissions.manage_messages:
                            await reaction.remove(user)

                    except asyncio.TimeoutError:
                        break
                # Start the battle after all dialogue
                await ctx.send("The battle begins!")  # Include the current dialogue page as the embed

            if level == 7:
                level_data = self.levels[level]
                face_image_url = "https://i.ibb.co/4jF6z29/download-22.jpg"
                level_7_dialogue = [
                    f"Lich Lord Moros: (Rising from the ethereal mist) {name_value}, you stand upon the Forgotten Abyss. I am the Lich Lord Moros, and this realm is my spectral dominion. Your fate is sealed.",
                    f"{name_value}: (With resolute determination) Your illusions won't deter me, Lich Lord Moros. I'll shatter your spectral veil and press on.",
                    f"Wisp: (With a haunting glow) Veil of the forgotten and the lost.",
                    f"Specter: (With an otherworldly presence) You'll become a forgotten memory."
                ]

                # Create an embed for the Abyssal Guardian's dialogue
                embed = discord.Embed(title="Arachnok Queen", color=0x003366)
                embed.set_thumbnail(url=face_image_url)

                # Function to create dialogue pages with specified titles, avatars, and thumbnails
                def create_dialogue_page(page):
                    dialogue_embed = discord.Embed(
                        title="Lich Lord Moros" if page == 0 else name_value if page == 1 else "Wisp" if page == 2 else "Specter",
                        color=0x003366,
                        description=level_7_dialogue[page]
                    )

                    # Set the Imp's thumbnail for the 4th dialogue
                    if page == 0:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/4jF6z29/download-22.jpg")
                    elif page == 1:
                        if ctx.author.avatar is not None:
                            dialogue_embed.set_thumbnail(url=ctx.author.avatar.url)
                        else:
                            dialogue_embed.set_thumbnail(url=ctx.author.default_avatar.url)

                    elif page == 2:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/J3PJzPR/download-26.jpg")
                    elif page == 3:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/XZPcqCY/download-20.jpg")

                    return dialogue_embed

                pages = [create_dialogue_page(page) for page in range(len(level_7_dialogue))]

                current_page = 0
                dialogue_message = await ctx.send(embed=pages[current_page])

                # Define reactions for pagination
                reactions = ["⬅️", "➡️"]

                for reaction in reactions:
                    await dialogue_message.add_reaction(reaction)

                def check(reaction, user):
                    return user == ctx.author and str(reaction.emoji) in reactions

                while current_page < len(pages) - 1:  # Check the length of the 'pages' list
                    try:
                        reaction, user = await self.bot.wait_for("reaction_add", check=check, timeout=60)

                        if str(reaction.emoji) == "⬅️":
                            current_page = max(0, current_page - 1)
                        elif str(reaction.emoji) == "➡️":
                            current_page = min(len(pages) - 1, current_page + 1)

                        await dialogue_message.edit(embed=pages[current_page])
                        if ctx.guild and ctx.guild.me.guild_permissions.manage_messages:
                            await reaction.remove(user)

                    except asyncio.TimeoutError:
                        break
                # Start the battle after all dialogue
                await ctx.send("The battle begins!")  # Include the current dialogue page as the embed

            if level == 8:
                level_data = self.levels[level]
                face_image_url = "https://i.ibb.co/YWSkgYx/download-27.jpg"
                level_8_dialogue = [
                    f"Frostfire Behemoth: (Rising from the molten core) {name_value}, you have entered the Dreadlord's Domain, my domain. I am the Frostfire Behemoth, and I shall incinerate your hopes.",
                    f"{name_value}: (With fierce determination) You will find no mercy in the heart of the dreadlord, Frostfire Behemoth. Your flames won't consume me.",
                    "Frost Imp: (With icy flames) Flames that burn with unrelenting fury.",
                    "Ice Elemental: (With a frigid gaze) We'll snuff out your defiance."
                ]

                # Create an embed for the Abyssal Guardian's dialogue
                embed = discord.Embed(title="Frostfire Behemoth", color=0x003366)
                embed.set_thumbnail(url=face_image_url)

                # Function to create dialogue pages with specified titles, avatars, and thumbnails
                def create_dialogue_page(page):
                    dialogue_embed = discord.Embed(
                        title="Frostfire Behemoth" if page == 0 else name_value if page == 1 else "Frost Imp" if page == 2 else "Ice Elemental",
                        color=0x003366,
                        description=level_8_dialogue[page]
                    )

                    # Set the Imp's thumbnail for the 4th dialogue
                    if page == 0:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/YWSkgYx/download-27.jpg")
                    elif page == 1:
                        if ctx.author.avatar is not None:
                            dialogue_embed.set_thumbnail(url=ctx.author.avatar.url)
                        else:
                            dialogue_embed.set_thumbnail(url=ctx.author.default_avatar.url)

                    elif page == 2:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/5M6zTB4/download-28.jpg")
                    elif page == 3:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/ssLVKWv/download-29.jpg")

                    return dialogue_embed

                pages = [create_dialogue_page(page) for page in range(len(level_8_dialogue))]

                current_page = 0
                dialogue_message = await ctx.send(embed=pages[current_page])

                # Define reactions for pagination
                reactions = ["⬅️", "➡️"]

                for reaction in reactions:
                    await dialogue_message.add_reaction(reaction)

                def check(reaction, user):
                    return user == ctx.author and str(reaction.emoji) in reactions

                while current_page < len(pages) - 1:  # Check the length of the 'pages' list
                    try:
                        reaction, user = await self.bot.wait_for("reaction_add", check=check, timeout=60)

                        if str(reaction.emoji) == "⬅️":
                            current_page = max(0, current_page - 1)
                        elif str(reaction.emoji) == "➡️":
                            current_page = min(len(pages) - 1, current_page + 1)

                        await dialogue_message.edit(embed=pages[current_page])
                        if ctx.guild and ctx.guild.me.guild_permissions.manage_messages:
                            await reaction.remove(user)

                    except asyncio.TimeoutError:
                        break
                # Start the battle after all dialogue
                await ctx.send("The battle begins!")  # Include the current dialogue page as the embed

            if level == 9:
                level_data = self.levels[level]
                face_image_url = "https://i.ibb.co/GC8V9cq/download-31.jpghttps://i.ibb.co/GC8V9cq/download-31.jpg"
                level_9_dialogue = [
                    f"Dragonlord Zaldrak: (Emerging from the icy winds) {name_value}, you tread upon the Frozen Abyss. I am the Dragonlord Zaldrak, and your presence chills me to the bone.",
                    f"{name_value}: (With steely resolve) I've come to conquer the tower. What frigid challenges lie ahead, Dragonlord Zaldrak?",
                    f"Lizardman: (With reptilian cunning) Challenges as cold as the abyss itself. Will your spirit thaw in the face of despair?",
                    f"Dragonkin: (With a fiery breath) We shall engulf you in frost and flame."
                ]

                # Create an embed for the Abyssal Guardian's dialogue
                embed = discord.Embed(title="Dragonlord Zaldrak", color=0x003366)
                embed.set_thumbnail(url=face_image_url)

                # Function to create dialogue pages with specified titles, avatars, and thumbnails
                def create_dialogue_page(page):
                    dialogue_embed = discord.Embed(
                        title="Dragonlord Zaldrak" if page == 0 else name_value if page == 1 else "Lizardman" if page == 2 else "Dragonkin",
                        color=0x003366,
                        description=level_9_dialogue[page]
                    )

                    # Set the Imp's thumbnail for the 4th dialogue
                    if page == 0:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/Q7VMzD0/download-30.jpg")
                    elif page == 1:
                        if ctx.author.avatar is not None:
                            dialogue_embed.set_thumbnail(url=ctx.author.avatar.url)
                        else:
                            dialogue_embed.set_thumbnail(url=ctx.author.default_avatar.url)

                    elif page == 2:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/GC8V9cq/download-31.jpg")
                    elif page == 3:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/2ckDS1k/download-32.jpg")

                    return dialogue_embed

                pages = [create_dialogue_page(page) for page in range(len(level_9_dialogue))]

                current_page = 0
                dialogue_message = await ctx.send(embed=pages[current_page])

                # Define reactions for pagination
                reactions = ["⬅️", "➡️"]

                for reaction in reactions:
                    await dialogue_message.add_reaction(reaction)

                def check(reaction, user):
                    return user == ctx.author and str(reaction.emoji) in reactions

                while current_page < len(pages) - 1:  # Check the length of the 'pages' list
                    try:
                        reaction, user = await self.bot.wait_for("reaction_add", check=check, timeout=60)

                        if str(reaction.emoji) == "⬅️":
                            current_page = max(0, current_page - 1)
                        elif str(reaction.emoji) == "➡️":
                            current_page = min(len(pages) - 1, current_page + 1)

                        await dialogue_message.edit(embed=pages[current_page])
                        if ctx.guild and ctx.guild.me.guild_permissions.manage_messages:
                            await reaction.remove(user)

                    except asyncio.TimeoutError:
                        break
                # Start the battle after all dialogue
                await ctx.send("The battle begins!")  # Include the current dialogue page as the embed

            if level == 10:
                level_data = self.levels[level]
                face_image_url = "https://i.ibb.co/5TYNLrc/download-33.jpg"
                level_10_dialogue = [
                    f"Soulreaver Lurkthar: (Manifesting from the void) {name_value}, you have reached the Ethereal Nexus, a realm beyond your comprehension. I am Soulreaver Lurkthar, and you are insignificant.",
                    f"{name_value}: (With unyielding determination) I've come this far. What secrets does this realm hold, Soulreaver Lurkthar?",
                    "Haunted Spirit: (With spectral whispers) Secrets that unravel sanity and defy reality. Are you prepared for the abyss of the unknown?",
                    "Phantom Wraith: (With an ethereal presence) Your mind will crumble in the presence of the enigma."
                ]

                # Create an embed for the Abyssal Guardian's dialogue
                embed = discord.Embed(title="Frostfire Behemoth", color=0x003366)
                embed.set_thumbnail(url=face_image_url)

                # Function to create dialogue pages with specified titles, avatars, and thumbnails
                def create_dialogue_page(page):
                    dialogue_embed = discord.Embed(
                        title="Soulreaver Lurkthar" if page == 0 else name_value if page == 1 else "Haunted Spirit" if page == 2 else "Phantom Wraith",
                        color=0x003366,
                        description=level_10_dialogue[page]
                    )

                    # Set the Imp's thumbnail for the 4th dialogue
                    if page == 0:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/5TYNLrc/download-33.jpg")
                    elif page == 1:
                        if ctx.author.avatar is not None:
                            dialogue_embed.set_thumbnail(url=ctx.author.avatar.url)
                        else:
                            dialogue_embed.set_thumbnail(url=ctx.author.default_avatar.url)

                    elif page == 2:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/kB5ypsM/download-34.jpg")
                    elif page == 3:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/6BTRt3s/download-35.jpg")

                    return dialogue_embed

                pages = [create_dialogue_page(page) for page in range(len(level_10_dialogue))]

                current_page = 0
                dialogue_message = await ctx.send(embed=pages[current_page])

                # Define reactions for pagination
                reactions = ["⬅️", "➡️"]

                for reaction in reactions:
                    await dialogue_message.add_reaction(reaction)

                def check(reaction, user):
                    return user == ctx.author and str(reaction.emoji) in reactions

                while current_page < len(pages) - 1:  # Check the length of the 'pages' list
                    try:
                        reaction, user = await self.bot.wait_for("reaction_add", check=check, timeout=60)

                        if str(reaction.emoji) == "⬅️":
                            current_page = max(0, current_page - 1)
                        elif str(reaction.emoji) == "➡️":
                            current_page = min(len(pages) - 1, current_page + 1)

                        await dialogue_message.edit(embed=pages[current_page])
                        if ctx.guild and ctx.guild.me.guild_permissions.manage_messages:
                            await reaction.remove(user)

                    except asyncio.TimeoutError:
                        break
                # Start the battle after all dialogue
                await ctx.send("The battle begins!")  # Include the current dialogue page as the embed

            if level == 11:
                level_data = self.levels[level]
                face_image_url = "https://i.ibb.co/5TYNLrc/download-33.jpg"
                level_11_dialogue = [
                    f"Ravengaze Alpha: (Rising from the shadows) {name_value}, you have ventured into the dreaded Ravengaze Highlands. I am the Ravengaze Alpha, and this is my hunting ground. Prepare for your demise.",
                    f"{name_value}: (With indomitable resolve) I've come to conquer this tower. What challenges await, Ravengaze Alpha?",
                    f"Gnoll Raider: (With savage fervor) Challenges that will make you pray for mercy. Do you have what it takes to survive?",
                    f"Hyena Pack: (With menacing laughter) *growls*"
                ]

                # Create an embed for the Abyssal Guardian's dialogue
                embed = discord.Embed(title="Ravengaze Alpha", color=0x003366)
                embed.set_thumbnail(url=face_image_url)

                # Function to create dialogue pages with specified titles, avatars, and thumbnails
                def create_dialogue_page(page):
                    dialogue_embed = discord.Embed(
                        title="Ravengaze Alpha" if page == 0 else name_value if page == 1 else "Gnoll Raider" if page == 2 else "Hyena Pack",
                        color=0x003366,
                        description=level_11_dialogue[page]
                    )

                    # Set the Imp's thumbnail for the 4th dialogue
                    if page == 0:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/YjqfWSc/download-8.jpg")
                    elif page == 1:
                        if ctx.author.avatar is not None:
                            dialogue_embed.set_thumbnail(url=ctx.author.avatar.url)
                        else:
                            dialogue_embed.set_thumbnail(url=ctx.author.default_avatar.url)

                    elif page == 2:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/kJyTsWL/download-11.jpg")
                    elif page == 3:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/Y7w2Sy4/download-12.jpg")

                    return dialogue_embed

                pages = [create_dialogue_page(page) for page in range(len(level_11_dialogue))]

                current_page = 0
                dialogue_message = await ctx.send(embed=pages[current_page])

                # Define reactions for pagination
                reactions = ["⬅️", "➡️"]

                for reaction in reactions:
                    await dialogue_message.add_reaction(reaction)

                def check(reaction, user):
                    return user == ctx.author and str(reaction.emoji) in reactions

                while current_page < len(pages) - 1:  # Check the length of the 'pages' list
                    try:
                        reaction, user = await self.bot.wait_for("reaction_add", check=check, timeout=60)

                        if str(reaction.emoji) == "⬅️":
                            current_page = max(0, current_page - 1)
                        elif str(reaction.emoji) == "➡️":
                            current_page = min(len(pages) - 1, current_page + 1)

                        await dialogue_message.edit(embed=pages[current_page])
                        if ctx.guild and ctx.guild.me.guild_permissions.manage_messages:
                            await reaction.remove(user)

                    except asyncio.TimeoutError:
                        break
                # Start the battle after all dialogue
                await ctx.send("The battle begins!")  # Include the current dialogue page as the embed

            if level == 12:
                level_data = self.levels[level]
                face_image_url = "https://i.ibb.co/5TYNLrc/download-33.jpg"
                level_12_dialogue = [
                    f"Nightshade Serpentis: (Emerging from the shadows) {name_value}, you stand within the cursed Nocturne Domain. I am Nightshade Serpentis, and your fate is sealed.",
                    f"{name_value}: (With unwavering determination) I've come to conquer the tower. What nightmares do you bring, Nightshade Serpentis?",
                    f"Gloomhound: (With eerie howling) Nightmares that will haunt your every thought. Do you have the courage to face them?",
                    f"Nocturne Stalker: (With a sinister grin) Your resolve will crumble under the weight of your own dread."
                ]

                # Create an embed for the Abyssal Guardian's dialogue
                embed = discord.Embed(title="Frostfire Behemoth", color=0x003366)
                embed.set_thumbnail(url=face_image_url)

                # Function to create dialogue pages with specified titles, avatars, and thumbnails
                def create_dialogue_page(page):
                    dialogue_embed = discord.Embed(
                        title="Nightshade Serpentis" if page == 0 else name_value if page == 1 else "Gloomhound" if page == 2 else "Nocturne Stalker",
                        color=0x003366,
                        description=level_12_dialogue[page]
                    )

                    # Set the Imp's thumbnail for the 4th dialogue
                    if page == 0:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/4TtY6T9/download-14.jpg")
                    elif page == 1:
                        if ctx.author.avatar is not None:
                            dialogue_embed.set_thumbnail(url=ctx.author.avatar.url)
                        else:
                            dialogue_embed.set_thumbnail(url=ctx.author.default_avatar.url)

                    elif page == 2:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/0BGmFXZ/download-15.jpg")
                    elif page == 3:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/svhv2XJ/download-16.jpg")

                    return dialogue_embed

                pages = [create_dialogue_page(page) for page in range(len(level_12_dialogue))]

                current_page = 0
                dialogue_message = await ctx.send(embed=pages[current_page])

                # Define reactions for pagination
                reactions = ["⬅️", "➡️"]

                for reaction in reactions:
                    await dialogue_message.add_reaction(reaction)

                def check(reaction, user):
                    return user == ctx.author and str(reaction.emoji) in reactions

                while current_page < len(pages) - 1:  # Check the length of the 'pages' list
                    try:
                        reaction, user = await self.bot.wait_for("reaction_add", check=check, timeout=60)

                        if str(reaction.emoji) == "⬅️":
                            current_page = max(0, current_page - 1)
                        elif str(reaction.emoji) == "➡️":
                            current_page = min(len(pages) - 1, current_page + 1)

                        await dialogue_message.edit(embed=pages[current_page])
                        if ctx.guild and ctx.guild.me.guild_permissions.manage_messages:
                            await reaction.remove(user)

                    except asyncio.TimeoutError:
                        break
                # Start the battle after all dialogue
                await ctx.send("The battle begins!")  # Include the current dialogue page as the embed

            if level == 13:
                level_data = self.levels[level]
                face_image_url = "https://i.ibb.co/5TYNLrc/download-33.jpg"
                level_13_dialogue = [
                    f"Ignis Inferno: (Rising from molten flames) {name_value}, you have entered the Pyroclasmic Abyss, a realm of searing torment. I am Ignis Inferno, and your presence will fuel the flames of destruction.",
                    f"{name_value}: (Unyielding) I've come to conquer this tower. What scorching challenges do you present, Ignis Inferno?",
                    f"Magma Elemental: (With fiery rage) Challenges as relentless as the molten core itself. Are you prepared to endure the unending inferno?",
                    f"Inferno Imp: (With malevolent glee) Your flesh will sear, and your spirit will smolder."
                ]

                # Create an embed for the Abyssal Guardian's dialogue
                embed = discord.Embed(title="Frostfire Behemoth", color=0x003366)
                embed.set_thumbnail(url=face_image_url)

                # Function to create dialogue pages with specified titles, avatars, and thumbnails
                def create_dialogue_page(page):
                    dialogue_embed = discord.Embed(
                        title="Ignis Inferno" if page == 0 else name_value if page == 1 else "Magma Elemental" if page == 2 else "Inferno Imp",
                        color=0x003366,
                        description=level_13_dialogue[page]
                    )

                    # Set the Imp's thumbnail for the 4th dialogue
                    if page == 0:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/HYcdZBy/download-17.jpg")
                    elif page == 1:
                        if ctx.author.avatar is not None:
                            dialogue_embed.set_thumbnail(url=ctx.author.avatar.url)
                        else:
                            dialogue_embed.set_thumbnail(url=ctx.author.default_avatar.url)

                    elif page == 2:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/K0tG23M/download-18.jpg")
                    elif page == 3:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/2ZgBn44/download-20.jpg")

                    return dialogue_embed

                pages = [create_dialogue_page(page) for page in range(len(level_13_dialogue))]

                current_page = 0
                dialogue_message = await ctx.send(embed=pages[current_page])

                # Define reactions for pagination
                reactions = ["⬅️", "➡️"]

                for reaction in reactions:
                    await dialogue_message.add_reaction(reaction)

                def check(reaction, user):
                    return user == ctx.author and str(reaction.emoji) in reactions

                while current_page < len(pages) - 1:  # Check the length of the 'pages' list
                    try:
                        reaction, user = await self.bot.wait_for("reaction_add", check=check, timeout=60)

                        if str(reaction.emoji) == "⬅️":
                            current_page = max(0, current_page - 1)
                        elif str(reaction.emoji) == "➡️":
                            current_page = min(len(pages) - 1, current_page + 1)

                        await dialogue_message.edit(embed=pages[current_page])
                        if ctx.guild and ctx.guild.me.guild_permissions.manage_messages:
                            await reaction.remove(user)

                    except asyncio.TimeoutError:
                        break
                # Start the battle after all dialogue
                await ctx.send("The battle begins!")  # Include the current dialogue page as the embed

            if level == 14:
                level_data = self.levels[level]
                face_image_url = "https://i.ibb.co/5TYNLrc/download-33.jpg"
                level_14_dialogue = [
                    f"Wraithlord Maroth: (Emerging from the spectral mists) {name_value}, you have reached the spectral wastes, a realm of eternal torment. I am Wraithlord Maroth, and your suffering will echo through the void.",
                    f"{name_value}: (With unwavering determination) I've come to conquer the tower. What spectral horrors await, Wraithlord Maroth?",
                    f"Cursed Banshee: (With haunting wails) Horrors that will rend your soul asunder. Do you have the will to endure?",
                    f"Spectral Harbinger: (With a malevolent whisper) Your torment shall be everlasting."
                ]

                # Create an embed for the Abyssal Guardian's dialogue
                embed = discord.Embed(title="Frostfire Behemoth", color=0x003366)
                embed.set_thumbnail(url=face_image_url)

                # Function to create dialogue pages with specified titles, avatars, and thumbnails
                def create_dialogue_page(page):
                    dialogue_embed = discord.Embed(
                        title="Wraithlord Maroth" if page == 0 else name_value if page == 1 else "Cursed Banshee" if page == 2 else "Spectral Harbinger",
                        color=0x003366,
                        description=level_14_dialogue[page]
                    )

                    # Set the Imp's thumbnail for the 4th dialogue
                    if page == 0:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/56dsQMY/download-21.jpg")
                    elif page == 1:
                        if ctx.author.avatar is not None:
                            dialogue_embed.set_thumbnail(url=ctx.author.avatar.url)
                        else:
                            dialogue_embed.set_thumbnail(url=ctx.author.default_avatar.url)

                    elif page == 2:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/pLP2djF/download-22.jpg")
                    elif page == 3:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/R0PdqJ7/download-23.jpg")

                    return dialogue_embed

                pages = [create_dialogue_page(page) for page in range(len(level_14_dialogue))]

                current_page = 0
                dialogue_message = await ctx.send(embed=pages[current_page])

                # Define reactions for pagination
                reactions = ["⬅️", "➡️"]

                for reaction in reactions:
                    await dialogue_message.add_reaction(reaction)

                def check(reaction, user):
                    return user == ctx.author and str(reaction.emoji) in reactions

                while current_page < len(pages) - 1:  # Check the length of the 'pages' list
                    try:
                        reaction, user = await self.bot.wait_for("reaction_add", check=check, timeout=60)

                        if str(reaction.emoji) == "⬅️":
                            current_page = max(0, current_page - 1)
                        elif str(reaction.emoji) == "➡️":
                            current_page = min(len(pages) - 1, current_page + 1)

                        await dialogue_message.edit(embed=pages[current_page])
                        if ctx.guild and ctx.guild.me.guild_permissions.manage_messages:
                            await reaction.remove(user)

                    except asyncio.TimeoutError:
                        break
                # Start the battle after all dialogue
                await ctx.send("The battle begins!")  # Include the current dialogue page as the embed

            if level == 15:
                level_data = self.levels[level]
                face_image_url = "https://i.ibb.co/5TYNLrc/download-33.jpg"
                level_15_dialogue = [
                    f"Infernus, the Infernal: (Emerging from the depths of fire) {name_value}, you have entered the Infernal Abyss, a realm of unrelenting flames. I am Infernus, the Infernal, and your existence will be consumed by the inferno.",
                    f"{name_value}: (Unyielding) I've come this far, and I won't be deterred. What blazing trials do you have in store, Infernus?",
                    f"Demonic Imp: (With a malevolent grin) Trials that will scorch your very soul. Are you prepared to burn?",
                    f"Hellspawn Reaver: (With fiery eyes) The flames of your doom shall be unquenchable."
                ]

                # Create an embed for the Abyssal Guardian's dialogue
                embed = discord.Embed(title="Frostfire Behemoth", color=0x003366)
                embed.set_thumbnail(url=face_image_url)

                # Function to create dialogue pages with specified titles, avatars, and thumbnails
                def create_dialogue_page(page):
                    dialogue_embed = discord.Embed(
                        title="Infernus, the Infernal" if page == 0 else name_value if page == 1 else "Demonic Imp" if page == 2 else "Hellspawn Reaver",
                        color=0x003366,
                        description=level_15_dialogue[page]
                    )

                    # Set the Imp's thumbnail for the 4th dialogue
                    if page == 0:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/R2Nm6vY/download-24.jpg")
                    elif page == 1:
                        if ctx.author.avatar is not None:
                            dialogue_embed.set_thumbnail(url=ctx.author.avatar.url)
                        else:
                            dialogue_embed.set_thumbnail(url=ctx.author.default_avatar.url)

                    elif page == 2:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/GdhXTWN/download-25.jpg")
                    elif page == 3:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/ZftX1xB/download-26.jpg")

                    return dialogue_embed

                pages = [create_dialogue_page(page) for page in range(len(level_15_dialogue))]

                current_page = 0
                dialogue_message = await ctx.send(embed=pages[current_page])

                # Define reactions for pagination
                reactions = ["⬅️", "➡️"]

                for reaction in reactions:
                    await dialogue_message.add_reaction(reaction)

                def check(reaction, user):
                    return user == ctx.author and str(reaction.emoji) in reactions

                while current_page < len(pages) - 1:  # Check the length of the 'pages' list
                    try:
                        reaction, user = await self.bot.wait_for("reaction_add", check=check, timeout=60)

                        if str(reaction.emoji) == "⬅️":
                            current_page = max(0, current_page - 1)
                        elif str(reaction.emoji) == "➡️":
                            current_page = min(len(pages) - 1, current_page + 1)

                        await dialogue_message.edit(embed=pages[current_page])
                        if ctx.guild and ctx.guild.me.guild_permissions.manage_messages:
                            await reaction.remove(user)

                    except asyncio.TimeoutError:
                        break
                # Start the battle after all dialogue
                await ctx.send("The battle begins!")  # Include the current dialogue page as the embed

            if level == 16:
                async with self.bot.pool.acquire() as connection:
                    query = 'SELECT "user" FROM profile WHERE "user" != $1 ORDER BY RANDOM() LIMIT 2'
                    random_users = await connection.fetch(query, ctx.author.id)

                    # Extracting the user IDs
                    random_user_objects = []

                    for user in random_users:
                        user_id = user['user']
                        # Fetch user object from ID
                        fetched_user = await self.bot.fetch_user(user_id)
                        if fetched_user:
                            random_user_objects.append(fetched_user)

                    # Ensure two separate user objects are obtained
                    if len(random_user_objects) >= 2:
                        random_user_object_1 = random_user_objects[0]
                        random_user_object_2 = random_user_objects[1]
                        # await ctx.send(f"{random_user_object_1.display_name} {random_user_object_2.display_name}")
                    else:
                        # Handle case if there are fewer than 2 non-author users in the database
                        return None, None
                level_data = self.levels[level]
                face_image_url = "https://gcdnb.pbrd.co/images/ueKgTmbvB8qb.jpg"
                level_16_dialogue = [
                    f"Master Shapeshifter: In the dance of shadows, I am the conductor—every face you've known, every trust betrayed, I've worn like a symphony; now, join my orchestra or become its crescendo.",
                    f"{name_value}: In your game of deceit, I see only a feeble attempt to shroud the inevitable. Your illusions crumble against my unyielding will—cross me, and witness the true horror of defiance.",
                    f"{random_user_object_1.display_name}: I mimic your friend's form, but within me, your worst nightmares lurk, a puppeteer of your trust, feeding on your doubt and fear, reveling in the impending doom.",
                    f"{random_user_object_2.display_name}: I've assumed your confidant's guise, yet beneath this borrowed skin, your anxieties writhe, whispering your secrets; in the labyrinth of your mind, I'm the embodiment of your darkest apprehensions, ready to consume your hopes."
                ]

                # Create an embed for the Abyssal Guardian's dialogue
                embed = discord.Embed(title="Master Shapeshifter", color=0x003366)
                embed.set_thumbnail(url=face_image_url)

                # Function to create dialogue pages with specified titles, avatars, and thumbnails
                def create_dialogue_page(page):
                    dialogue_embed = discord.Embed(
                        title="Master Shapeshifter" if page == 0 else name_value if page == 1 else random_user_object_1.display_name if page == 2 else random_user_object_2.display_name,
                        color=0x003366,
                        description=level_16_dialogue[page]
                    )

                    # Set the Imp's thumbnail for the 4th dialogue
                    default_avatar_url = "https://ia803204.us.archive.org/4/items/discordprofilepictures/discordblue.png"

                    if page == 0:
                        thumbnail_url = "https://gcdnb.pbrd.co/images/ueKgTmbvB8qb.jpg"
                    elif page == 1:
                        thumbnail_url = ctx.author.avatar.url if ctx.author.avatar else default_avatar_url
                    elif page == 2:
                        thumbnail_url = random_user_object_1.avatar.url if random_user_object_1.avatar else default_avatar_url
                    elif page == 3:
                        thumbnail_url = random_user_object_2.avatar.url if random_user_object_2.avatar else default_avatar_url

                    dialogue_embed.set_thumbnail(url=thumbnail_url)

                    return dialogue_embed

                pages = [create_dialogue_page(page) for page in range(len(level_16_dialogue))]

                current_page = 0
                dialogue_message = await ctx.send(embed=pages[current_page])

                # Define reactions for pagination
                reactions = ["⬅️", "➡️"]

                for reaction in reactions:
                    await dialogue_message.add_reaction(reaction)

                def check(reaction, user):
                    return user == ctx.author and str(reaction.emoji) in reactions

                while current_page < len(pages) - 1:  # Check the length of the 'pages' list
                    try:
                        reaction, user = await self.bot.wait_for("reaction_add", check=check, timeout=60)

                        if str(reaction.emoji) == "⬅️":
                            current_page = max(0, current_page - 1)
                        elif str(reaction.emoji) == "➡️":
                            current_page = min(len(pages) - 1, current_page + 1)

                        await dialogue_message.edit(embed=pages[current_page])
                        if ctx.guild and ctx.guild.me.guild_permissions.manage_messages:
                            await reaction.remove(user)

                    except asyncio.TimeoutError:
                        break
                # Start the battle after all dialogue
                await ctx.send("The battle begins!")  # Include the current dialogue page as the embed

            # ===============================================================================================
            # ===============================================================================================

            if level == 17:
                level_data = self.levels[level]
                face_image_url = "https://i.ibb.co/R2v9jYs/image2.png"
                level_15_dialogue = [
                    f"Eldritch Devourer: (Rising from the cosmic abyss) {name_value}, you stand at the Convergence Nexus, a junction of cosmic forces. I am the Eldritch Devourer, and your futile resistance will be devoured by the void.",
                    f"{name_value}: (Fierce) I've carved my path through the chaos, and your cosmic feast won't satiate your hunger. Prepare for annihilation, Devourer.",
                    f"Chaos Fiend: (With malicious glee) Annihilation, you say? The chaos you face is beyond comprehension. Your defiance is merely a flicker against the impending cosmic storm.",
                    f"Voidborn Horror: (With eyes like swirling galaxies) Your essence will dissipate into the cosmic winds. Prepare for oblivion, interloper."
                ]

                # Create an embed for the Abyssal Guardian's dialogue
                embed = discord.Embed(title="Eldritch Devourer", color=0x003366)
                embed.set_thumbnail(url=face_image_url)

                # Function to create dialogue pages with specified titles, avatars, and thumbnails
                def create_dialogue_page(page):
                    dialogue_embed = discord.Embed(
                        title="Eldritch Devourer" if page == 0 else name_value if page == 1 else "Chaos Fiend" if page == 2 else "Voidborn Horror",
                        color=0x003366,
                        description=level_15_dialogue[page]
                    )

                    # Set the Imp's thumbnail for the 4th dialogue
                    if page == 0:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/R2v9jYs/image2.png")
                    elif page == 1:
                        dialogue_embed.set_thumbnail(url=ctx.author.avatar.url)
                    elif page == 2:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/c22vsWF/image.png")
                    elif page == 3:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/kyvTszF/image3.png")

                    return dialogue_embed

                pages = [create_dialogue_page(page) for page in range(len(level_15_dialogue))]

                current_page = 0
                dialogue_message = await ctx.send(embed=pages[current_page])

                # Define reactions for pagination
                reactions = ["⬅️", "➡️"]

                for reaction in reactions:
                    await dialogue_message.add_reaction(reaction)

                def check(reaction, user):
                    return user == ctx.author and str(reaction.emoji) in reactions

                while current_page < len(pages) - 1:  # Check the length of the 'pages' list
                    try:
                        reaction, user = await self.bot.wait_for("reaction_add", check=check, timeout=60)

                        if str(reaction.emoji) == "⬅️":
                            current_page = max(0, current_page - 1)
                        elif str(reaction.emoji) == "➡️":
                            current_page = min(len(pages) - 1, current_page + 1)

                        await dialogue_message.edit(embed=pages[current_page])
                        if ctx.guild and ctx.guild.me.guild_permissions.manage_messages:
                            await reaction.remove(user)

                    except asyncio.TimeoutError:
                        break
                # Start the battle after all dialogue
                await ctx.send("The battle begins!")  # Include the current dialogue page as the embed

            # ===============================================================================================
            # ===============================================================================================
            # ===============================================================================================
            # ===============================================================================================

            if level == 18:
                level_data = self.levels[level]
                face_image_url = "https://i.ibb.co/0DjDgy5/image4.png"
                level_15_dialogue = [
                    f"Dreadlord Vortigon: (Unfurling shadowy wings) {name_value}, your presence disrupts the harmony of shadows. I am Dreadlord Vortigon, and your defiance will be swallowed by the eternal night.",
                    f"{name_value}: (Menacing) The shadows won't shield you from the reckoning I bring, Dreadlord. Prepare for your eternal night to meet its dawn of doom.",
                    f"Blood Warden: (With vampiric grace) Doom, you say? Your life force will sustain the shadows, but it won't save you from the crimson embrace of the Blood Warden.",
                    f"Juzam Djinn: (With an otherworldly sneer) Taste? Your arrogance is amusing, mortal. The price for entering this domain is your torment, inflicted by the Juzam Djinn."
                ]

                # Create an embed for the Abyssal Guardian's dialogue
                embed = discord.Embed(title="Dreadlord Vortigon", color=0x003366)
                embed.set_thumbnail(url=face_image_url)

                # Function to create dialogue pages with specified titles, avatars, and thumbnails
                def create_dialogue_page(page):
                    dialogue_embed = discord.Embed(
                        title="Dreadlord Vortigon" if page == 0 else name_value if page == 1 else "Blood Warden" if page == 2 else "Juzam Djinn",
                        color=0x003366,
                        description=level_15_dialogue[page]
                    )

                    # Set the Imp's thumbnail for the 4th dialogue
                    if page == 0:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/0DjDgy5/image4.png")
                    elif page == 1:
                        dialogue_embed.set_thumbnail(url=ctx.author.avatar.url)
                    elif page == 2:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/t4QJ8ym/image5.png")
                    elif page == 3:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/bRYv2Db/image6.png")

                    return dialogue_embed

                pages = [create_dialogue_page(page) for page in range(len(level_15_dialogue))]

                current_page = 0
                dialogue_message = await ctx.send(embed=pages[current_page])

                # Define reactions for pagination
                reactions = ["⬅️", "➡️"]

                for reaction in reactions:
                    await dialogue_message.add_reaction(reaction)

                def check(reaction, user):
                    return user == ctx.author and str(reaction.emoji) in reactions

                while current_page < len(pages) - 1:  # Check the length of the 'pages' list
                    try:
                        reaction, user = await self.bot.wait_for("reaction_add", check=check, timeout=60)

                        if str(reaction.emoji) == "⬅️":
                            current_page = max(0, current_page - 1)
                        elif str(reaction.emoji) == "➡️":
                            current_page = min(len(pages) - 1, current_page + 1)

                        await dialogue_message.edit(embed=pages[current_page])
                        if ctx.guild and ctx.guild.me.guild_permissions.manage_messages:
                            await reaction.remove(user)

                    except asyncio.TimeoutError:
                        break
                # Start the battle after all dialogue
                await ctx.send("The battle begins!")  # Include the current dialogue page as the embed

            # ===============================================================================================
            # ===============================================================================================

            if level == 19:
                # Start the battle after all dialogue
                await ctx.send("The battle begins!")  # Include the current dialogue page as the embed

            if level == 20:
                # Start the battle after all dialogue
                await ctx.send("The battle begins!")  # Include the current dialogue page as the embed

            if level == 21:
                # Start the battle after all dialogue
                await ctx.send("The battle begins!")  # Include the current dialogue page as the embed

            if level == 22:
                # Start the battle after all dialogue
                await ctx.send("The battle begins!")  # Include the current dialogue page as the embed

            if level == 23:
                # Start the battle after all dialogue
                await ctx.send("The battle begins!")  # Include the current dialogue page as the embed

            if level == 24:
                # Start the battle after all dialogue
                await ctx.send("The battle begins!")  # Include the current dialogue page as the embed

            if level == 25:
                # Start the battle after all dialogue
                await ctx.send("The battle begins!")  # Include the current dialogue page as the embed

            if level == 26:
                # Start the battle after all dialogue
                await ctx.send("The battle begins!")  # Include the current dialogue page as the embed

            if level == 27:
                # Start the battle after all dialogue
                await ctx.send("The battle begins!")  # Include the current dialogue page as the embed

            if level == 28:
                # Start the battle after all dialogue
                await ctx.send("The battle begins!")  # Include the current dialogue page as the embed

            if level == 29:
                # Start the battle after all dialogue
                await ctx.send("The battle begins!")  # Include the current dialogue page as the embed

            if level == 30:
                # Start the battle after all dialogue
                await ctx.send("The battle begins!")  # Include the current dialogue page as the embed

            # ===============================================================================================
            # ===============================================================================================

            if level == 0:
                await self.remove_player_from_fight(ctx.author.id)
                if player_balance < 10000:
                    await self.bot.reset_cooldown(ctx)
                    return await ctx.send(
                        f"{ctx.author.mention}, you do not have enough money to pay the entry fee. Consider earning at least **$10,000** before approaching the Battle Tower."
                    )


                else:
                    confirm = await ctx.confirm(
                        message="Are you sure you want to proceed with this level? It will cost you **$10,000** This is a one time fee.",
                        timeout=10)
                try:
                    if confirm is not None:
                        await ctx.send("You chosen to approach the gates.")
                    else:
                        await self.bot.reset_cooldown(ctx)
                        await ctx.send("You chosen not to approach the gates.")
                except Exception as e:
                    error_message = f"{e}"
                    await ctx.send(error_message)
                    await self.bot.reset_cooldown(ctx)

                # Create dialogue for paying the entry fee
                entry_fee_dialogue = [
                    "Guard: Halt, brave traveler! You now stand before the awe-inspiring entrance to the Battle Tower, a place where legends are forged and glory awaits. However, passage through this imposing gate comes at a price, a test of your commitment to the path of champions.",
                    f"{name_value}: (Your eyes are fixed on the grand tower) How much must I offer to open this formidable gate?",
                    "Guard: (The guardian, armored and stern, lowers their towering spear) The entry fee, is no trifling matter. It demands a substantial **$10,000**. Prove your dedication by paying this fee now, and the path of champions shall be unveiled before you.",
                    f"{name_value}: (Resolute and unwavering) Very well, here is **$10,000**, a token of my unwavering resolve.",
                    "Guard: (With a slow nod of approval) Your decision is wise, traveler. With your payment, you have taken your first step into the hallowed tower. Now, proceed to level 1, where the Abyssal Guardian awaits your challenge. Be prepared for the battles that lie ahead."
                ]

                embed = discord.Embed(title="Guard", color=0x003366)
                current_page = 0
                face_image_url = "https://i.ibb.co/CWTp4xf/download.jpg"
                entry_fee_pages = [self.create_dialogue_page(page, level, ctx, name_value, entry_fee_dialogue, [],
                                                             "https://i.ibb.co/CWTp4xf/download.jpg") for page in
                                   range(len(entry_fee_dialogue))]

                entry_fee_message = await ctx.send(embed=entry_fee_pages[current_page])

                # Define reactions for pagination
                reactions = ["⬅️", "➡️"]
                for reaction in reactions:
                    await entry_fee_message.add_reaction(reaction)

                def check(reaction, user):
                    return user == ctx.author and str(reaction.emoji) in reactions

                while current_page < len(entry_fee_dialogue) - 1:
                    try:
                        reaction, user = await self.bot.wait_for("reaction_add", check=check, timeout=60)

                        if str(reaction.emoji) == "⬅️":
                            current_page = max(0, current_page - 1)
                        elif str(reaction.emoji) == "➡️":
                            current_page = min(len(entry_fee_pages) - 1, current_page + 1)

                        await entry_fee_message.edit(embed=entry_fee_pages[current_page])
                        if ctx.guild and ctx.guild.me.guild_permissions.manage_messages:
                            await reaction.remove(user)

                    except asyncio.TimeoutError:
                        return await ctx.send("Timed out.")

                    if current_page == 4:
                        async with self.bot.pool.acquire() as connection:
                            # Adjust this code to match your database structure
                            entry_fee = 10000  # The entry fee amount
                            if player_balance < entry_fee:
                                return await ctx.send("An error has occurred: You can no longer afford this.")
                            await connection.execute('UPDATE profile SET money = money - $1 WHERE "user" = $2',
                                                     entry_fee, ctx.author.id)

                            await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                     ctx.author.id)

                            await self.bot.reset_cooldown(ctx)
                            return await ctx.send(
                                f"{ctx.author.mention}, you've paid the entry fee of ${entry_fee}. You may proceed to level 1 using `$battletower fight`.")

            if level == 1:
                level_data = self.levels[level]
                face_image_url = "https://i.ibb.co/MgMbRjF/download-4.jpg"
                dialogue = [
                    "[Abyssal Guardian]: With a bone-chilling hiss, the Abyssal Guardian emerges from the shadows, its eyes glowing with malevolence. Its obsidian armor exudes an aura of dread.",
                    f"[{name_value}]: (Defiance in your voice) 'I've come to reclaim the Battle Tower and restore its glory,' you proclaim. Your voice echoes through the chamber, unwavering.",
                    "[Abyssal Guardian]: (Raising its enormous spear high) 'Reclaim the tower, you say?' it taunts. 'Hahaha! You'll need more than bravado to defeat me. Prepare to face the abyss itself!'",
                    "[Imp]: (Cackling with wicked glee) The impish creature appears at the guardian's side. 'Oh boy! I am starving. Gimme gimme!!'",
                    f"[{name_value}]: (Radiant aura surrounding you) 'My resolve remains unshaken,' you declare. 'With the blessings of {god_value}, I shall bring your reign to an end!'"
                ]

                # Create an embed for the Abyssal Guardian's dialogue
                embed = discord.Embed(title="Abyssal Guardian", color=0x003366)
                embed.set_thumbnail(url=face_image_url)

                # Function to create dialogue pages with specified titles, avatars, and thumbnails
                def create_dialogue_page(page):
                    dialogue_embed = discord.Embed(
                        title="Abyssal Guardian" if page == 0 else name_value if page == 1 else "Abyssal Guardian" if page == 2 else "Imp" if page == 3 else name_value,
                        color=0x003366,
                        description=dialogue[page]
                    )

                    # Set the Imp's thumbnail for the 4th dialogue
                    if page == 3:
                        dialogue_embed.set_thumbnail(url="https://i.ibb.co/vYBdn7j/download-7.jpg")
                    else:
                        # Set the player's profile picture as the thumbnail for dialogues 1 and 5
                        thumbnail_url = str(ctx.author.avatar.url) if page in [1, 4] else face_image_url
                        dialogue_embed.set_thumbnail(url=thumbnail_url)

                    return dialogue_embed

                pages = [create_dialogue_page(page) for page in range(len(dialogue))]

                current_page = 0
                dialogue_message = await ctx.send(embed=pages[current_page])

                # Define reactions for pagination
                reactions = ["⬅️", "➡️"]

                for reaction in reactions:
                    await dialogue_message.add_reaction(reaction)

                def check(reaction, user):
                    return user == ctx.author and str(reaction.emoji) in reactions

                while current_page < len(dialogue) - 1:
                    try:
                        reaction, user = await self.bot.wait_for("reaction_add", check=check, timeout=60)

                        if str(reaction.emoji) == "⬅️":
                            current_page = max(0, current_page - 1)
                        elif str(reaction.emoji) == "➡️":
                            current_page = min(len(pages) - 1, current_page + 1)

                        await dialogue_message.edit(embed=pages[current_page])
                        if ctx.guild and ctx.guild.me.guild_permissions.manage_messages:
                            await reaction.remove(user)

                    except asyncio.TimeoutError:
                        break
                # Start the battle after all dialogue
                await ctx.send("The battle begins!")  # Include the current dialogue page as the embed

            minion1_name = level_data["minion1_name"]
            minion2_name = level_data["minion2_name"]
            boss_name = level_data["boss_name"]
            minion1_stats = level_data["minion1"]
            minion2_stats = level_data["minion2"]
            boss_stats = level_data["boss"]

            await self.add_player_to_fight(ctx.author.id)

            # Create a lock for the player if it doesn't exist

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

            # Mage evolution levels and damage multipliers
            mage_evolution_levels = {
                "Witcher": 1,
                "Enchanter": 2,
                "Mage": 3,
                "Warlock": 4,
                "Dark Caster": 5,
                "White Sorcerer": 6,
            }

            evolution_damage_multiplier = {
                1: 1.10,  # 110%
                2: 1.20,  # 120%
                3: 1.30,  # 130%
                4: 1.50,  # 150%
                5: 1.75,  # 175%
                6: 2.00,  # 200%
            }

            try:
                user_id = ctx.author.id
                # Define common queries
                query_class = 'SELECT "class" FROM profile WHERE "user" = $1;'
                query_xp = 'SELECT "xp" FROM profile WHERE "user" = $1;'

                # Query data for ctx.author.id
                result_author = await self.bot.pool.fetch(query_class, ctx.author.id)
                auth_xp = await self.bot.pool.fetch(query_xp, ctx.author.id)

                # Convert XP to level for ctx.author.id
                auth_level = rpgtools.xptolevel(auth_xp[0]['xp'])

                # Initialize chance
                maxedhp = False
                author_chance = 0
                lifestealauth = 0
                lifestealopp = 0
                cheated = False  # Initialize cheated variable
                # Mage evolution level
                author_mage_evolution = None

                if result_author:
                    author_classes = result_author[0]["class"]  # Assume it's a list of classes
                    if isinstance(author_classes, list):
                        author_classes = author_classes
                    else:
                        author_classes = [author_classes]

                    # Function to get Mage evolution level
                    def get_mage_evolution(classes):
                        max_evolution = None
                        for class_name in classes:
                            if class_name in mage_evolution_levels:
                                level = mage_evolution_levels[class_name]
                                if max_evolution is None or level > max_evolution:
                                    max_evolution = level
                        return max_evolution

                    author_mage_evolution = get_mage_evolution(author_classes)

                    for class_name in author_classes:
                        if class_name in specified_words_values:
                            author_chance += specified_words_values[class_name]
                        if class_name in life_steal_values:
                            lifestealauth += life_steal_values[class_name]

            except Exception as e:
                import traceback
                error_message = f"Error occurred: {e}\n"
                error_message += traceback.format_exc()
                await ctx.send(error_message)
                print(error_message)

            if author_chance != 0:
                authorchance = author_chance

            if level != 16:
                levelhp = rpgtools.xptolevel(ctx.character_data["xp"])
                async with self.bot.pool.acquire() as conn:
                    query = 'SELECT "health", "stathp" FROM profile WHERE "user" = $1;'
                    result = await conn.fetchrow(query, user_id)

                if result:
                    # Extract the health value from the result
                    base_health = 250
                    health = result['health'] + base_health
                    stathp = result['stathp'] * 50

                    # Calculate total health based on level and add to current health
                    total_health = health + (levelhp * 5)
                    total_health = total_health + stathp

                newhp = ctx.character_data["health"] + 250 + levelhp
                async with self.bot.pool.acquire() as conn:
                    dmg, deff = await self.bot.get_raidstats(ctx.author, conn=conn)
                player = {"user": ctx.author, "hp": total_health, "armor": deff, "damage": dmg}
                async with self.bot.pool.acquire() as connection:
                    prestige_level = await connection.fetchval('SELECT prestige FROM battletower WHERE id = $1',
                                                               ctx.author.id)
                    level = await connection.fetchval('SELECT level FROM battletower WHERE id = $1', ctx.author.id)
                if prestige_level != 0:
                    prestige_multiplier = 1 + (0.40 * prestige_level)
                    prestige_multiplierhp = 1 + (0.20 * prestige_level)

                    opponents = [
                        {"user": minion1_name, "hp": int(round(minion1_stats["hp"] * prestige_multiplierhp)),
                         "armor": int(round(minion1_stats["armor"] * prestige_multiplierhp)),
                         "damage": int(round(minion1_stats["damage"] * prestige_multiplier))},
                        {"user": minion2_name, "hp": int(round(minion2_stats["hp"] * prestige_multiplierhp)),
                         "armor": int(round(minion2_stats["armor"] * prestige_multiplierhp)),
                         "damage": int(round(minion2_stats["damage"] * prestige_multiplier))},
                        {"user": boss_name, "hp": int(round(boss_stats["hp"] * prestige_multiplierhp)),
                         "armor": int(round(boss_stats["armor"] * prestige_multiplierhp)),
                         "damage": int(round(boss_stats["damage"] * prestige_multiplier))},
                    ]

                else:
                    opponents = [
                        {"user": minion1_name, "hp": minion1_stats["hp"], "armor": minion1_stats["armor"],
                         "damage": minion1_stats["damage"]},
                        {"user": minion2_name, "hp": minion2_stats["hp"], "armor": minion2_stats["armor"],
                         "damage": minion2_stats["damage"]},
                        {"user": boss_name, "hp": boss_stats["hp"], "armor": boss_stats["armor"],
                         "damage": boss_stats["damage"]},
                    ]
            else:
                levelhp = rpgtools.xptolevel(ctx.character_data["xp"])
                async with self.bot.pool.acquire() as conn:
                    query = 'SELECT "health", "stathp" FROM profile WHERE "user" = $1;'
                    result = await conn.fetchrow(query, user_id)

                if result:
                    # Extract the health value from the result
                    base_health = 250
                    health = result['health'] + base_health
                    stathp = result['stathp'] * 50

                    # Calculate total health based on level and add to current health
                    total_health = health + (levelhp * 5)
                    total_health = total_health + stathp
                levelhp = level * 5
                newhp = ctx.character_data["health"] + 250 + levelhp
                async with self.bot.pool.acquire() as conn:
                    dmg, deff = await self.bot.get_raidstats(ctx.author, conn=conn)
                    m1dmg, m1deff = await self.bot.get_raidstats(random_user_object_1.id, conn=conn)
                    m2dmg, m2deff = await self.bot.get_raidstats(random_user_object_2.id, conn=conn)
                player = {"user": ctx.author, "hp": total_health, "armor": deff, "damage": dmg}

                opponents = [
                    {"user": random_user_object_1.display_name, "hp": 250, "armor": m1deff,
                     "damage": m1dmg},
                    {"user": random_user_object_2.display_name, "hp": 250, "armor": m2deff,
                     "damage": m2dmg},
                    {"user": boss_name, "hp": boss_stats["hp"], "armor": boss_stats["armor"],
                     "damage": boss_stats["damage"]},
                ]
            maxhp = player["hp"]

            victory_description = None
            import utils.random as random

            for opponent in opponents:
                defender = opponent
                staticdefender = defender["user"]
                battle_log = deque(
                    [
                        (
                            0,
                            _("Raidbattle {p1} vs. {p2} started!").format(
                                p1=player["user"], p2=defender["user"]
                            ),
                        )
                    ],
                    maxlen=3,
                )

                embed = discord.Embed(
                    description=battle_log[0][1], color=self.bot.config.game.primary_colour
                )

                log_message = await ctx.send(
                    embed=embed
                )
                await asyncio.sleep(4)

                # Initialize the attacker and defender

                random_number = random.randint(1, 3)


                if random_number <= 2:
                    attacker, defender = player, opponent
                else:
                    attacker, defender = opponent, player

                while player["hp"] > 0 and defender["hp"] > 0:
                    # Perform the attack with the current attacker
                    # Calculate damage
                    if attacker == player and author_mage_evolution is not None:
                        # Mage's Fireball ability with 40% chance
                        fireball_chance = random.randint(1, 100)
                        if fireball_chance <= 40:
                            # Fireball triggers
                            evolution_level = author_mage_evolution
                            damage_multiplier = evolution_damage_multiplier.get(evolution_level, 1.0)
                            damage = (
                                    (attacker["damage"] + Decimal(random.randint(0, 100)) - defender["armor"])
                                    * Decimal(damage_multiplier)
                            )
                            damage = 1 if damage <= 0 else damage  # Ensure no negative damage
                            defender["hp"] -= damage

                            # Initialize message
                            message = _("{attacker} casts Fireball! {defender} takes **{dmg}HP** damage.").format(
                                attacker=attacker["user"],
                                defender=defender["user"],
                                dmg=damage,
                            )
                        else:
                            # Regular attack
                            damage = (
                                    attacker["damage"] + Decimal(random.randint(0, 100)) - defender["armor"]
                            )
                            damage = 1 if damage <= 0 else damage  # Ensure no negative damage
                            defender["hp"] -= damage

                            # Initialize message
                            message = _("{attacker} attacks! {defender} takes **{dmg}HP** damage.").format(
                                attacker=attacker["user"],
                                defender=defender["user"],
                                dmg=damage,
                            )
                    else:
                        # Opponent attacks or player is not a Mage
                        damage = (
                                attacker["damage"] + Decimal(random.randint(0, 100)) - defender["armor"]
                        )
                        damage = 1 if damage <= 0 else damage  # Ensure no negative damage
                        defender["hp"] -= damage

                        # Initialize message
                        message = _("{attacker} attacks! {defender} takes **{dmg}HP** damage.").format(
                            attacker=attacker["user"],
                            defender=defender["user"],
                            dmg=damage,
                        )

                    # Now check if defender is defeated
                    if defender["hp"] <= 0:
                        # Handle cheat death if defender is the player
                        if defender["user"] == ctx.author:
                            chance = authorchance
                            random_number = random.randint(1, 100)
                            if not cheated:
                                if random_number <= chance:
                                    defender["hp"] = 75
                                    cheated = True
                                    message += _(" {defender} cheats death and survives with 75HP!").format(
                                        defender=defender["user"]
                                    )
                                else:
                                    defender["hp"] = 0
                                    message += _(" {defender} is defeated!").format(
                                        defender=defender["user"]
                                    )
                            else:
                                defender["hp"] = 0
                                message += _(" {defender} is defeated!").format(
                                    defender=defender["user"]
                                )
                        else:
                            defender["hp"] = 0
                            message += _(" {defender} is defeated!").format(
                                defender=defender["user"]
                            )
                    else:
                        # Handle lifesteal if applicable
                        if attacker == player:
                            if lifestealauth != 0:
                                lifesteal_percentage = Decimal(lifestealauth) / Decimal(100)
                                heal = lifesteal_percentage * Decimal(damage)
                                attacker["hp"] += heal.quantize(Decimal('0.00'), rounding=ROUND_HALF_UP)
                                if attacker["hp"] > maxhp:
                                    attacker["hp"] = maxhp
                                    maxedhp = True
                                message += _(" Lifesteals: **{heal}HP**").format(heal=heal)

                    # Append message to battle log
                    battle_log.append(
                        (
                            battle_log[-1][0] + 1,
                            message,
                        )
                    )
                    if attacker["user"] == ctx.author:
                        # Update the embed with the correct attacker and defender names and HP
                        embed = discord.Embed(
                            description=_("{p1} - {hp1} HP left\n{p2} - {hp2} HP left").format(
                                p1=attacker["user"],
                                hp1=attacker["hp"],
                                p2=defender["user"],
                                hp2=defender["hp"],
                            ),
                            color=self.bot.config.game.primary_colour,
                        )
                    else:
                        # Update the embed with the correct attacker and defender names and HP
                        embed = discord.Embed(
                            description=_("{p1} - {hp1} HP left\n{p2} - {hp2} HP left").format(
                                p1=defender["user"],
                                hp1=defender["hp"],
                                p2=attacker["user"],
                                hp2=attacker["hp"],
                            ),
                            color=self.bot.config.game.primary_colour,
                        )

                    for line in battle_log:
                        embed.add_field(
                            name=_("Action #{number}").format(number=line[0]), value=line[1]
                        )

                    await log_message.edit(embed=embed)
                    await asyncio.sleep(4)

                    # Check if defender is defeated and break the loop if so
                    if defender["hp"] <= 0:
                        break  # Move to the next opponent or end the battle

                    # Swap attacker and defender for the next turn
                    attacker, defender = defender, attacker

                await asyncio.sleep(2)  # Delay after defeating an opponent

                if player["hp"] <= 0:
                    victory_description = _(f"{ctx.author.mention} Better luck next time. You were defeated.")
                    await self.remove_player_from_fight(ctx.author.id)
                    break  # Exit the loop if the player loses
                else:
                    # victory_description = _("{opponent} has been defeated!").format(opponent=defender["user"])
                    await asyncio.sleep(2)  # Delay before the next battle

                # Check if there are more opponents
                if opponent != opponents[-1]:
                    await asyncio.sleep(2)  # Delay before the next battle

            # Rest of your code continues...

            if victory_description:
                await ctx.send(victory_description)
            else:

                level_names = [
                    "The Tower's Foyer",
                    "Shadowy Staircase",
                    "Chamber of Whispers",
                    "Serpent's Lair",
                    "Halls of Despair",
                    "Crimson Abyss",
                    "Forgotten Abyss",
                    "Dreadlord's Domain",
                    "Gates of Twilight",
                    "Twisted Reflections",
                    "Voidforged Sanctum",
                    "Nexus of Chaos",
                    "Eternal Torment Halls",
                    "Abyssal Desolation",
                    "Cursed Citadel",
                    "The Spire of Shadows",
                    "Tempest's Descent",
                    "Roost of Doombringers",
                    "The Endless Spiral",
                    "Malevolent Apex",
                    "Apocalypse's Abyss",
                    "Chaosborne Throne",
                    "Supreme Darkness",
                    "The Tower's Heart",
                    "The Ultimate Test",
                    "Realm of Annihilation",
                    "Lord of Despair",
                    "Abyssal Overlord",
                    "The End of All",
                    "The Final Confrontation"
                ]

                level_name = level_names[level - 1]

                if level == 1:
                    victory_embed = discord.Embed(
                        title="Victory!",
                        description=(
                            "As the dust settles, you stand victorious over the fallen minions and the defeated Abyssal Guardian, "
                            "its ominous form dissipating into the shadows. The floor is now free from its grasp, "
                            "and the path to treasure lies ahead."
                        ),
                        color=0x00ff00  # Green color for success
                    )
                    await ctx.send(embed=victory_embed)

                    # Create an embed for the treasure chest options
                    chest_embed = discord.Embed(
                        title="Choose Your Treasure",
                        description=(
                            "You have a choice to make: Before you lie two treasure chests, each shimmering with an otherworldly aura. "
                            "The left chest appears ancient and ornate, while the right chest is smaller but radiates a faint magical glow."
                            f"{ctx.author.mention}, Type `left` or `right` to make your decision. You have 2 minutes!"
                        ),
                        color=0x0055ff  # Blue color for options
                    )
                    chest_embed.set_footer(text=f"Type left or right to make your decision.")
                    await ctx.send(embed=chest_embed)

                    async with self.bot.pool.acquire() as connection:
                        prestige_level = await connection.fetchval('SELECT prestige FROM battletower WHERE id = $1',
                                                                   ctx.author.id)
                        level = await connection.fetchval('SELECT level FROM battletower WHERE id = $1', ctx.author.id)

                    def check(m):
                        return m.author == ctx.author and m.content.lower() in ['left', 'right']

                    import random
                    if prestige_level >= 1:
                        new_level = level + 1

                        async with self.bot.pool.acquire() as connection:
                            left_reward_type = random.choice(['crate', 'money'])
                            right_reward_type = random.choice(['crate', 'money'])

                            if left_reward_type == 'crate':
                                left_options = ['legendary', 'fortune', 'mystery', 'common', 'uncommon', 'magic',
                                                'rare']
                                left_weights = [1, 1, 80, 170, 150, 20, 75]  # Weighted values according to percentages
                                left_crate_type = random.choices(left_options, left_weights)[0]
                            else:
                                left_money_amount = random.choice(
                                    [10000, 15000, 20000, 50000, 25000, 10000, 27000, 33000, 100000, 5000, 150000])

                            if right_reward_type == 'crate':
                                right_options = ['legendary', 'fortune', 'mystery', 'common', 'uncommon', 'magic',
                                                 'rare']
                                right_weights = [1, 1, 80, 170, 150, 20, 75]  # Weighted values according to percentages
                                right_crate_type = random.choices(right_options, right_weights)[0]
                            else:
                                right_money_amount = random.choice(
                                    [10000, 15000, 20000, 50000, 25000, 10000, 27000, 33000, 100000, 5000, 150000])

                            await ctx.send(
                                "You see two chests: one on the left and one on the right. Which one do you choose? (Type 'left' or 'right')")

                            try:
                                msg = await self.bot.wait_for('message', check=check, timeout=60.0)
                            except asyncio.TimeoutError:
                                await ctx.send('You took too long to decide. The chests remain unopened.')
                                await ctx.send(f'You have advanced to floor: {new_level}')
                                await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                         ctx.author.id)
                                try:
                                    await self.remove_player_from_fight(ctx.author.id)
                                except Exception as e:
                                    pass
                                return

                            choice = msg.content.lower()

                            if choice == 'left':
                                if left_reward_type == 'crate':
                                    await ctx.send(
                                        f'You open the chest on the left and find a {emotes[left_crate_type]} crate!')
                                    await connection.execute(
                                        f'UPDATE profile SET crates_{left_crate_type} = crates_{left_crate_type} + 1 WHERE "user" = $1',
                                        ctx.author.id)
                                    unchosen_reward = right_reward_type
                                    if unchosen_reward == 'crate':
                                        await ctx.send(
                                            f'You could have gotten a {emotes[right_crate_type]} crate if you chose the right chest.')
                                    else:
                                        await ctx.send(
                                            f'You could have gotten **${right_money_amount}** if you chose the right chest.')
                                else:
                                    await ctx.send(f'You open the chest on the left and find **${left_money_amount}**!')
                                    await connection.execute('UPDATE profile SET money = money + $1 WHERE "user" = $2',
                                                             left_money_amount, ctx.author.id)
                                    unchosen_reward = right_reward_type
                                    if unchosen_reward == 'crate':
                                        await ctx.send(
                                            f'You could have gotten a {emotes[right_crate_type]} crate if you chose the right chest.')
                                    else:
                                        await ctx.send(
                                            f'You could have gotten **${right_money_amount}** if you chose the right chest.')
                            else:
                                if right_reward_type == 'crate':
                                    await ctx.send(
                                        f'You open the chest on the right and find a {emotes[right_crate_type]} crate!')
                                    await connection.execute(
                                        f'UPDATE profile SET crates_{right_crate_type} = crates_{right_crate_type} + 1 WHERE "user" = $1',
                                        ctx.author.id)
                                    unchosen_reward = left_reward_type
                                    if unchosen_reward == 'crate':
                                        await ctx.send(
                                            f'You could have gotten a {emotes[left_crate_type]} crate if you chose the left chest.')
                                    else:
                                        await ctx.send(
                                            f'You could have gotten **${left_money_amount}** if you chose the left chest.')
                                else:
                                    await ctx.send(
                                        f'You open the chest on the right and find **${right_money_amount}**!')
                                    await connection.execute('UPDATE profile SET money = money + $1 WHERE "user" = $2',
                                                             right_money_amount, ctx.author.id)
                                    unchosen_reward = left_reward_type
                                    if unchosen_reward == 'crate':
                                        await ctx.send(
                                            f'You could have gotten a {emotes[left_crate_type]} crate if you chose the left chest.')
                                    else:
                                        await ctx.send(
                                            f'You could have gotten **${left_money_amount}** if you chose the left chest.')

                            await ctx.send(f'You have advanced to floor: {new_level}')
                            await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                     ctx.author.id)
                            try:
                                await self.remove_player_from_fight(ctx.author.id)
                            except Exception as e:
                                pass

                            #--------------------------
                            # --------------------------
                            # --------------------------
                    else:
                        def check(m):
                            return m.author == ctx.author and m.content.lower() in ['left', 'right']

                        try:
                            msg = await self.bot.wait_for('message', check=check, timeout=120.0)
                        except asyncio.TimeoutError:
                            newlevel = level + 1
                            await ctx.send('You took too long to decide. The chests remain unopened.')
                            await ctx.send(f'You have advanced to floor: {newlevel}')
                            async with self.bot.pool.acquire() as connection:
                                await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                         ctx.author.id)
                                try:
                                    await self.remove_player_from_fight(ctx.author.id)
                                except Exception as e:
                                    pass
                                return
                        else:
                            newlevel = level + 1
                            if msg.content.lower() == 'left':
                                await ctx.send(
                                    'You open the chest on the left and find: <:F_rare:1139514880517484666> A '
                                    'rare Crate!')
                                await ctx.send(f'You have advanced to floor: {newlevel}')
                                async with self.bot.pool.acquire() as connection:
                                    await connection.execute(
                                        'UPDATE profile SET crates_rare = crates_rare + 1 WHERE "user" '
                                        '= $1', ctx.author.id)
                                    await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                             ctx.author.id)
                            else:
                                await ctx.send('You open the chest on the right and find: Nothing, bad luck!')
                                async with self.bot.pool.acquire() as connection:
                                    await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                             ctx.author.id)
                                await ctx.send(f'You have advanced to floor: {newlevel}')
                if level == 2:
                    victory_embed = discord.Embed(
                        title="Triumphant Conquest!",
                        description=(
                            "A deafening silence falls upon the Shadowy Staircase as the lifeless forms of Wraith and Soul Eater lay shattered at your feet. "
                            "The Vile Serpent writhes in agony, its malevolent presence vanquished by your unwavering determination."
                            "\n\nThe darkness recedes, unveiling a newfound path ahead, leading you deeper into the mysterious Battle Tower."
                        ),
                        color=0x00ff00  # Green color for success
                    )
                    await ctx.send(embed=victory_embed)

                    # Create an embed for the treasure chest options
                    newlevel = level + 1
                    async with self.bot.pool.acquire() as connection:
                        await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                 ctx.author.id)
                    await ctx.send(f'You have advanced to floor: {newlevel}')

                if level == 3:
                    victory_embed = discord.Embed(
                        title="Triumph Over Warlord Grakthar!",
                        description=(
                            "The war drums of the Chamber of Whispers have fallen silent, and the imposing Warlord Grakthar lies defeated. "
                            "His goblin and orc minions cower in fear as your indomitable spirit overcame their darkness."
                            "\n\nThe chamber, once filled with dread, now echoes with your resounding victory, and the path ahead beckons with unknown challenges."
                        ),
                        color=0x00ff00  # Green color for success
                    )

                    await ctx.send(embed=victory_embed)

                    newlevel = level + 1
                    async with self.bot.pool.acquire() as connection:
                        await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                 ctx.author.id)
                    await ctx.send(f'You have advanced to floor: {newlevel}')

                if level == 6:
                    victory_embed = discord.Embed(
                        title="Arachnok Queen Defeated!",
                        description=(
                            f"As you stand amidst the shattered webs and the defeated {minion1_name}s and {minion2_name}s, a tense silence envelops the Crimson Abyss. "
                            f"The Arachnok Queen, a monstrous ruler of arachnids, has been vanquished, her venomous web dismantled, and her reign of terror put to an end."
                            "\n\nAs you take a moment to catch your breath, you notice a peculiar artifact hidden within the queen's lair. This ancient relic begins to glow with an eerie light, and when you touch it, a vision unfolds before your eyes."
                            "\n\nIn the vision, you see the tower as it once was, a beacon of hope and valor. But it's gradually consumed by darkness, as an otherworldly entity known as the 'Eclipse Wraith' appears. This malevolent being hungers for the tower's immense power and begins to absorb the very light and life from within. In desperation, the tower's defenders created the artifacts, the only weapons capable of opposing the Eclipse Wraith's darkness."
                            "\n\nWith newfound purpose, you continue your ascent, knowing that you possess one of the artifacts, and the fate of the tower now rests in your hands."
                        ),
                        color=0x00ff00  # Green color for success
                    )

                    await ctx.send(embed=victory_embed)

                    newlevel = level + 1
                    async with self.bot.pool.acquire() as connection:
                        await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                 ctx.author.id)
                    await ctx.send(f'You have advanced to floor: {newlevel}')

                if level == 7:
                    victory_embed = discord.Embed(
                        title="Lich Lord Moros Defeated!",
                        description=(
                            f"As you stand amidst the vanquished {minion1_name}s and {minion2_name}s, an eerie stillness surrounds the {level_name}. "
                            f"The Lich Lord Moros, a master of spectral dominion, has been defeated, his ethereal reign shattered, and his dark enchantments dispelled."
                            "\n\nAs you explore the aftermath, another artifact reveals a vision to you. This time, you witness a group of brave souls, the 'Order of Radiance,' who were the last defenders of the tower. They reveal their intentions to harness the power of the artifacts and use them to push back the Eclipse Wraith. But their attempts were in vain, as the Eclipse Wraith's darkness overcame them, corrupting their very souls."
                            "\n\nYour journey takes on a deeper purpose as you learn of the Eclipse Wraith's corruption and its influence over the tower. The artifacts are your only hope to stand against this malevolence."
                        ),
                        color=0x00ff00  # Green color for success
                    )

                    await ctx.send(embed=victory_embed)

                    newlevel = level + 1
                    async with self.bot.pool.acquire() as connection:
                        await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                 ctx.author.id)
                    await ctx.send(f'You have advanced to floor: {newlevel}')

                if level == 8:
                    victory_embed = discord.Embed(
                        title="Frostfire Behemoth Defeated!",
                        description=(
                            f"As you stand amidst the defeated {minion1_name}s and {minion2_name}s, an oppressive heat fills the {level_name}. "
                            f"The Frostfire Behemoth, a master of fire and ice, has been vanquished, its elemental power extinguished, and its molten heart frozen."
                            "\n\nIn the scorching aftermath, you encounter an artifact that projects yet another vision. This time, you see the Eclipse Wraith's origin. It was once a powerful entity of light and balance, but it was corrupted by its insatiable thirst for power and dominion."
                            "\n\nYou realize that the Eclipse Wraith's corruption is tied to the artifacts themselves. The more you possess, the closer you come to facing the Eclipse Wraith. You continue your journey, determined to uncover the truth and put an end to the tower's darkness."
                        ),
                        color=0x00ff00  # Green color for success
                    )

                    await ctx.send(embed=victory_embed)

                    newlevel = level + 1
                    async with self.bot.pool.acquire() as connection:
                        await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                 ctx.author.id)
                    await ctx.send(f'You have advanced to floor: {newlevel}')

                if level == 9:
                    victory_embed = discord.Embed(
                        title="Dragonlord Zaldrak Defeated!",
                        description=(
                            f"As you stand amidst the frozen tundra and the defeated {minion1_name}s and {minion2_name}s, an icy stillness blankets the {level_name}, the Frozen Abyss. "
                            f"The Dragonlord Zaldrak, a master of frost and flame, has been vanquished, its frigid and fiery power quelled, and its dominion shattered."
                            "\n\nAmidst the frost, you come across an artifact with a chilling vision. It reveals that the Eclipse Wraith has already absorbed the power of the other artifacts and has grown stronger. It seeks to devour the entire world, and the only way to stop it is by wielding the combined power of the remaining artifacts."
                            "\n\nWith the artifacts in your possession, your journey becomes a race against time, as you are the last hope to prevent the Eclipse Wraith's catastrophic release."
                        ),
                        color=0x00ff00  # Green color for success
                    )

                    await ctx.send(embed=victory_embed)

                    newlevel = level + 1
                    async with self.bot.pool.acquire() as connection:
                        await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                 ctx.author.id)
                    await ctx.send(f'You have advanced to floor: {newlevel}')

                if level == 10:
                    victory_embed = discord.Embed(
                        title="Soulreaver Lurkthar Defeated!",
                        description=(
                            f"As you stand amidst the shattered spirits and the defeated {minion1_name}s and {minion2_name}s, an eerie sense of tranquility washes over the {level_name}, the Soulreaver's Embrace. "
                            f"Soulreaver Lurkthar, a formidable entity that consumed countless souls, has been vanquished, its malevolent grip on the spectral realm broken, and the souls it enslaved set free."
                            "\n\nYou take a moment to appreciate the artifact you acquired in the Crimson Abyss, as it once again glows with an ethereal light. This time, it offers a vision of the tower's guardians, including the bosses you have faced. They were once noble protectors of the tower, known as the 'Sentinels of Radiance.'"
                            "\n\nLong ago, the Sentinels guarded the tower against all threats, including the Eclipse Wraith. However, the power of the Eclipse Wraith corrupted them, turning them into the very foes they once fought against."
                            "\n\nThe artifact in your possession is not only a weapon but also a key to unlocking the potential within these fallen Sentinels. With it, you have the power to cleanse and restore them to their former glory. You realize that your journey is not just about defeating the Eclipse Wraith but also redeeming the defenders who lost their way."
                        ),
                        color=0x00ff00  # Green color for success
                    )

                    await ctx.send(embed=victory_embed)

                    chest_embed = discord.Embed(
                        title="Choose Your Treasure",
                        description=(
                            "You have a choice to make: Before you lie two treasure chests, each shimmering with an otherworldly aura. "
                            "The left chest appears ancient and ornate, while the right chest is smaller but radiates a faint magical glow."
                            f"{ctx.author.mention}, Type left or right to make your decision. You have 60 seconds!"
                        ),
                        color=0x0055ff  # Blue color for options
                    )
                    chest_embed.set_footer(text=f"Type left or right to make your decision.")
                    await ctx.send(embed=chest_embed)

                    async with self.bot.pool.acquire() as connection:
                        prestige_level = await connection.fetchval('SELECT prestige FROM battletower WHERE id = $1',
                                                                   ctx.author.id)
                        level = await connection.fetchval('SELECT level FROM battletower WHERE id = $1', ctx.author.id)

                    def check(m):
                        return m.author == ctx.author and m.content.lower() in ['left', 'right']

                    import random
                    if prestige_level >= 1:
                        new_level = level + 1

                        async with self.bot.pool.acquire() as connection:
                            left_reward_type = random.choice(['crate', 'money'])
                            right_reward_type = random.choice(['crate', 'money'])

                            if left_reward_type == 'crate':
                                left_options = ['legendary', 'fortune', 'mystery', 'common', 'uncommon', 'magic',
                                                'rare']
                                left_weights = [1, 1, 80, 170, 150, 20, 75]  # Weighted values according to percentages
                                left_crate_type = random.choices(left_options, left_weights)[0]
                            else:
                                left_money_amount = random.choice(
                                    [10000, 15000, 20000, 50000, 25000, 10000, 27000, 33000, 100000, 5000, 150000])

                            if right_reward_type == 'crate':
                                right_options = ['legendary', 'fortune', 'mystery', 'common', 'uncommon', 'magic',
                                                 'rare']
                                right_weights = [1, 1, 80, 170, 150, 20, 75]  # Weighted values according to percentages
                                right_crate_type = random.choices(right_options, right_weights)[0]
                            else:
                                right_money_amount = random.choice(
                                    [10000, 15000, 20000, 50000, 25000, 10000, 27000, 33000, 100000, 5000, 150000])

                            await ctx.send(
                                "You see two chests: one on the left and one on the right. Which one do you choose? (Type 'left' or 'right')")

                            try:
                                msg = await self.bot.wait_for('message', check=check, timeout=60.0)
                            except asyncio.TimeoutError:
                                await ctx.send('You took too long to decide. The chests remain unopened.')
                                await ctx.send(f'You have advanced to floor: {new_level}')
                                await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                         ctx.author.id)
                                try:
                                    await self.remove_player_from_fight(ctx.author.id)
                                except Exception as e:
                                    pass
                                return

                            choice = msg.content.lower()

                            if choice == 'left':
                                if left_reward_type == 'crate':
                                    await ctx.send(
                                        f'You open the chest on the left and find a {emotes[left_crate_type]} crate!')
                                    await connection.execute(
                                        f'UPDATE profile SET crates_{left_crate_type} = crates_{left_crate_type} + 1 WHERE "user" = $1',
                                        ctx.author.id)
                                    unchosen_reward = right_reward_type
                                    if unchosen_reward == 'crate':
                                        await ctx.send(
                                            f'You could have gotten a {emotes[right_crate_type]} crate if you chose the right chest.')
                                    else:
                                        await ctx.send(
                                            f'You could have gotten **${right_money_amount}** if you chose the right chest.')
                                else:
                                    await ctx.send(f'You open the chest on the left and find **${left_money_amount}**!')
                                    await connection.execute('UPDATE profile SET money = money + $1 WHERE "user" = $2',
                                                             left_money_amount, ctx.author.id)
                                    unchosen_reward = right_reward_type
                                    if unchosen_reward == 'crate':
                                        await ctx.send(
                                            f'You could have gotten a {emotes[right_crate_type]} crate if you chose the right chest.')
                                    else:
                                        await ctx.send(
                                            f'You could have gotten **${right_money_amount}** if you chose the right chest.')
                            else:
                                if right_reward_type == 'crate':
                                    await ctx.send(
                                        f'You open the chest on the right and find a {emotes[right_crate_type]} crate!')
                                    await connection.execute(
                                        f'UPDATE profile SET crates_{right_crate_type} = crates_{right_crate_type} + 1 WHERE "user" = $1',
                                        ctx.author.id)
                                    unchosen_reward = left_reward_type
                                    if unchosen_reward == 'crate':
                                        await ctx.send(
                                            f'You could have gotten a {emotes[left_crate_type]} crate if you chose the left chest.')
                                    else:
                                        await ctx.send(
                                            f'You could have gotten **${left_money_amount}** if you chose the left chest.')
                                else:
                                    await ctx.send(
                                        f'You open the chest on the right and find **${right_money_amount}**!')
                                    await connection.execute('UPDATE profile SET money = money + $1 WHERE "user" = $2',
                                                             right_money_amount, ctx.author.id)
                                    unchosen_reward = left_reward_type
                                    if unchosen_reward == 'crate':
                                        await ctx.send(
                                            f'You could have gotten a {emotes[left_crate_type]} crate if you chose the left chest.')
                                    else:
                                        await ctx.send(
                                            f'You could have gotten **${left_money_amount}** if you chose the left chest.')

                            await ctx.send(f'You have advanced to floor: {new_level}')
                            await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                     ctx.author.id)
                            try:
                                await self.remove_player_from_fight(ctx.author.id)
                            except Exception as e:
                                pass

                            # --------------------------
                            # --------------------------
                            # --------------------------

                    else:
                        try:
                            msg = await self.bot.wait_for('message', check=check, timeout=60.0)
                        except asyncio.TimeoutError:
                            new_level = level + 1
                            await ctx.send('You took too long to decide. The chests remain unopened.')
                            await ctx.send(f'You have advanced to floor: {new_level}')
                            async with self.bot.pool.acquire() as connection:
                                await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                         ctx.author.id)
                                try:
                                    await self.remove_player_from_fight(ctx.author.id)
                                except Exception as e:
                                    pass
                                return
                        else:

                            new_level = level + 1
                            if msg.content.lower() == 'left':
                                await ctx.send(
                                    'You open the chest on the left and find: <:F_Magic:1139514865174720532> 2 '
                                    'Magic Crates!')
                                await ctx.send(f'You have advanced to floor: {new_level}')
                                async with self.bot.pool.acquire() as connection:
                                    await connection.execute(
                                        'UPDATE profile SET crates_magic = crates_magic + 2 WHERE "user" '
                                        '= $1', ctx.author.id)
                                    await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                             ctx.author.id)
                            else:
                                await ctx.send('You open the chest on the right and find: **$55000**!')
                                await ctx.send(f'You have advanced to floor: {new_level}')
                                async with self.bot.pool.acquire() as connection:
                                    await connection.execute(
                                        'UPDATE profile SET money = money + 55000 WHERE "user" '
                                        '= $1', ctx.author.id)
                                    await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                             ctx.author.id)

                # ---------------------------------------------------------------------------------------------------------------------
                if level == 11:
                    victory_embed = discord.Embed(
                        title="Ravengaze Alpha Defeated!",
                        description=(
                            f"As you stand amidst the fallen Gnoll Raiders and defeated Hyena Packs, the {level_name}, the Voidforged Sanctum, echoes with an eerie silence. "
                            f"Ravengaze Alpha, a once-proud leader of the hyena tribe, has been vanquished, and the dark aura surrounding them has lifted."
                            "\n\nThe artifact from the Soulreaver's Embrace pulses with newfound energy. It reveals another vision, one of a grand council chamber within the tower. Here, the Guardians of Radiance, the Sentinels of Light, forged a pact with the Eclipse Wraith to protect the tower against a greater, hidden threat."
                            "\n\nThe vision hints that the tower's fall into darkness was a last resort to prevent this hidden power from being unleashed. Your journey is now a quest to unveil this hidden threat and restore the tower to its original purpose."
                            "\n\nBut the vision holds a revelation - one of the Guardians, who stood as a beacon of light, is revealed to have orchestrated the Eclipse Wraith's corruption, becoming its greatest ally and adversary."
                        ),
                        color=0x00ff00  # Green color for success
                    )

                    await ctx.send(embed=victory_embed)

                    newlevel = level + 1
                    async with self.bot.pool.acquire() as connection:
                        await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                 ctx.author.id)
                    await ctx.send(f'You have advanced to floor: {newlevel}')

                if level == 12:
                    victory_embed = discord.Embed(
                        title="Nightshade Serpentis Defeated!",
                        description=(
                            f"As you stand amidst the fallen Gloomhounds and defeated Nocturne Stalkers, the {level_name}, the Nexus of Chaos, resonates with a sense of restored equilibrium. "
                            f"Nightshade Serpentis, once a guardian of the tower, has been vanquished, and the arcane chaos that enveloped the floor dissipates."
                            "\n\nThe artifact in your possession once again shines with brilliance, revealing another vision. This vision takes you to a library within the tower, where the Guardians of Radiance researched the tower's history and its ancient purpose."
                            "\n\nYou learn that the Eclipse Wraith's curse was the result of a great betrayal by one of the Guardians, who sought to harness the tower's power for their own gain. The Eclipse Wraith was summoned as a protector, but the dark force turned against its summoners."
                            "\n\nYour journey now encompasses a quest for knowledge as you seek to understand the tower's true history and the identity of the betrayer who initiated its fall. A plot to harness ultimate power is unveiled."
                        ),
                        color=0x00ff00  # Green color for success
                    )

                    await ctx.send(embed=victory_embed)

                    newlevel = level + 1
                    async with self.bot.pool.acquire() as connection:
                        await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                 ctx.author.id)
                    await ctx.send(f'You have advanced to floor: {newlevel}')

                if level == 16:
                    victory_embed = discord.Embed(
                        title="Master Puppeteer Defeated!",
                        description=(
                            f"As the Master Puppeteer falls amidst the wreckage of marionettes and severed strings, the {level_name}, the Manipulated Marionette Chamber, echoes with an eerie silence. The artifact in your possession, known for revealing visions, suddenly projects ancient symbols onto the chamber's walls."
                            "\n\nThese symbols tell the tale of an ancient weapon, the Tower, designed by a civilization known as the Forerunners. The tower's purpose was to stop a cosmic malevolence threatening galaxies."
                            "\n\nHowever, a startling revelation unfolds as the artifact translates these ancient inscriptions. It becomes evident that the tower itself, now controlled by a malevolent force, is the very threat the Forerunners built it to stop—an ominous power seeking to wreak havoc on cosmic scales."
                            "\n\nAs you delve deeper into the translated inscriptions, a fragmented history emerges. The malevolent force corrupted the tower, turning it against its intended purpose. It manipulated events through the Master Puppeteer to ensure chaos would reign, setting the stage for an imminent cosmic cataclysm."
                            "\n\nThe artifact, once deemed a mere visionary device, now pulses with untapped potential—a cosmic weapon capable of restoring the tower to its intended purpose or, if wielded incorrectly, unleashing a catastrophic cosmic upheaval."
                            "\n\nWith this newfound understanding, you brace yourself for the ultimate confrontation against the malevolent force controlling the tower—a showdown not just to liberate the tower but to prevent a cosmic disaster that threatens to engulf entire galaxies."
                            "\n\nArmed with the artifact's augmented power, you step forth, knowing that the fate of the cosmos hangs in the balance, and the final battle to reclaim the tower's purpose is the first step in averting a catastrophe of unprecedented proportions."
                        ),
                        color=0x00ff00  # Green color for success
                    )

                    await ctx.send(embed=victory_embed)

                    newlevel = level + 1
                    async with self.bot.pool.acquire() as connection:
                        await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                 ctx.author.id)
                    await ctx.send(f'You have advanced to floor: {newlevel}')

                if level == 13:
                    victory_embed = discord.Embed(
                        title="Ignis Inferno Defeated!",
                        description=(
                            f"As you stand amidst the vanquished Magma Elementals and Inferno Imps, the {level_name}, the Eternal Torment Halls, ceases to tremble with searing heat. "
                            f"Ignis Inferno, a blazing entity with an unquenchable fire, has been extinguished, and the fires that consumed the floor subside."
                            "\n\nThe artifact shines with a fiery brilliance, revealing yet another vision. This time, it transports you to the heart of the tower's inner sanctum, where the ultimate secret is unveiled - the hidden threat is not an external force but a malevolent consciousness within the tower itself."
                            "\n\nThe Eclipse Wraith, now recognized as the Tower's Heart, was designed to contain and counterbalance this malevolent consciousness. Its transformation into darkness was intentional, and it's not the tower's adversary, but its guardian."
                            "\n\nYour journey has now reached its apex. You must confront the malevolent consciousness within the Tower's Heart to either save or seal the tower's fate. A shocking twist that challenges everything you knew about the tower's history."
                        ),
                        color=0x00ff00  # Green color for success
                    )

                    await ctx.send(embed=victory_embed)

                    newlevel = level + 1
                    async with self.bot.pool.acquire() as connection:
                        await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                 ctx.author.id)
                    await ctx.send(f'You have advanced to floor: {newlevel}')

                if level == 14:
                    victory_embed = discord.Embed(
                        title="Wraithlord Maroth Defeated!",
                        description=(
                            f"As you stand amidst the defeated Cursed Banshees and Spectral Harbingers, the {level_name}, the Abyssal Desolation, resonates with a newfound stillness. "
                            f"Wraithlord Maroth, a sinister figure with dominion over lost souls, has been vanquished, and the lingering wails of the desolation fade."
                            "\n\nYour artifact gleams, offering a vision of a council meeting among the Guardians of Radiance. Here, the decision to summon the Eclipse Wraith was made, a desperate act to combat the hidden threat that endangered the tower."
                            "\n\nHowever, this vision reveals a shocking truth - the Eclipse Wraith's dark transformation was not due to the betrayal of a Guardian, but it was always intended to be a guardian of darkness, a necessary counterbalance to the hidden threat."
                            "\n\nYour journey now becomes a quest to understand the true purpose of the Eclipse Wraith and confront the hidden threat head-on. A twist in the narrative reveals the Eclipse Wraith as a guardian of balance."
                        ),
                        color=0x00ff00  # Green color for success
                    )

                    await ctx.send(embed=victory_embed)

                    newlevel = level + 1
                    async with self.bot.pool.acquire() as connection:
                        await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                 ctx.author.id)
                    await ctx.send(f'You have advanced to floor: {newlevel}')

                if level == 15:
                    victory_embed = discord.Embed(
                        title="Infernus, the Infernal Defeated!",
                        description=(
                            f"As you stand amidst the defeated Demonic Imps and Hellspawn Reavers, the {level_name}, the Cursed Citadel, feels almost solemn. "
                            f"Infernus, the Infernal, a creature of elemental destruction, has been vanquished, and the citadel's flames subside."
                            "\n\nYour artifact radiates with power and offers a vision. This vision transports you to a chamber deep within the tower, where the ultimate secret is unveiled - the hidden threat is not an external force but a malevolent consciousness within the tower itself."
                            "\n\nThe Eclipse Wraith, now recognized as the Tower's Heart, was designed to contain and counterbalance this malevolent consciousness. Its transformation into darkness was intentional, and it's not the tower's adversary, but its guardian."
                            "\n\nYour journey has now reached its apex. You must confront the malevolent consciousness within the Tower's Heart to either save or seal the tower's fate."
                            "\n\nHowever, the vision also reveals that the remaining Guardians of Radiance are imprisoned within the tower, their power siphoned to sustain the malevolent consciousness. A plot twist that sets the stage for your most challenging battle."
                        ),
                        color=0x00ff00  # Green color for success
                    )

                    await ctx.send(embed=victory_embed)

                    chest_embed = discord.Embed(
                        title="Choose Your Treasure",
                        description=(
                            "You have a choice to make: Before you lie two treasure chests, each shimmering with an otherworldly aura. "
                            "The left chest appears ancient and ornate, while the right chest is smaller but radiates a faint magical glow."
                            f"{ctx.author.mention}, Type left or right to make your decision. You have 60 seconds!"
                        ),
                        color=0x0055ff  # Blue color for options
                    )
                    chest_embed.set_footer(text=f"Type left or right to make your decision.")
                    await ctx.send(embed=chest_embed)
                    legran = random.randint(1, 2)

                    async with self.bot.pool.acquire() as connection:
                        prestige_level = await connection.fetchval('SELECT prestige FROM battletower WHERE id = $1',
                                                                   ctx.author.id)
                        level = await connection.fetchval('SELECT level FROM battletower WHERE id = $1', ctx.author.id)

                    def check(m):
                        return m.author == ctx.author and m.content.lower() in ['left', 'right']

                    import random
                    if prestige_level >= 1:
                        new_level = level + 1

                        async with self.bot.pool.acquire() as connection:
                            left_reward_type = random.choice(['crate', 'money'])
                            right_reward_type = random.choice(['crate', 'money'])

                            if left_reward_type == 'crate':
                                left_options = ['legendary', 'fortune', 'mystery', 'common', 'uncommon', 'magic',
                                                'rare']
                                left_weights = [1, 1, 80, 170, 150, 20, 75]  # Weighted values according to percentages
                                left_crate_type = random.choices(left_options, left_weights)[0]
                            else:
                                left_money_amount = random.choice(
                                    [10000, 15000, 20000, 50000, 25000, 10000, 27000, 33000, 100000, 5000, 150000])

                            if right_reward_type == 'crate':
                                right_options = ['legendary', 'fortune', 'mystery', 'common', 'uncommon', 'magic',
                                                 'rare']
                                right_weights = [1, 1, 80, 170, 150, 20, 75]  # Weighted values according to percentages
                                right_crate_type = random.choices(right_options, right_weights)[0]
                            else:
                                right_money_amount = random.choice(
                                    [10000, 15000, 20000, 50000, 25000, 10000, 27000, 33000, 100000, 5000, 150000])

                            await ctx.send(
                                "You see two chests: one on the left and one on the right. Which one do you choose? (Type 'left' or 'right')")

                            try:
                                msg = await self.bot.wait_for('message', check=check, timeout=60.0)
                            except asyncio.TimeoutError:
                                await ctx.send('You took too long to decide. The chests remain unopened.')
                                await ctx.send(f'You have advanced to floor: {new_level}')
                                await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                         ctx.author.id)
                                try:
                                    await self.remove_player_from_fight(ctx.author.id)
                                except Exception as e:
                                    pass
                                return

                            choice = msg.content.lower()

                            if choice == 'left':
                                if left_reward_type == 'crate':
                                    await ctx.send(
                                        f'You open the chest on the left and find a {emotes[left_crate_type]} crate!')
                                    await connection.execute(
                                        f'UPDATE profile SET crates_{left_crate_type} = crates_{left_crate_type} + 1 WHERE "user" = $1',
                                        ctx.author.id)
                                    unchosen_reward = right_reward_type
                                    if unchosen_reward == 'crate':
                                        await ctx.send(
                                            f'You could have gotten a {emotes[right_crate_type]} crate if you chose the right chest.')
                                    else:
                                        await ctx.send(
                                            f'You could have gotten **${right_money_amount}** if you chose the right chest.')
                                else:
                                    await ctx.send(f'You open the chest on the left and find **${left_money_amount}**!')
                                    await connection.execute('UPDATE profile SET money = money + $1 WHERE "user" = $2',
                                                             left_money_amount, ctx.author.id)
                                    unchosen_reward = right_reward_type
                                    if unchosen_reward == 'crate':
                                        await ctx.send(
                                            f'You could have gotten a {emotes[right_crate_type]} crate if you chose the right chest.')
                                    else:
                                        await ctx.send(
                                            f'You could have gotten **${right_money_amount}** if you chose the right chest.')
                            else:
                                if right_reward_type == 'crate':
                                    await ctx.send(
                                        f'You open the chest on the right and find a {emotes[right_crate_type]} crate!')
                                    await connection.execute(
                                        f'UPDATE profile SET crates_{right_crate_type} = crates_{right_crate_type} + 1 WHERE "user" = $1',
                                        ctx.author.id)
                                    unchosen_reward = left_reward_type
                                    if unchosen_reward == 'crate':
                                        await ctx.send(
                                            f'You could have gotten a {emotes[left_crate_type]} crate if you chose the left chest.')
                                    else:
                                        await ctx.send(
                                            f'You could have gotten **${left_money_amount}** if you chose the left chest.')
                                else:
                                    await ctx.send(
                                        f'You open the chest on the right and find **${right_money_amount}**!')
                                    await connection.execute('UPDATE profile SET money = money + $1 WHERE "user" = $2',
                                                             right_money_amount, ctx.author.id)
                                    unchosen_reward = left_reward_type
                                    if unchosen_reward == 'crate':
                                        await ctx.send(
                                            f'You could have gotten a {emotes[left_crate_type]} crate if you chose the left chest.')
                                    else:
                                        await ctx.send(
                                            f'You could have gotten **${left_money_amount}** if you chose the left chest.')

                            await ctx.send(f'You have advanced to floor: {new_level}')
                            await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                     ctx.author.id)
                            try:
                                await self.remove_player_from_fight(ctx.author.id)
                            except Exception as e:
                                pass

                            # --------------------------
                            # --------------------------
                            # --------------------------

                    else:

                        try:
                            msg = await self.bot.wait_for('message', check=check, timeout=60.0)
                        except asyncio.TimeoutError:
                            newlevel = level + 1
                            await ctx.send('You took too long to decide. The chests remain unopened.')
                            await ctx.send(f'You have advanced to floor: {newlevel}')
                            async with self.bot.pool.acquire() as connection:
                                await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                         ctx.author.id)
                                try:
                                    await self.remove_player_from_fight(ctx.author.id)
                                except Exception as e:
                                    pass
                                return
                        else:
                            newlevel = level + 1
                            if msg.content.lower() == 'left':
                                if legran == 1:
                                    await ctx.send('You open the chest on the left and find: Nothing, bad luck!')
                                    await ctx.send(f'You have advanced to floor: {newlevel}')
                                    async with self.bot.pool.acquire() as connection:
                                        await connection.execute(
                                            'UPDATE battletower SET level = level + 1 WHERE id = $1',
                                            ctx.author.id)
                                else:

                                    await ctx.send(
                                        'You open the chest on the right and find: <:F_Legendary:1139514868400132116> A Legendary Crate!')
                                    await ctx.send(f'You have advanced to floor: {newlevel}')
                                    async with self.bot.pool.acquire() as connection:
                                        await connection.execute(
                                            'UPDATE profile SET crates_legendary = crates_legendary + 1 WHERE "user" '
                                            '= $1', ctx.author.id)
                                        await connection.execute(
                                            'UPDATE battletower SET level = level + 1 WHERE id = $1',
                                            ctx.author.id)
                            else:

                                if legran == 2:
                                    await ctx.send('You open the chest on the left and find: Nothing, bad luck!')
                                    await ctx.send(f'You have advanced to floor: {newlevel}')
                                    async with self.bot.pool.acquire() as connection:
                                        await connection.execute(
                                            'UPDATE battletower SET level = level + 1 WHERE id = $1',
                                            ctx.author.id)
                                else:

                                    await ctx.send(
                                        'You open the chest on the right and find: <:F_Legendary:1139514868400132116> A Legendary Crate!')
                                    await ctx.send(f'You have advanced to floor: {newlevel}')
                                    async with self.bot.pool.acquire() as connection:
                                        await connection.execute(
                                            'UPDATE profile SET crates_legendary = crates_legendary + 1 WHERE "user" '
                                            '= $1', ctx.author.id)
                                        await connection.execute(
                                            'UPDATE battletower SET level = level + 1 WHERE id = $1',
                                            ctx.author.id)

                if level == 19:
                    # Spectral Overlord's Last Stand
                    victory_embed = discord.Embed(
                        title="Spectral Overlord Defeated!",
                        description=(
                            "The Ethereal Nexus trembles as the Spectral Overlord falls, its dominion shattered. Phantom Wraiths dissipate, and the once-formidable Overlord crumbles."
                            "\n\nAmidst the cosmic aftermath, the tower itself seems to whisper secrets, revealing the echoes of the Forerunners' desperation and the Guardians' self-sacrifice."
                            "\n\nA surge of cosmic energy propels you to Level 20, a realm shrouded in mysteries yet to unravel."
                        ),
                        color=0x00ff00  # Green color for success
                    )

                    await ctx.send(embed=victory_embed)

                    newlevel = level + 1
                    async with self.bot.pool.acquire() as connection:
                        await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                 ctx.author.id)
                    await ctx.send(f'You have advanced to floor: {newlevel}')

                if level == 20:
                    # Frostbite, the Ice Tyrant's Frigid Domain
                    victory_embed = discord.Embed(
                        title="Frostbite, the Ice Tyrant Defeated!",
                        description=(
                            "The Glacial Bastion witnesses an epic clash, Frozen Horrors crumbling under your relentless onslaught. Frostbite, the Ice Tyrant, bows before your might, and the frozen heart thaws into oblivion."
                            "\n\nVisions unfurl, unveiling an ancient alliance—a cosmic dance disrupted by betrayal. Your journey intertwines with remnants of the cosmic alliance, and the Ice Tyrant's remains hold untapped powers that could tip the cosmic balance."
                            "\n\nLevel 21 beckons, promising revelations that transcend mere artifacts."
                        ),
                        color=0x00ff00  # Green color for success
                    )

                    await ctx.send(embed=victory_embed)

                    chest_embed = discord.Embed(
                        title="Choose Your Treasure",
                        description=(
                            "You have a choice to make: Before you lie two treasure chests, each shimmering with an otherworldly aura. "
                            "The left chest appears ancient and ornate, while the right chest is smaller but radiates a faint magical glow."
                            f"{ctx.author.mention}, Type left or right to make your decision. You have 60 seconds!"
                        ),
                        color=0x0055ff  # Blue color for options
                    )
                    chest_embed.set_footer(text=f"Type left or right to make your decision.")
                    await ctx.send(embed=chest_embed)

                    async with self.bot.pool.acquire() as connection:
                        prestige_level = await connection.fetchval('SELECT prestige FROM battletower WHERE id = $1',
                                                                   ctx.author.id)
                        level = await connection.fetchval('SELECT level FROM battletower WHERE id = $1', ctx.author.id)

                    def check(m):
                        return m.author == ctx.author and m.content.lower() in ['left', 'right']

                    import random
                    if prestige_level >= 1:
                        new_level = level + 1

                        async with self.bot.pool.acquire() as connection:
                            left_reward_type = random.choice(['crate', 'money'])
                            right_reward_type = random.choice(['crate', 'money'])

                            if left_reward_type == 'crate':
                                left_options = ['legendary', 'fortune', 'mystery', 'common', 'uncommon', 'magic',
                                                'rare']
                                left_weights = [1, 1, 80, 170, 150, 20, 75]  # Weighted values according to percentages
                                left_crate_type = random.choices(left_options, left_weights)[0]
                            else:
                                left_money_amount = random.choice(
                                    [10000, 15000, 20000, 50000, 25000, 10000, 27000, 33000, 100000, 5000, 150000])

                            if right_reward_type == 'crate':
                                right_options = ['legendary', 'fortune', 'mystery', 'common', 'uncommon', 'magic',
                                                 'rare']
                                right_weights = [1, 1, 80, 170, 150, 20, 75]  # Weighted values according to percentages
                                right_crate_type = random.choices(right_options, right_weights)[0]
                            else:
                                right_money_amount = random.choice(
                                    [10000, 15000, 20000, 50000, 25000, 10000, 27000, 33000, 100000, 5000, 150000])

                            await ctx.send(
                                "You see two chests: one on the left and one on the right. Which one do you choose? (Type 'left' or 'right')")

                            try:
                                msg = await self.bot.wait_for('message', check=check, timeout=60.0)
                            except asyncio.TimeoutError:
                                await ctx.send('You took too long to decide. The chests remain unopened.')
                                await ctx.send(f'You have advanced to floor: {new_level}')
                                await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                         ctx.author.id)
                                try:
                                    await self.remove_player_from_fight(ctx.author.id)
                                except Exception as e:
                                    pass
                                return

                            choice = msg.content.lower()

                            if choice == 'left':
                                if left_reward_type == 'crate':
                                    await ctx.send(
                                        f'You open the chest on the left and find a {emotes[left_crate_type]} crate!')
                                    await connection.execute(
                                        f'UPDATE profile SET crates_{left_crate_type} = crates_{left_crate_type} + 1 WHERE "user" = $1',
                                        ctx.author.id)
                                    unchosen_reward = right_reward_type
                                    if unchosen_reward == 'crate':
                                        await ctx.send(
                                            f'You could have gotten a {emotes[right_crate_type]} crate if you chose the right chest.')
                                    else:
                                        await ctx.send(
                                            f'You could have gotten **${right_money_amount}** if you chose the right chest.')
                                else:
                                    await ctx.send(f'You open the chest on the left and find **${left_money_amount}**!')
                                    await connection.execute('UPDATE profile SET money = money + $1 WHERE "user" = $2',
                                                             left_money_amount, ctx.author.id)
                                    unchosen_reward = right_reward_type
                                    if unchosen_reward == 'crate':
                                        await ctx.send(
                                            f'You could have gotten a {emotes[right_crate_type]} crate if you chose the right chest.')
                                    else:
                                        await ctx.send(
                                            f'You could have gotten **${right_money_amount}** if you chose the right chest.')
                            else:
                                if right_reward_type == 'crate':
                                    await ctx.send(
                                        f'You open the chest on the right and find a {emotes[right_crate_type]} crate!')
                                    await connection.execute(
                                        f'UPDATE profile SET crates_{right_crate_type} = crates_{right_crate_type} + 1 WHERE "user" = $1',
                                        ctx.author.id)
                                    unchosen_reward = left_reward_type
                                    if unchosen_reward == 'crate':
                                        await ctx.send(
                                            f'You could have gotten a {emotes[left_crate_type]} crate if you chose the left chest.')
                                    else:
                                        await ctx.send(
                                            f'You could have gotten **${left_money_amount}** if you chose the left chest.')
                                else:
                                    await ctx.send(
                                        f'You open the chest on the right and find **${right_money_amount}**!')
                                    await connection.execute('UPDATE profile SET money = money + $1 WHERE "user" = $2',
                                                             right_money_amount, ctx.author.id)
                                    unchosen_reward = left_reward_type
                                    if unchosen_reward == 'crate':
                                        await ctx.send(
                                            f'You could have gotten a {emotes[left_crate_type]} crate if you chose the left chest.')
                                    else:
                                        await ctx.send(
                                            f'You could have gotten **${left_money_amount}** if you chose the left chest.')

                            await ctx.send(f'You have advanced to floor: {new_level}')
                            await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                     ctx.author.id)
                            try:
                                await self.remove_player_from_fight(ctx.author.id)
                            except Exception as e:
                                pass

                            # --------------------------
                            # --------------------------
                            # --------------------------

                    else:
                        try:
                            msg = await self.bot.wait_for('message', check=check, timeout=60.0)
                        except asyncio.TimeoutError:
                            newlevel = level + 1
                            await ctx.send('You took too long to decide. The chests remain unopened.')
                            await ctx.send(f'You have advanced to floor: {newlevel}')
                            async with self.bot.pool.acquire() as connection:
                                await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                         ctx.author.id)
                                try:
                                    await self.remove_player_from_fight(ctx.author.id)
                                except Exception as e:
                                    pass
                                return
                        else:
                            newlevel = level + 1
                            if msg.content.lower() == 'left':
                                await ctx.send(
                                    'You open the chest on the left and find: <:F_Magic:1139514865174720532> 2 '
                                    'Magic Crates!')
                                await ctx.send(f'You have advanced to floor: {newlevel}')
                                async with self.bot.pool.acquire() as connection:
                                    await connection.execute(
                                        'UPDATE profile SET crates_magic = crates_magic + 2 WHERE "user" '
                                        '= $1', ctx.author.id)
                                    await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                             ctx.author.id)
                            else:
                                await ctx.send('You open the chest on the right and find: **$120000**!')
                                await ctx.send(f'You have advanced to floor: {newlevel}')
                                async with self.bot.pool.acquire() as connection:
                                    await connection.execute(
                                        'UPDATE profile SET money = money + 120000 WHERE "user" '
                                        '= $1', ctx.author.id)
                                    await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                             ctx.author.id)

                if level == 21:
                    # Chromaggus the Flamebrand's Roaring Inferno
                    victory_embed = discord.Embed(
                        title="Chromaggus the Flamebrand Defeated!",
                        description=(
                            "Ember Spire roars with the clash against Dragonkin and Chromatic Wyrms. Chromaggus the Flamebrand succumbs to your relentless assault, its fiery essence extinguished into cosmic embers."
                            "\n\nThe tower itself, now a sentient force, reveals a prophecy—a chosen one, a celestial dance between light and shadow, and the impending cosmic upheaval."
                            "\n\nYour journey faces its ultimate trial, entwined with the fate of the cosmic alliance and the tower's redemption. The essence of defeated bosses pulsates within you, presenting a cosmic choice—restore balance or unleash a cataclysmic force."
                            "\n\nStanding on the precipice of destiny, you prepare for the final confrontation that will determine the fate of the tower, the Eclipse Wraith, and the entire cosmos."
                        ),
                        color=0x00ff00  # Green color for success
                    )

                    await ctx.send(embed=victory_embed)

                    newlevel = level + 1
                    async with self.bot.pool.acquire() as connection:
                        await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                 ctx.author.id)
                    await ctx.send(f'You have advanced to floor: {newlevel}')

                if level == 22:
                    # Banshee Queen Shriekara's Lament
                    victory_embed = discord.Embed(
                        title="Banshee Queen Shriekara Defeated!",
                        description=(
                            "The Hallowed Mausoleum echoes with the wails of Phantom Banshees and Wailing Apparitions as you triumph over the Banshee Queen Shriekara. The spectral queen dissolves into cosmic echoes, and the tower itself seems to mourn."
                            "\n\nAs the ethereal remnants of the defeated queen coalesce, the tower shares cryptic visions—an ancient pact, a melody of sorrow, and a revelation that transcends the boundaries of life and death."
                            "\n\nLevel 23 beckons, promising a dance with the void and revelations that resonate with the cosmic harmony."
                        ),
                        color=0x00ff00  # Green color for success
                    )

                    await ctx.send(embed=victory_embed)

                    newlevel = level + 1
                    async with self.bot.pool.acquire() as connection:
                        await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                 ctx.author.id)
                    await ctx.send(f'You have advanced to floor: {newlevel}')

                if level == 23:
                    # Voidlord Malgros' Abyssal Dominion
                    victory_embed = discord.Embed(
                        title="Voidlord Malgros Defeated!",
                        description=(
                            "Abyssal Imps and Voidbringer Fiends succumb to your might in the Chaotic Abyss. The Voidlord Malgros bows before the cosmic forces at play, his abyssal dominion shattered."
                            "\n\nAs the void dissipates, the tower pulsates with ancient energies, revealing glimpses of a forbidden prophecy—a realm between realms, the Voidlord's fall, and the imminent convergence of cosmic forces."
                            "\n\nLevel 24 awaits, promising a descent into the shadows and revelations that pierce the veil of reality."
                        ),
                        color=0x00ff00  # Green color for success
                    )

                    await ctx.send(embed=victory_embed)

                    newlevel = level + 1
                    async with self.bot.pool.acquire() as connection:
                        await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                 ctx.author.id)
                    await ctx.send(f'You have advanced to floor: {newlevel}')

                if level == 24:
                    # Soulshredder Vorath's Haunting Embrace
                    victory_embed = discord.Embed(
                        title="Soulshredder Vorath Defeated!",
                        description=(
                            "Dreadshade Specters and Soulreaver Harbingers fade into the shadows as you conquer the Enigmatic Sanctum. Soulshredder Vorath, a harbinger of desolation, succumbs to your unwavering resolve."
                            "\n\nIn the aftermath, the tower itself whispers of forbidden rituals, shattered soul essences, and the Soulshredder's malevolent purpose. Cosmic energies surge, guiding you towards Level 25—a realm where the line between reality and nightmare blurs."
                        ),
                        color=0x00ff00  # Green color for success
                    )

                    await ctx.send(embed=victory_embed)

                    newlevel = level + 1
                    async with self.bot.pool.acquire() as connection:
                        await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                 ctx.author.id)
                    await ctx.send(f'You have advanced to floor: {newlevel}')

                if level == 25:
                    # Pyroclasmic Overfiend's Infernal Convergence
                    victory_embed = discord.Embed(
                        title="Pyroclasmic Overfiend Defeated!",
                        description=(
                            "Inferno Aberrations and Brimstone Fiends bow before your might as you conquer the Blazing Abyss. The Pyroclasmic Overfiend, a creature of elemental chaos, succumbs to the cosmic flames."
                            "\n\nThe tower, now pulsating with immense energy, unfolds visions of cataclysmic convergence, a cosmic inferno, and the imminent unraveling of reality itself. As the Pyroclasmic Overfiend's essence merges with the tower, Level 26 beckons—the final threshold where destinies entwine and cosmic forces clash."
                        ),
                        color=0x00ff00  # Green color for success
                    )

                    await ctx.send(embed=victory_embed)

                    chest_embed = discord.Embed(
                        title="Choose Your Treasure",
                        description=(
                            "You have a choice to make: Before you lie two treasure chests, each shimmering with an otherworldly aura. "
                            "The left chest appears ancient and ornate, while the right chest is smaller but radiates a faint magical glow."
                            f"{ctx.author.mention}, Type left or right to make your decision. You have 60 seconds!"
                        ),
                        color=0x0055ff  # Blue color for options
                    )
                    chest_embed.set_footer(text=f"Type left or right to make your decision.")
                    await ctx.send(embed=chest_embed)

                    async with self.bot.pool.acquire() as connection:
                        prestige_level = await connection.fetchval('SELECT prestige FROM battletower WHERE id = $1',
                                                                   ctx.author.id)
                        level = await connection.fetchval('SELECT level FROM battletower WHERE id = $1', ctx.author.id)

                    def check(m):
                        return m.author == ctx.author and m.content.lower() in ['left', 'right']

                    import random
                    if prestige_level >= 1:
                        new_level = level + 1

                        async with self.bot.pool.acquire() as connection:
                            left_reward_type = random.choice(['crate', 'money'])
                            right_reward_type = random.choice(['crate', 'money'])

                            if left_reward_type == 'crate':
                                left_options = ['legendary', 'fortune', 'mystery', 'common', 'uncommon', 'magic',
                                                'rare']
                                left_weights = [1, 1, 80, 170, 150, 20, 75]  # Weighted values according to percentages
                                left_crate_type = random.choices(left_options, left_weights)[0]
                            else:
                                left_money_amount = random.choice(
                                    [10000, 15000, 20000, 50000, 25000, 10000, 27000, 33000, 100000, 5000, 150000])

                            if right_reward_type == 'crate':
                                right_options = ['legendary', 'fortune', 'mystery', 'common', 'uncommon', 'magic',
                                                 'rare']
                                right_weights = [1, 1, 80, 170, 150, 20, 75]  # Weighted values according to percentages
                                right_crate_type = random.choices(right_options, right_weights)[0]
                            else:
                                right_money_amount = random.choice(
                                    [10000, 15000, 20000, 50000, 25000, 10000, 27000, 33000, 100000, 5000, 150000])

                            await ctx.send(
                                "You see two chests: one on the left and one on the right. Which one do you choose? (Type 'left' or 'right')")

                            try:
                                msg = await self.bot.wait_for('message', check=check, timeout=60.0)
                            except asyncio.TimeoutError:
                                await ctx.send('You took too long to decide. The chests remain unopened.')
                                await ctx.send(f'You have advanced to floor: {new_level}')
                                await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                         ctx.author.id)
                                try:
                                    await self.remove_player_from_fight(ctx.author.id)
                                except Exception as e:
                                    pass
                                return

                            choice = msg.content.lower()

                            if choice == 'left':
                                if left_reward_type == 'crate':
                                    await ctx.send(
                                        f'You open the chest on the left and find a {emotes[left_crate_type]} crate!')
                                    await connection.execute(
                                        f'UPDATE profile SET crates_{left_crate_type} = crates_{left_crate_type} + 1 WHERE "user" = $1',
                                        ctx.author.id)
                                    unchosen_reward = right_reward_type
                                    if unchosen_reward == 'crate':
                                        await ctx.send(
                                            f'You could have gotten a {emotes[right_crate_type]} crate if you chose the right chest.')
                                    else:
                                        await ctx.send(
                                            f'You could have gotten **${right_money_amount}** if you chose the right chest.')
                                else:
                                    await ctx.send(f'You open the chest on the left and find **${left_money_amount}**!')
                                    await connection.execute('UPDATE profile SET money = money + $1 WHERE "user" = $2',
                                                             left_money_amount, ctx.author.id)
                                    unchosen_reward = right_reward_type
                                    if unchosen_reward == 'crate':
                                        await ctx.send(
                                            f'You could have gotten a {emotes[right_crate_type]} crate if you chose the right chest.')
                                    else:
                                        await ctx.send(
                                            f'You could have gotten **${right_money_amount}** if you chose the right chest.')
                            else:
                                if right_reward_type == 'crate':
                                    await ctx.send(
                                        f'You open the chest on the right and find a {emotes[right_crate_type]} crate!')
                                    await connection.execute(
                                        f'UPDATE profile SET crates_{right_crate_type} = crates_{right_crate_type} + 1 WHERE "user" = $1',
                                        ctx.author.id)
                                    unchosen_reward = left_reward_type
                                    if unchosen_reward == 'crate':
                                        await ctx.send(
                                            f'You could have gotten a {emotes[left_crate_type]} crate if you chose the left chest.')
                                    else:
                                        await ctx.send(
                                            f'You could have gotten **${left_money_amount}** if you chose the left chest.')
                                else:
                                    await ctx.send(
                                        f'You open the chest on the right and find **${right_money_amount}**!')
                                    await connection.execute('UPDATE profile SET money = money + $1 WHERE "user" = $2',
                                                             right_money_amount, ctx.author.id)
                                    unchosen_reward = left_reward_type
                                    if unchosen_reward == 'crate':
                                        await ctx.send(
                                            f'You could have gotten a {emotes[left_crate_type]} crate if you chose the left chest.')
                                    else:
                                        await ctx.send(
                                            f'You could have gotten **${left_money_amount}** if you chose the left chest.')

                            await ctx.send(f'You have advanced to floor: {new_level}')
                            await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                     ctx.author.id)
                            try:
                                await self.remove_player_from_fight(ctx.author.id)
                            except Exception as e:
                                pass

                            #--------------------------
                            # --------------------------
                            # --------------------------
                    else:
                        try:
                            msg = await self.bot.wait_for('message', check=check, timeout=60.0)
                        except asyncio.TimeoutError:
                            newlevel = level + 1
                            await ctx.send('You took too long to decide. The chests remain unopened.')
                            await ctx.send(f'You have advanced to floor: {newlevel}')
                            async with self.bot.pool.acquire() as connection:
                                await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                         ctx.author.id)
                                try:
                                    await self.remove_player_from_fight(ctx.author.id)
                                except Exception as e:
                                    pass
                                return
                        else:
                            newlevel = level + 1
                            if msg.content.lower() == 'left':
                                await ctx.send(
                                    'You open the chest on the left and find: <:f_money:1146593710516224090> 1 '
                                    'Fortune Crate!')
                                await ctx.send(f'You have advanced to floor: {newlevel}')
                                async with self.bot.pool.acquire() as connection:
                                    await connection.execute(
                                        'UPDATE profile SET crates_fortune = crates_fortune + 1 WHERE "user" '
                                        '= $1', ctx.author.id)
                                    await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                             ctx.author.id)
                            else:
                                await ctx.send(
                                    'You open the chest on the right and find: **$2** Maybe there is a coffee shop somewhere here..')
                                await ctx.send(f'You have advanced to floor: {newlevel}')
                                async with self.bot.pool.acquire() as connection:
                                    await connection.execute(
                                        'UPDATE profile SET money = money + 2 WHERE "user" '
                                        '= $1', ctx.author.id)
                                    await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                             ctx.author.id)

                if level == 26:
                    # Sangromancer Malroth's Crimson Overture
                    victory_embed = discord.Embed(
                        title="Sangromancer Malroth Defeated!",
                        description=(
                            "The Crimson Serpent and Sanguine Horror writhe in defeat as you conquer the Scarlet Sanctum. Sangromancer Malroth, a master of blood magic, bows before the cosmic symphony."
                            "\n\nAs the tower resonates with arcane melodies, visions unfold—a tapestry of forbidden rituals, a symphony of despair, and the Sangromancer's malevolent dance. The tower's pulse quickens, guiding you towards Level 27—a realm where chaos forges Leviathans and destinies intertwine."
                        ),
                        color=0x00ff00  # Green color for success
                    )

                    await ctx.send(embed=victory_embed)

                    newlevel = level + 1
                    async with self.bot.pool.acquire() as connection:
                        await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                 ctx.author.id)
                    await ctx.send(f'You have advanced to floor: {newlevel}')

                if level == 27:
                    # Chaosforged Leviathan's Abyssal Onslaught
                    victory_embed = discord.Embed(
                        title="Chaosforged Leviathan Defeated!",
                        description=(
                            "Doombringer Abominations and Chaosspawn Horrors yield before your might in the Abyssal Abyss. The Chaosforged Leviathan, a creature born of cosmic chaos, succumbs to the relentless onslaught."
                            "\n\nAs the tower vibrates with primordial energies, visions reveal a realm of discord, a Leviathan's awakening, and the imminent clash of chaotic forces. The tower's resonance deepens, beckoning you towards Level 28—a realm where nethersworn aberrations and eldritch behemoths await."
                        ),
                        color=0x00ff00  # Green color for success
                    )

                    await ctx.send(embed=victory_embed)

                    newlevel = level + 1
                    async with self.bot.pool.acquire() as connection:
                        await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                 ctx.author.id)
                    await ctx.send(f'You have advanced to floor: {newlevel}')

                if level == 28:
                    # Abyssal Enderark's Nethersworn Convergence
                    victory_embed = discord.Embed(
                        title="Abyssal Enderark Defeated!",
                        description=(
                            "Nethersworn Aberrations and Eldritch Behemoths fall silent as you conquer the Netherrealm Nexus. Abyssal Enderark, a harbinger of the abyss, succumbs to your unyielding resolve."
                            "\n\nIn the aftermath, the tower pulsates with eldritch energies, revealing glimpses of a realm between realms, an Enderark's descent, and the cosmic convergence of nether forces. The tower's call grows stronger, guiding you towards Level 29—a realm where darktide krakens and abyssal voidlords await."
                        ),
                        color=0x00ff00  # Green color for success
                    )

                    await ctx.send(embed=victory_embed)

                    newlevel = level + 1
                    async with self.bot.pool.acquire() as connection:
                        await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                 ctx.author.id)
                    await ctx.send(f'You have advanced to floor: {newlevel}')

                if level == 29:
                    # Tidal Terror Abaddon's Abyssal Onslaught
                    victory_embed = discord.Embed(
                        title="Tidal Terror Abaddon Defeated!",
                        description=(
                            "Darktide Krakens and Abyssal Voidlords yield before your might in the Cursed Abyss. Tidal Terror Abaddon, a creature of abyssal waters, succumbs to the relentless onslaught."
                            "\n\nAs the tower resonates with aquatic energies, visions unfold—a tempest of darkness, a kraken's lament, and the imminent clash of abyssal forces. The tower's song reaches a crescendo, beckoning you towards Level 30—the final realm where destinies converge, and the tower's ultimate secret is laid bare."
                        ),
                        color=0x00ff00  # Green color for success
                    )

                    await ctx.send(embed=victory_embed)

                    newlevel = level + 1
                    async with self.bot.pool.acquire() as connection:
                        await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                 ctx.author.id)
                    await ctx.send(f'You have advanced to floor: {newlevel}')

                if level == 30:

                    # The Revelation of Cosmic Tragedy
                    cosmic_embed = discord.Embed(
                        title="The Cosmic Abyss: A Symphony of Despair",
                        description=(
                            "As you stand amidst the cosmic ruins, triumphant after landing a fatal blow to the Guardian, the room immediately lights back up. However, a sinister revelation unfolds—a large shadow lurks behind you. Only then do you realize the shocking truth: you were a puppet the entire time, masked by the dark magic of the Overlord."
                            "\n\nThe malevolent magic twisted your perception, making you believe you were fighting evil. In reality, you were mercilessly slaying the forces of good. Your vision was impaired by the enchantment, distorting all that was pure into sinister illusions. The growls and snarls were not manifestations of evil, but the screams of horror as you rampaged through the tower, cutting down every good essence in your path."
                            "\n\nThe room, once filled with the triumphant glow of your victory, now becomes a haunting reminder of the manipulation that led you astray. The cosmic tragedy deepens as the Overlord's dark magic reveals its insidious nature, turning your heroic journey into a nightmarish descent into despair."
                            "\n\nThe mocking laughter of the Overlord of Shadows echoes through the void, resonating with the cruel irony of your unwitting role in this cosmic play. The once-heroic Guardians, sacrificed to contain the unleashed energies, now join the chorus of sorrowful echoes, their tales entwined with your own."
                            "\n\nAs you are forcibly teleported to a desolate room, the essence of nothingness prevails—an eternal void devoid of sensation. No family, no friends, no warmth, or comforting embrace; all connections to the world you once knew severed. Time itself unravels, trapping you in perpetual stasis amid the overwhelming silence that accentuates the profound emptiness."
                            "\n\nIn this timeless abyss, the weight of regret becomes an indomitable force. You, stripped of purpose and connection, are left to grapple with the consequences of your unwitting role in the tower's demise. The laughter of the Overlord of Shadows continues to reverberate, a haunting reminder of the malevolence that exploited your journey."
                            "\n\nAs you drift aimlessly through the emptiness, the echoes of the corrupted Guardians' stories intertwine with your own. Your existence becomes a forlorn symphony of despair, a solitary melody played in the cosmic void."
                            "\n\nThere is no escape, no redemption, only an eternity of isolation and remorse. The Battle Tower, once a beacon of hope, is now a distant memory, and you, adrift in the abyss, become a forgotten soul—lost to the cosmic tragedy orchestrated by the Overlord of Shadows."
                            "\n\nAnd in this void, a cruel twist awaits. You are subjected to an unending torment—a relentless loop that replays the events of the tower. However, in this distorted reality, you witness a distorted version of yourself, a puppet dancing to the malevolent tune of the Overlord."
                            "\n\nYou, now a mere spectator of your own nightmare, see yourself slaying innocent people, mercilessly striking down the Guardians of Radiance who once fought valiantly. The tortured souls of the fallen beg you to stop, their pleas echoing in the hollow abyss."
                            "\n\nYet, you are powerless to change the course of this macabre play. The visions unfold relentlessly, each repetition etching the weight of guilt deeper into your essence. The distorted version of you, manipulated by the Overlord's dark magic, becomes a puppet of cosmic tragedy, forever ensnared in a nightmarish loop of despair."
                        ),
                        color=0xff0000  # Red color for the climax
                    )

                    await ctx.send(embed=cosmic_embed)

                    async with self.bot.pool.acquire() as connection:
                        prestige_level = await connection.fetchval('SELECT prestige FROM battletower WHERE id = $1',
                                                                   ctx.author.id)

                    if prestige_level >= 1:

                        async with self.bot.pool.acquire() as connection:
                            await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                     ctx.author.id)

                        await ctx.send(f'This is the end for you... {ctx.author.mention}.. or is it..?')

                        crate_options = ['legendary', 'divine', 'mystery', 'fortune']
                        weights = [0.25, 0.25, 0.25, 0.25]  # Weighted values according to percentages

                        selected_crate = randomm.choices(crate_options, weights)[0]

                        if ctx.author.id == 295173706496475136:
                            selected_crate = 'divine'


                        async with self.bot.pool.acquire() as connection:
                            await connection.execute(
                                f'UPDATE profile SET crates_{selected_crate} = crates_{selected_crate} +1 WHERE "user" = $1',
                                ctx.author.id)

                        await ctx.send(
                            f"You have received 1 {emotes[selected_crate]} crate for completing the battletower on prestige level: {prestige_level}. Congratulations!")


                    else:
                        async with self.bot.pool.acquire() as connection:
                            await connection.execute(
                                'UPDATE profile SET crates_divine = crates_divine +1 WHERE "user" '
                                '= $1', ctx.author.id)
                        async with self.bot.pool.acquire() as connection:
                            await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                     ctx.author.id)
                        await ctx.send(f'This is the end for you... {ctx.author.mention}.. or is it..?')

                        await ctx.send(
                            "You have received 1 <:f_divine:1169412814612471869> crate for completing the battletower, congratulations.")

                # ----------------------------------------------------------------------------------------------------------------------

                if level == 4:
                    victory_embed = discord.Embed(
                        title="Necromancer Voss Defeated!",
                        description=(
                            "As you stand amidst the shattered skeletons and defeated zombies, a haunting silence fills the Serpent's Lair. "
                            "Necromancer Voss, a dark conjurer of unholy power, has been vanquished, his malevolent schemes thwarted."
                            "\n\nYet, the Necromancer's presence lingers in the air, and you can't help but wonder about the origins of this once hallowed tower. What secrets does it hold?"
                            "\n\nWith this victory, you move deeper into the tower, your journey now intertwined with the ancient mysteries it holds."
                        ),
                        color=0x00ff00  # Green color for success
                    )

                    await ctx.send(embed=victory_embed)

                    # Create an embed for the treasure chest options
                    chest_embed = discord.Embed(
                        title="Choose Your Treasure",
                        description=(
                            "You have a choice to make: Before you lie two treasure chests, each shimmering with an otherworldly aura. "
                            "The left chest appears ancient and ornate, while the right chest is smaller but radiates a faint magical glow."
                            f"{ctx.author.mention}, Type left or right to make your decision. You have 60 seconds!"
                        ),
                        color=0x0055ff  # Blue color for options
                    )
                    chest_embed.set_footer(text=f"Type left or right to make your decision.")
                    await ctx.send(embed=chest_embed)

                    async with self.bot.pool.acquire() as connection:
                        prestige_level = await connection.fetchval('SELECT prestige FROM battletower WHERE id = $1',
                                                                   ctx.author.id)
                        level = await connection.fetchval('SELECT level FROM battletower WHERE id = $1', ctx.author.id)

                    def check(m):
                        return m.author == ctx.author and m.content.lower() in ['left', 'right']

                    import random
                    if prestige_level >= 1:
                        new_level = level + 1

                        async with self.bot.pool.acquire() as connection:
                            left_reward_type = random.choice(['crate', 'money'])
                            right_reward_type = random.choice(['crate', 'money'])

                            if left_reward_type == 'crate':
                                left_options = ['legendary', 'fortune', 'mystery', 'common', 'uncommon', 'magic',
                                                'rare']
                                left_weights = [1, 1, 80, 170, 150, 20, 75]  # Weighted values according to percentages
                                left_crate_type = random.choices(left_options, left_weights)[0]
                            else:
                                left_money_amount = random.choice(
                                    [10000, 15000, 20000, 50000, 25000, 10000, 27000, 33000, 100000, 5000, 150000])

                            if right_reward_type == 'crate':
                                right_options = ['legendary', 'fortune', 'mystery', 'common', 'uncommon', 'magic',
                                                 'rare']
                                right_weights = [1, 1, 80, 170, 150, 20, 75]  # Weighted values according to percentages
                                right_crate_type = random.choices(right_options, right_weights)[0]
                            else:
                                right_money_amount = random.choice(
                                    [10000, 15000, 20000, 50000, 25000, 10000, 27000, 33000, 100000, 5000, 150000])

                            await ctx.send(
                                "You see two chests: one on the left and one on the right. Which one do you choose? (Type 'left' or 'right')")

                            try:
                                msg = await self.bot.wait_for('message', check=check, timeout=60.0)
                            except asyncio.TimeoutError:
                                await ctx.send('You took too long to decide. The chests remain unopened.')
                                await ctx.send(f'You have advanced to floor: {new_level}')
                                await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                         ctx.author.id)
                                try:
                                    await self.remove_player_from_fight(ctx.author.id)
                                except Exception as e:
                                    pass
                                return

                            choice = msg.content.lower()

                            if choice == 'left':
                                if left_reward_type == 'crate':
                                    await ctx.send(
                                        f'You open the chest on the left and find a {emotes[left_crate_type]} crate!')
                                    await connection.execute(
                                        f'UPDATE profile SET crates_{left_crate_type} = crates_{left_crate_type} + 1 WHERE "user" = $1',
                                        ctx.author.id)
                                    unchosen_reward = right_reward_type
                                    if unchosen_reward == 'crate':
                                        await ctx.send(
                                            f'You could have gotten a {emotes[right_crate_type]} crate if you chose the right chest.')
                                    else:
                                        await ctx.send(
                                            f'You could have gotten **${right_money_amount}** if you chose the right chest.')
                                else:
                                    await ctx.send(f'You open the chest on the left and find **${left_money_amount}**!')
                                    await connection.execute('UPDATE profile SET money = money + $1 WHERE "user" = $2',
                                                             left_money_amount, ctx.author.id)
                                    unchosen_reward = right_reward_type
                                    if unchosen_reward == 'crate':
                                        await ctx.send(
                                            f'You could have gotten a {emotes[right_crate_type]} crate if you chose the right chest.')
                                    else:
                                        await ctx.send(
                                            f'You could have gotten **${right_money_amount}** if you chose the right chest.')
                            else:
                                if right_reward_type == 'crate':
                                    await ctx.send(
                                        f'You open the chest on the right and find a {emotes[right_crate_type]} crate!')
                                    await connection.execute(
                                        f'UPDATE profile SET crates_{right_crate_type} = crates_{right_crate_type} + 1 WHERE "user" = $1',
                                        ctx.author.id)
                                    unchosen_reward = left_reward_type
                                    if unchosen_reward == 'crate':
                                        await ctx.send(
                                            f'You could have gotten a {emotes[left_crate_type]} crate if you chose the left chest.')
                                    else:
                                        await ctx.send(
                                            f'You could have gotten **${left_money_amount}** if you chose the left chest.')
                                else:
                                    await ctx.send(
                                        f'You open the chest on the right and find **${right_money_amount}**!')
                                    await connection.execute('UPDATE profile SET money = money + $1 WHERE "user" = $2',
                                                             right_money_amount, ctx.author.id)
                                    unchosen_reward = left_reward_type
                                    if unchosen_reward == 'crate':
                                        await ctx.send(
                                            f'You could have gotten a {emotes[left_crate_type]} crate if you chose the left chest.')
                                    else:
                                        await ctx.send(
                                            f'You could have gotten **${left_money_amount}** if you chose the left chest.')

                            await ctx.send(f'You have advanced to floor: {new_level}')
                            await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                     ctx.author.id)
                            try:
                                await self.remove_player_from_fight(ctx.author.id)
                            except Exception as e:
                                pass

                            # --------------------------
                            # --------------------------
                            # --------------------------
                    else:
                        try:
                            msg = await self.bot.wait_for('message', check=check, timeout=60.0)
                        except asyncio.TimeoutError:
                            newlevel = level + 1
                            await ctx.send('You took too long to decide. The chests remain unopened.')
                            await ctx.send(f'You have advanced to floor: {newlevel}')
                            async with self.bot.pool.acquire() as connection:
                                await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                         ctx.author.id)
                                try:
                                    await self.remove_player_from_fight(ctx.author.id)
                                except Exception as e:
                                    pass
                                return
                        else:
                            newlevel = level + 1
                            if msg.content.lower() == 'left':
                                await ctx.send(
                                    'You open the chest on the left and find: <:F_Common:1139514874016309260> 3 '
                                    'Common Crates!')
                                await ctx.send(f'You have advanced to floor: {newlevel}')
                                async with self.bot.pool.acquire() as connection:
                                    await connection.execute(
                                        'UPDATE profile SET crates_common = crates_common + 3 WHERE "user" '
                                        '= $1', ctx.author.id)
                                    await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                             ctx.author.id)
                            else:
                                await ctx.send('You open the chest on the right and find: **20000**!')
                                await ctx.send(f'You have advanced to floor: {newlevel}')
                                async with self.bot.pool.acquire() as connection:
                                    await connection.execute(
                                        'UPDATE profile SET money = money + 20000 WHERE "user" '
                                        '= $1', ctx.author.id)
                                    await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                             ctx.author.id)

                if level == 17:
                    victory_embed = discord.Embed(
                        title="Eldritch Devourer Defeated!",
                        description=(
                            f"The {level_name}, known as the Convergence Nexus, shudders as the last echoes of battle fade. Chaos Fiends and Voidborn Horrors lie scattered, defeated. The Eldritch Devourer, a colossal cosmic anomaly, succumbs to your relentless assault, its astral essence dissipating into the void."
                            "\n\nAs the Eldritch Devourer crumbles, the artifact in your possession vibrates with newfound energy. It projects ethereal visions, revealing the birth of the Devourer—a celestial being designed by the Forerunners as the embodiment of cosmic balance. Yet, the malevolent force, lurking in the shadows, twisted its purpose, turning it into a force of destruction."
                            "\n\nIn the cosmic aftermath, the artifact speaks in resonant whispers, hinting at latent powers within the Eldritch Devourer's remnants. With the convergence of cosmic forces, a new revelation awaits you on level 18—the Tower's inner sanctum, where the fabric of reality is interwoven with the remnants of ancient guardians and the malevolent force's sinister schemes."
                            "\n\nEmbrace the cosmic energies and ascend to level 18, for the Tower's secrets are yet to unfold, and the cosmic dance of destiny continues."
                        ),
                        color=0x00ff00  # Green color for success
                    )

                    await ctx.send(embed=victory_embed)

                    newlevel = level + 1
                    async with self.bot.pool.acquire() as connection:
                        await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                 ctx.author.id)
                    await ctx.send(
                        f'You have transcended to the next cosmic realm, where ancient revelations await: {newlevel}')

                # Let's add the altered perception and hint for the dark truth in level 18

                if level == 18:
                    # Altered Perception Flash
                    flash_embed = discord.Embed(
                        title="A Momentary Flash",
                        description=(
                            "In the midst of the cosmic chaos, there's a fleeting moment where everything shifts. The room bathes in an ethereal light, and for an instant, it seems as if you've just triumphed over a guardian of good, a defender of cosmic harmony."
                            "\n\nHowever, the vision is ephemeral, and the room swiftly returns to its dark and foreboding state. The artifact's glow dims, leaving you with a disquieting sense of uncertainty. Was it a glimpse of the past, a trick of the malevolent force, or a foreshadowing of darker revelations?"
                        ),
                        color=0xffd700  # Gold color for mystical elements
                    )

                    await ctx.send(embed=flash_embed)

                    # Broodmother Arachna's Lair Victory
                    victory_embed = discord.Embed(
                        title="Broodmother Arachna Defeated!",
                        description=(
                            f"The {level_name}, also known as Arachna's Abyss, bears the scars of a relentless clash. The Venomous Arachnids' hisses and the Arachnoid Aberrations' eerie silence now accompany the stillness of Broodmother Arachna's lair. The colossal arachnid queen lies vanquished, her once-daunting domain now a silent testament to your triumph."
                            "\n\nAs the aftermath settles, the artifact resonates with the remnants of the cosmic battle. It unfolds visions that transcend the linear flow of time—showcasing not only the corrupted past of Broodmother Arachna but glimpses of her potential redemption. Within her twisted essence, ancient echoes of a guardian's duty linger."
                            "\n\nWith each step deeper into the Tower, the artifact pulses with anticipation. It reveals the imminent convergence of destinies—the Tower's core, a crucible of ancient guardians and the malevolent force's relentless machinations. A cosmic alliance may await, as the Tower itself yearns for redemption."
                            "\n\nAs you ascend to level 19, the Tower's heartbeat becomes more palpable. The artifact, now an arcane compass, guides you towards the heart of the cosmic storm, where revelations and alliances await—a celestial dance that could reshape the very fabric of the cosmos."
                        ),
                        color=0x00ff00  # Green color for success
                    )

                    await ctx.send(embed=victory_embed)

                    newlevel = level + 1
                    async with self.bot.pool.acquire() as connection:
                        await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                 ctx.author.id)
                    await ctx.send(
                        f'You have transcended to the next cosmic realm, where destinies intertwine and revelations unfold: {newlevel}')

                if level == 5:
                    victory_embed = discord.Embed(
                        title="Triumph Over Blackblade Marauder!",
                        description=(
                            "The Halls of Despair fall eerily silent as the notorious Blackblade Marauder lies defeated. "
                            "His bandit and highwayman accomplices now tremble before your indomitable spirit."
                            "\n\nThe Marauder's sinister map, discovered in his lair, hints at hidden treasures deeper within the tower. You're drawn further into the tower's enigmatic history, eager to uncover its secrets."
                        ),
                        color=0x00ff00  # Green color for success
                    )

                    await ctx.send(embed=victory_embed)

                    # Create an embed for the treasure chest options
                    chest_embed = discord.Embed(
                        title="Choose Your Treasure",
                        description=(
                            "You have a choice to make: Before you lie two treasure chests, each shimmering with an otherworldly aura. "
                            "The left chest appears ancient and ornate, while the right chest is smaller but radiates a faint magical glow."
                            f"{ctx.author.mention}, Type left or right to make your decision. You have 60 seconds!"
                        ),
                        color=0x0055ff  # Blue color for options
                    )
                    chest_embed.set_footer(text=f"Type left or right to make your decision.")
                    await ctx.send(embed=chest_embed)

                    async with self.bot.pool.acquire() as connection:
                        prestige_level = await connection.fetchval('SELECT prestige FROM battletower WHERE id = $1',
                                                                   ctx.author.id)
                        level = await connection.fetchval('SELECT level FROM battletower WHERE id = $1', ctx.author.id)

                    def check(m):
                        return m.author == ctx.author and m.content.lower() in ['left', 'right']

                    import random
                    if prestige_level >= 1:
                        new_level = level + 1

                        async with self.bot.pool.acquire() as connection:
                            left_reward_type = random.choice(['crate', 'money'])
                            right_reward_type = random.choice(['crate', 'money'])

                            if left_reward_type == 'crate':
                                left_options = ['legendary', 'fortune', 'mystery', 'common', 'uncommon', 'magic',
                                                'rare']
                                left_weights = [1, 1, 80, 170, 150, 20, 75]  # Weighted values according to percentages
                                left_crate_type = random.choices(left_options, left_weights)[0]
                            else:
                                left_money_amount = random.choice(
                                    [10000, 15000, 20000, 50000, 25000, 10000, 27000, 33000, 100000, 5000, 150000])

                            if right_reward_type == 'crate':
                                right_options = ['legendary', 'fortune', 'mystery', 'common', 'uncommon', 'magic',
                                                 'rare']
                                right_weights = [1, 1, 80, 170, 150, 20, 75]  # Weighted values according to percentages
                                right_crate_type = random.choices(right_options, right_weights)[0]
                            else:
                                right_money_amount = random.choice(
                                    [10000, 15000, 20000, 50000, 25000, 10000, 27000, 33000, 100000, 5000, 150000])

                            await ctx.send(
                                "You see two chests: one on the left and one on the right. Which one do you choose? (Type 'left' or 'right')")

                            try:
                                msg = await self.bot.wait_for('message', check=check, timeout=60.0)
                            except asyncio.TimeoutError:
                                await ctx.send('You took too long to decide. The chests remain unopened.')
                                await ctx.send(f'You have advanced to floor: {new_level}')
                                await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                         ctx.author.id)
                                try:
                                    await self.remove_player_from_fight(ctx.author.id)
                                except Exception as e:
                                    pass
                                return

                            choice = msg.content.lower()

                            if choice == 'left':
                                if left_reward_type == 'crate':
                                    await ctx.send(
                                        f'You open the chest on the left and find a {emotes[left_crate_type]} crate!')
                                    await connection.execute(
                                        f'UPDATE profile SET crates_{left_crate_type} = crates_{left_crate_type} + 1 WHERE "user" = $1',
                                        ctx.author.id)
                                    unchosen_reward = right_reward_type
                                    if unchosen_reward == 'crate':
                                        await ctx.send(
                                            f'You could have gotten a {emotes[right_crate_type]} crate if you chose the right chest.')
                                    else:
                                        await ctx.send(
                                            f'You could have gotten **${right_money_amount}** if you chose the right chest.')
                                else:
                                    await ctx.send(f'You open the chest on the left and find **${left_money_amount}**!')
                                    await connection.execute('UPDATE profile SET money = money + $1 WHERE "user" = $2',
                                                             left_money_amount, ctx.author.id)
                                    unchosen_reward = right_reward_type
                                    if unchosen_reward == 'crate':
                                        await ctx.send(
                                            f'You could have gotten a {emotes[right_crate_type]} crate if you chose the right chest.')
                                    else:
                                        await ctx.send(
                                            f'You could have gotten **${right_money_amount}** if you chose the right chest.')
                            else:
                                if right_reward_type == 'crate':
                                    await ctx.send(
                                        f'You open the chest on the right and find a {emotes[right_crate_type]} crate!')
                                    await connection.execute(
                                        f'UPDATE profile SET crates_{right_crate_type} = crates_{right_crate_type} + 1 WHERE "user" = $1',
                                        ctx.author.id)
                                    unchosen_reward = left_reward_type
                                    if unchosen_reward == 'crate':
                                        await ctx.send(
                                            f'You could have gotten a {emotes[left_crate_type]} crate if you chose the left chest.')
                                    else:
                                        await ctx.send(
                                            f'You could have gotten **${left_money_amount}** if you chose the left chest.')
                                else:
                                    await ctx.send(
                                        f'You open the chest on the right and find **${right_money_amount}**!')
                                    await connection.execute('UPDATE profile SET money = money + $1 WHERE "user" = $2',
                                                             right_money_amount, ctx.author.id)
                                    unchosen_reward = left_reward_type
                                    if unchosen_reward == 'crate':
                                        await ctx.send(
                                            f'You could have gotten a {emotes[left_crate_type]} crate if you chose the left chest.')
                                    else:
                                        await ctx.send(
                                            f'You could have gotten **${left_money_amount}** if you chose the left chest.')

                            await ctx.send(f'You have advanced to floor: {new_level}')
                            await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                     ctx.author.id)
                            try:
                                await self.remove_player_from_fight(ctx.author.id)
                            except Exception as e:
                                pass



                    else:
                        try:
                            msg = await self.bot.wait_for('message', check=check, timeout=60.0)
                        except asyncio.TimeoutError:
                            newlevel = level + 1
                            await ctx.send('You took too long to decide. The chests remain unopened.')
                            await ctx.send(f'You have advanced to floor: {newlevel}')
                            async with self.bot.pool.acquire() as connection:
                                await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                         ctx.author.id)

                                try:
                                    await self.remove_player_from_fight(ctx.author.id)
                                except Exception as e:
                                    pass
                                return
                        else:
                            newlevel = level + 1
                            if msg.content.lower() == 'left':
                                await ctx.send(
                                    'You open the chest on the left and find: <:F_common:1139514874016309260> 3 '
                                    'Common Crates!')
                                await ctx.send(f'You have advanced to floor: {newlevel}')
                                async with self.bot.pool.acquire() as connection:
                                    await connection.execute(
                                        'UPDATE profile SET crates_common = crates_common + 3 WHERE "user" '
                                        '= $1', ctx.author.id)
                                    await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                             ctx.author.id)
                            else:
                                await ctx.send('You open the chest on the right and find: **$20000**!')
                                await ctx.send(f'You have advanced to floor: {newlevel}')
                                async with self.bot.pool.acquire() as connection:
                                    await connection.execute(
                                        'UPDATE profile SET money = money + 20000 WHERE "user" '
                                        '= $1', ctx.author.id)
                                    await connection.execute('UPDATE battletower SET level = level + 1 WHERE id = $1',
                                                             ctx.author.id)
                                try:
                                    await self.remove_player_from_fight(ctx.author.id)
                                except KeyError:
                                    pass
                try:
                    await self.remove_player_from_fight(ctx.author.id)
                except Exception as e:
                    pass

        except Exception as e:
            error_message = f"An error occurred before the battle: {e}"
            await ctx.send(error_message)


    @has_char()
    @user_cooldown(100)
    @commands.command(brief=_("Battle against a player (includes raidstats)"))
    @locale_doc
    async def raidbattle(
            self, ctx, money: IntGreaterThan(-1) = 0, enemy: discord.Member = None
    ):
        _(
            """`[money]` - A whole number that can be 0 or greater; defaults to 0
            `[enemy]` - A user who has a profile; defaults to anyone

            Fight against another player while betting money.
            To decide the players' stats, their items, race and class bonuses and raidstats are evaluated.
            
            You also have a change of tripping depending on your luck.

            The money is removed from both players at the start of the battle. Once a winner has been decided, they will receive their money, plus the enemy's money.
            The battle is divided into rounds, in which a player attacks. The first round's attacker is chosen randomly, all other rounds the attacker is the last round's defender.

            The battle ends if one player's HP drops to 0 (winner decided), or if 5 minutes after the battle started pass (tie).
            In case of a tie, both players will get their money back.

            The battle's winner will receive a PvP win, which shows on their profile.
            (This command has a cooldown of 5 minutes)"""
        )
        authorchance = 0
        enemychance = 0
        cheated = False
        max_hp_limit = 5000

        if enemy == ctx.author:
            await self.bot.reset_cooldown(ctx)
            return await ctx.send(_("You can't battle yourself."))

        if ctx.character_data["money"] < money:
            await self.bot.reset_cooldown(ctx)
            return await ctx.send(_("You are too poor."))

        await self.bot.pool.execute(
            'UPDATE profile SET "money"="money"-$1 WHERE "user"=$2;',
            money,
            ctx.author.id,
        )

        if not enemy:
            text = _("{author} - **LVL {level}** seeks a raidbattle! The price is **${money}**.").format(
                author=ctx.author.mention, level=rpgtools.xptolevel(ctx.character_data["xp"]), money=money
            )
        else:
            async with self.bot.pool.acquire() as conn:
                query = 'SELECT xp FROM profile WHERE "user" = $1;'
                xp_value = await conn.fetchval(query, enemy.id)
            text = _(
                "{author} - **LVL {level}** seeks a raidbattle with {enemy} - LVL **{levelen}**! The price is **${money}**."
            ).format(author=ctx.author.mention, level=rpgtools.xptolevel(ctx.character_data["xp"]), enemy=enemy.mention,
                     levelen=rpgtools.xptolevel(xp_value), money=money)

        async def check(user: discord.User) -> bool:
            return await has_money(self.bot, user.id, money)

        future = asyncio.Future()
        view = SingleJoinView(
            future,
            Button(
                style=ButtonStyle.primary,
                label=_("Join the raidbattle!"),
                emoji="\U00002694",
            ),
            allowed=enemy,
            prohibited=ctx.author,
            timeout=60,
            check=check,
            check_fail_message=_("You don't have enough money to join the raidbattle."),
        )

        await ctx.send(text, view=view)

        try:
            enemy_ = await future
        except asyncio.TimeoutError:
            await self.bot.reset_cooldown(ctx)
            await self.bot.pool.execute(
                'UPDATE profile SET "money"="money"+$1 WHERE "user"=$2;',
                money,
                ctx.author.id,
            )
            return await ctx.send(
                _("No one wanted to join your raidbattle, {author}!").format(
                    author=ctx.author.mention
                )
            )

        await self.bot.pool.execute(
            'UPDATE profile SET "money"="money"-$1 WHERE "user"=$2;', money, enemy_.id
        )

        players = []

        try:
            # Fetch elements for the player and the enemy
            highest_item = await self.bot.pool.fetchrow(
                "SELECT ai.element FROM profile p JOIN allitems ai ON (p.user=ai.owner) JOIN"
                " inventory i ON (ai.id=i.item) WHERE i.equipped IS TRUE AND p.user=$1"
                " ORDER BY GREATEST(ai.damage, ai.armor) DESC LIMIT 1;",
                ctx.author.id,
            )

            if highest_item:
                highest_element = highest_item[0]
                highest_element = highest_element.capitalize()
                if highest_element in self.emoji_to_element.values():
                    for emoji, element in self.emoji_to_element.items():
                        if element == highest_element:
                            emoji_for_element = emoji
                            break
                else:
                    emoji_for_element = "❌"
            else:
                emoji_for_element = "❌"

            highest_item_enemy = await self.bot.pool.fetchrow(
                "SELECT ai.element FROM profile p JOIN allitems ai ON (p.user=ai.owner) JOIN"
                " inventory i ON (ai.id=i.item) WHERE i.equipped IS TRUE AND p.user=$1"
                " ORDER BY GREATEST(ai.damage, ai.armor) DESC LIMIT 1;",
                enemy_.id,
            )

            if highest_item_enemy:
                highest_element_enemy = highest_item_enemy[0]
                highest_element_enemy = highest_element_enemy.capitalize()
                if highest_element_enemy in self.emoji_to_element.values():
                    for emoji, element in self.emoji_to_element.items():
                        if element == highest_element_enemy:
                            emoji_for_elementenemy = emoji
                            break
                else:
                    emoji_for_elementenemy = "❌"
            else:
                emoji_for_elementenemy = "❌"

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

            mage_evolution_levels = {
                "Witcher": 1,
                "Enchanter": 2,
                "Mage": 3,
                "Warlock": 4,
                "Dark Caster": 5,
                "White Sorcerer": 6,
            }

            evolution_damage_multiplier = {
                1: 1.10,  # 110%
                2: 1.20,  # 120%
                3: 1.30,  # 130%
                4: 1.50,  # 150%
                5: 1.75,  # 175%
                6: 2.00,  # 200%
            }

            # User IDs
            user_id = ctx.author.id
            enemy_id = enemy_.id

            # Fetch classes and XP
            try:
                # Define common queries
                query_class = 'SELECT "class" FROM profile WHERE "user" = $1;'
                query_xp = 'SELECT "xp" FROM profile WHERE "user" = $1;'

                # Query data for ctx.author.id
                result_author = await self.bot.pool.fetch(query_class, user_id)
                auth_xp = await self.bot.pool.fetch(query_xp, user_id)

                # Convert XP to level for ctx.author.id
                auth_level = rpgtools.xptolevel(auth_xp[0]['xp'])

                # Query data for enemy_.id
                result_opp = await self.bot.pool.fetch(query_class, enemy_id)
                opp_xp = await self.bot.pool.fetch(query_xp, enemy_id)

                # Convert XP to level for enemy_.id
                opp_level = rpgtools.xptolevel(opp_xp[0]['xp'])

                # Initialize chance
                author_chance = 0
                enemy_chance = 0
                lifestealauth = 0
                lifestealopp = 0

                # Function to get Mage evolution level
                def get_mage_evolution(classes):
                    max_evolution = None
                    for class_name in classes:
                        if class_name in mage_evolution_levels:
                            level = mage_evolution_levels[class_name]
                            if max_evolution is None or level > max_evolution:
                                max_evolution = level
                    return max_evolution

                if result_author:
                    author_classes = result_author[0]["class"]
                    if isinstance(author_classes, list):
                        author_classes = author_classes
                    else:
                        author_classes = [author_classes]

                    author_mage_evolution = get_mage_evolution(author_classes)
                    for class_name in author_classes:
                        if class_name in specified_words_values:
                            author_chance += specified_words_values[class_name]
                        if class_name in life_steal_values:
                            lifestealauth += life_steal_values[class_name]

                if result_opp:
                    opp_classes = result_opp[0]["class"]
                    if isinstance(opp_classes, list):
                        opp_classes = opp_classes
                    else:
                        opp_classes = [opp_classes]

                    opp_mage_evolution = get_mage_evolution(opp_classes)
                    for class_name in opp_classes:
                        if class_name in specified_words_values:
                            enemy_chance += specified_words_values[class_name]
                        if class_name in life_steal_values:
                            lifestealopp += life_steal_values[class_name]

            except Exception as e:
                await ctx.send(f"An error occurred while fetching classes and XP: {e}")

            if author_chance != 0:
                authorchance = author_chance

            if enemy_chance != 0:
                enemychance = enemy_chance

            # Fetch player stats
            async with self.bot.pool.acquire() as conn:
                for player in (ctx.author, enemy_):
                    try:
                        user_id = player.id

                        luck_booster = await self.bot.get_booster(player, "luck")

                        query = 'SELECT "luck", "health", "stathp" FROM profile WHERE "user" = $1;'
                        result = await conn.fetchrow(query, user_id)
                        if result:
                            luck_value = float(result['luck'])
                            if luck_value <= 0.3:
                                Luck = 20
                            else:
                                Luck = ((luck_value - 0.3) / (1.5 - 0.3)) * 80 + 20
                            Luck = float(round(Luck, 2))

                            if luck_booster:
                                Luck += Luck * 0.25
                                Luck = float(min(Luck, 100))

                            base_health = 250
                            health = result['health'] + base_health
                            stathp = result['stathp'] * 50
                            dmg, deff = await self.bot.get_raidstats(player, conn=conn)

                            level = rpgtools.xptolevel(
                                auth_xp[0]['xp']) if player == ctx.author else rpgtools.xptolevel(opp_xp[0]['xp'])
                            total_health = health + (level * 5)
                            total_health = total_health + stathp

                            # Get Mage evolution level
                            if player == ctx.author:
                                mage_evolution = author_mage_evolution
                            else:
                                mage_evolution = opp_mage_evolution

                            # Create player dictionary with relevant information
                            u = {"user": player, "hp": total_health, "armor": deff, "damage": dmg, "luck": Luck,
                                 "mage_evolution": mage_evolution}
                            players.append(u)
                        else:
                            await ctx.send(f"User with ID {user_id} not found in the profile table.")
                    except Exception as e:
                        await ctx.send(f"An error occurred: {e}")

            # Determine elements for damage modifiers
            element_strengths = {
                "Light": "Corrupted",
                "Dark": "Light",
                "Corrupted": "Dark",
                "Nature": "Electric",
                "Electric": "Water",
                "Water": "Fire",
                "Fire": "Nature",
                "Wind": "Electric"
            }

            def calculate_damage_modifier(player_element, enemy_element):
                if player_element in element_strengths and element_strengths[player_element] == enemy_element:
                    return decimal.Decimal(round(randomm.uniform(0.1, 0.3), 1))
                elif enemy_element in element_strengths and element_strengths[enemy_element] == player_element:
                    return decimal.Decimal(round(randomm.uniform(-0.1, -0.3), 1))
                return decimal.Decimal('0')

            player_element = highest_element
            enemy_element = highest_element_enemy

            damage_modifier_player = calculate_damage_modifier(player_element, enemy_element)
            damage_modifier_enemy = calculate_damage_modifier(enemy_element, player_element)

            for player in players:
                if player["user"] == ctx.author:
                    player["damage"] = round(player["damage"] * (1 + damage_modifier_player), 2)
                else:
                    player["damage"] = round(player["damage"] * (1 + damage_modifier_enemy), 2)

            # Begin the battle
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
                description=battle_log[0][1], color=self.bot.config.game.primary_colour
            )

            log_message = await ctx.send(
                embed=embed
            )
            await asyncio.sleep(4)

            start = datetime.datetime.utcnow()
            attacker_is_player = random.choice([True, False])

            # Main battle loop
            while (
                    players[0]["hp"] > 0
                    and players[1]["hp"] > 0
                    and datetime.datetime.utcnow() < start + datetime.timedelta(minutes=5)
            ):
                if attacker_is_player:
                    attacker = players[0]
                    defender = players[1]
                else:
                    attacker = players[1]
                    defender = players[0]

                trickluck = float(random.randint(1, 100))

                if float(trickluck) < float(attacker["luck"]):
                    # Check for Mage's Fireball ability
                    if attacker.get("mage_evolution") is not None:
                        fireball_chance = random.randint(1, 100)
                        if fireball_chance <= 40:
                            evolution_level = attacker["mage_evolution"]
                            damage_multiplier = evolution_damage_multiplier.get(evolution_level, 1.0)
                            dmg = (
                                    (attacker["damage"] + Decimal(random.randint(0, 100)) - defender["armor"])
                                    * Decimal(damage_multiplier)
                            )
                            dmg = max(dmg, 1)
                            defender["hp"] -= dmg

                            # Initialize message
                            message = _("{attacker} casts Fireball! {defender} takes **{dmg}HP** damage.").format(
                                attacker=attacker["user"],
                                defender=defender["user"],
                                dmg=dmg,
                            )
                        else:
                            # Regular attack
                            dmg = (
                                    attacker["damage"] + Decimal(random.randint(0, 100)) - defender["armor"]
                            )
                            dmg = max(dmg, 1)
                            defender["hp"] -= dmg

                            # Initialize message
                            message = _("{attacker} attacks! {defender} takes **{dmg}HP** damage.").format(
                                attacker=attacker["user"],
                                defender=defender["user"],
                                dmg=dmg,
                            )
                    else:
                        # Regular attack for non-Mage
                        dmg = (
                                attacker["damage"] + Decimal(random.randint(0, 100)) - defender["armor"]
                        )
                        dmg = max(dmg, 1)
                        defender["hp"] -= dmg

                        # Initialize message
                        message = _("{attacker} attacks! {defender} takes **{dmg}HP** damage.").format(
                            attacker=attacker["user"],
                            defender=defender["user"],
                            dmg=dmg,
                        )

                    # Check if defender is defeated
                    if defender["hp"] <= 0:
                        # Handle cheat death if defender is the player
                        if defender["user"] == ctx.author:
                            chance = authorchance
                            random_number = random.randint(1, 100)
                            if not cheated:
                                if random_number <= chance:
                                    defender["hp"] = 75
                                    cheated = True
                                    message += _(" {defender} cheats death and survives with 75HP!").format(
                                        defender=defender["user"]
                                    )
                                else:
                                    defender["hp"] = 0
                                    message += _(" {defender} is defeated!").format(
                                        defender=defender["user"]
                                    )
                            else:
                                defender["hp"] = 0
                                message += _(" {defender} is defeated!").format(
                                    defender=defender["user"]
                                )
                        else:
                            defender["hp"] = 0
                            message += _(" {defender} is defeated!").format(
                                defender=defender["user"]
                            )
                    else:
                        # Handle lifesteal if applicable
                        if attacker["user"] == ctx.author:
                            if lifestealauth != 0:
                                lifesteal_percentage = Decimal(lifestealauth) / Decimal(100)
                                heal = lifesteal_percentage * Decimal(dmg)
                                attacker["hp"] += heal.quantize(Decimal('0.00'), rounding=ROUND_HALF_UP)
                                if attacker["hp"] > max_hp_limit:
                                    attacker["hp"] = max_hp_limit
                                message += _(" Lifesteals: **{heal}HP**").format(heal=heal)
                        else:
                            if lifestealopp != 0:
                                lifesteal_percentage = Decimal(lifestealopp) / Decimal(100)
                                heal = lifesteal_percentage * Decimal(dmg)
                                attacker["hp"] += heal.quantize(Decimal('0.00'), rounding=ROUND_HALF_UP)
                                if attacker["hp"] > max_hp_limit:
                                    attacker["hp"] = max_hp_limit
                                message += _(" Lifesteals: **{heal}HP**").format(heal=heal)
                else:
                    # Attacker tripped and took damage
                    dmg = Decimal('10.000')
                    attacker["hp"] -= dmg
                    attacker["hp"] = max(attacker["hp"], 0)
                    message = _("{attacker} tripped and took **{dmg}HP** damage. Bad luck!").format(
                        attacker=attacker["user"],
                        dmg=dmg,
                    )

                # Append message to battle log
                battle_log.append(
                    (
                        battle_log[-1][0] + 1,
                        message,
                    )
                )

                # Update the embed with fixed positions
                embed = discord.Embed(
                    description=_("{p1} {emoji1} - {hp1} HP left\n{p2} {emoji2} - {hp2} HP left").format(
                        p1=players[0]["user"],
                        hp1=players[0]["hp"],
                        p2=players[1]["user"],
                        hp2=players[1]["hp"],
                        emoji1=emoji_for_element,
                        emoji2=emoji_for_elementenemy,
                    ),
                    color=self.bot.config.game.primary_colour,
                )

                for line in battle_log:
                    embed.add_field(
                        name=_("Action #{number}").format(number=line[0]), value=line[1]
                    )

                await log_message.edit(embed=embed)
                await asyncio.sleep(4)

                # Check if defender is defeated and break if so
                if defender["hp"] <= 0:
                    break  # Battle ends

                # Swap attacker for the next turn
                attacker_is_player = not attacker_is_player

            # Determine the winner
            players = sorted(players, key=lambda x: x["hp"])
            winner = players[1]["user"]
            loser = players[0]["user"]

        except Exception as e:
            import traceback
            error_message = f"An error occurred during the battle: {e}\n"
            error_message += traceback.format_exc()
            await ctx.send(error_message)
            print(error_message)

        # Update database and send final message
        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                'UPDATE profile SET "money"="money"+$1, "pvpwins"="pvpwins"+1 WHERE'
                ' "user"=$2;',
                money * 2,
                winner.id,
            )
            await self.bot.log_transaction(
                ctx,
                from_=loser.id,
                to=winner.id,
                subject="RaidBattle Bet",
                data={"Gold": money},
                conn=conn,
            )
        await ctx.send(
            _("{p1} won the raidbattle vs {p2}! Congratulations!").format(
                p1=winner.mention, p2=loser.mention
            )
        )

    @has_char()
    @user_cooldown(600)
    @commands.command(brief=_("Battle against a player (active)"))
    @locale_doc
    async def activebattle(
            self, ctx, money: IntGreaterThan(-1) = 0, enemy: discord.Member = None
    ):
        _(
            """`[money]` - A whole number that can be 0 or greater; defaults to 0
            `[enemy]` - A user who has a profile; defaults to anyone

            Fight against another player while betting money.
            To decide players' stats, their items, race and class bonuses are evaluated.

            The money is removed from both players at the start of the battle. Once a winner has been decided, they will receive their money, plus the enemy's money.
            The battle takes place in rounds. Each round, both players have to choose their move using the reactions.
            Players can attack (⚔️), defend (🛡️) or recover HP (❤️).

            The battle ends if one player's HP drops to 0 (winner decided), or a player does not move (forfeit).
            In case of a forfeit, neither of the players will get their money back.

            The battle's winner will receive a PvP win, which shows on their profile.
            (This command has a cooldown of 10 minutes.)"""
        )

        if enemy == ctx.author:
            return await ctx.send(_("You can't battle yourself."))
        if ctx.character_data["money"] < money:
            return await ctx.send(_("You are too poor."))

        await self.bot.pool.execute(
            'UPDATE profile SET "money"="money"-$1 WHERE "user"=$2;',
            money,
            ctx.author.id,
        )

        if not enemy:
            text = _(
                "{author} seeks an active battle! The price is **${money}**."
            ).format(author=ctx.author.mention, money=money)
        else:
            text = _(
                "{author} seeks an active battle with {enemy}! The price is **${money}**."
            ).format(author=ctx.author.mention, enemy=enemy.mention, money=money)

        async def check(user: discord.User) -> bool:
            return await has_money(self.bot, user.id, money)

        future = asyncio.Future()
        view = SingleJoinView(
            future,
            Button(
                style=ButtonStyle.primary,
                label=_("Join the activebattle!"),
                emoji="\U00002694",
            ),
            allowed=enemy,
            prohibited=ctx.author,
            timeout=60,
            check=check,
            check_fail_message=_(
                "You don't have enough money to join the activebattle."
            ),
        )

        await ctx.send(text, view=view)

        try:
            enemy_ = await future
        except asyncio.TimeoutError:
            await self.bot.reset_cooldown(ctx)
            await self.bot.pool.execute(
                'UPDATE profile SET "money"="money"+$1 WHERE "user"=$2;',
                money,
                ctx.author.id,
            )
            return await ctx.send(
                _("Noone wanted to join your activebattle, {author}!").format(
                    author=ctx.author.mention
                )
            )

        players = {
            ctx.author: {
                "hp": 0,
                "damage": 0,
                "defense": 0,
                "lastmove": "",
                "action": None,
            },
            enemy_: {
                "hp": 0,
                "damage": 0,
                "defense": 0,
                "lastmove": "",
                "action": None,
            },
        }

        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                'UPDATE profile SET "money"="money"-$1 WHERE "user"=$2;',
                money,
                enemy_.id,
            )

            for p in players:
                classes = [
                    class_from_string(i)
                    for i in await conn.fetchval(
                        'SELECT class FROM profile WHERE "user"=$1;', p.id
                    )
                ]
                if any(c.in_class_line(Ranger) for c in classes if c):
                    players[p]["hp"] = 120
                else:
                    players[p]["hp"] = 100

                attack, defense = await self.bot.get_damage_armor_for(p, conn=conn)
                players[p]["damage"] = int(attack)
                players[p]["defense"] = int(defense)

        moves = {
            "\U00002694": "attack",
            "\U0001f6e1": "defend",
            "\U00002764": "recover",
        }

        msg = await ctx.send(
            _("Battle {p1} vs {p2}").format(p1=ctx.author.mention, p2=enemy_.mention),
            embed=discord.Embed(
                title=_("Let the battle begin!"),
                color=self.bot.config.game.primary_colour,
            ),
        )

        def is_valid_move(r, u):
            return str(r.emoji) in moves and u in players and r.message.id == msg.id

        for emoji in moves:
            await msg.add_reaction(emoji)

        while players[ctx.author]["hp"] > 0 and players[enemy_]["hp"] > 0:
            await msg.edit(
                embed=discord.Embed(
                    description=_(
                        "{prevaction}\n{player1}: **{hp1}** HP\n{player2}: **{hp2}**"
                        " HP\nReact to play."
                    ).format(
                        prevaction="\n".join([i["lastmove"] for i in players.values()]),
                        player1=ctx.author.mention,
                        player2=enemy_.mention,
                        hp1=players[ctx.author]["hp"],
                        hp2=players[enemy_]["hp"],
                    )
                )
            )
            players[ctx.author]["action"], players[enemy_]["action"] = None, None
            players[ctx.author]["lastmove"], players[enemy_]["lastmove"] = (
                _("{user} does nothing...").format(user=ctx.author.mention),
                _("{user} does nothing...").format(user=enemy_.mention),
            )

            while (not players[ctx.author]["action"]) or (
                    not players[enemy_]["action"]
            ):
                try:
                    r, u = await self.bot.wait_for(
                        "reaction_add", timeout=30, check=is_valid_move
                    )
                    try:
                        await msg.remove_reaction(r.emoji, u)
                    except discord.Forbidden:
                        pass
                except asyncio.TimeoutError:
                    await self.bot.reset_cooldown(ctx)
                    await self.bot.pool.execute(
                        'UPDATE profile SET "money"="money"+$1 WHERE "user"=$2 or "user"=$3;',
                        money,
                        ctx.author.id,
                        enemy_.id,
                    )
                    return await ctx.send(
                        _("Someone refused to move. Activebattle stopped.")
                    )
                if not players[u]["action"]:
                    players[u]["action"] = moves[str(r.emoji)]
                else:
                    playerlist = list(players.keys())
                    await ctx.send(
                        _(
                            "{user}, you already moved! Waiting for {other}'s move..."
                        ).format(
                            user=u.mention,
                            other=playerlist[1 - playerlist.index(u)].mention,
                        )
                    )
            plz = list(players.keys())
            for idx, user in enumerate(plz):
                other = plz[1 - idx]
                if players[user]["action"] == "recover":
                    heal_hp = round(players[user]["damage"] * 0.25) or 1
                    players[user]["hp"] += heal_hp
                    players[user]["lastmove"] = _(
                        "{user} healed themselves for **{hp} HP**."
                    ).format(user=user.mention, hp=heal_hp)
                elif (
                        players[user]["action"] == "attack"
                        and players[other]["action"] != "defend"
                ):
                    eff = random.choice(
                        [
                            players[user]["damage"],
                            int(players[user]["damage"] * 0.5),
                            int(players[user]["damage"] * 0.2),
                            int(players[user]["damage"] * 0.8),
                        ]
                    )
                    players[other]["hp"] -= eff
                    players[user]["lastmove"] = _(
                        "{user} hit {enemy} for **{eff}** damage."
                    ).format(user=user.mention, enemy=other.mention, eff=eff)
                elif (
                        players[user]["action"] == "attack"
                        and players[other]["action"] == "defend"
                ):
                    eff = random.choice(
                        [
                            int(players[user]["damage"]),
                            int(players[user]["damage"] * 0.5),
                            int(players[user]["damage"] * 0.2),
                            int(players[user]["damage"] * 0.8),
                        ]
                    )
                    eff2 = random.choice(
                        [
                            int(players[other]["defense"]),
                            int(players[other]["defense"] * 0.5),
                            int(players[other]["defense"] * 0.2),
                            int(players[other]["defense"] * 0.8),
                        ]
                    )
                    if eff - eff2 > 0:
                        players[other]["hp"] -= eff - eff2
                        players[user]["lastmove"] = _(
                            "{user} hit {enemy} for **{eff}** damage."
                        ).format(user=user.mention, enemy=other.mention, eff=eff - eff2)
                        players[other]["lastmove"] = _(
                            "{enemy} tried to defend, but failed.".format(
                                enemy=other.mention
                            )
                        )

                    else:
                        players[user]["lastmove"] = _(
                            "{user}'s attack on {enemy} failed!"
                        ).format(user=user.mention, enemy=other.mention)
                        players[other]["lastmove"] = _(
                            "{enemy} blocked {user}'s attack.".format(
                                enemy=other.mention, user=user.mention
                            )
                        )
                elif players[user]["action"] == players[other]["action"] == "defend":
                    players[ctx.author]["lastmove"] = _("You both tried to defend.")
                    players[enemy_]["lastmove"] = _("It was not very effective...")

        if players[ctx.author]["hp"] <= 0 and players[enemy_]["hp"] <= 0:
            await self.bot.pool.execute(
                'UPDATE profile SET "money"="money"+$1 WHERE "user"=$2 or "user"=$3;',
                money,
                ctx.author.id,
                enemy_.id,
            )
            return await ctx.send(_("You both died!"))
        if players[ctx.author]["hp"] > players[enemy_]["hp"]:
            winner, looser = ctx.author, enemy_
        else:
            looser, winner = ctx.author, enemy_
        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                'UPDATE profile SET "pvpwins"="pvpwins"+1, "money"="money"+$1 WHERE'
                ' "user"=$2;',
                money * 2,
                winner.id,
            )
            await self.bot.log_transaction(
                ctx,
                from_=looser.id,
                to=winner.id,
                subject="Active Battle Bet",
                data={"Gold": money},
                conn=conn,
            )
        await msg.edit(
            embed=discord.Embed(
                description=_(
                    "{prevaction}\n{player1}: **{hp1}** HP\n{player2}: **{hp2}**"
                    " HP\nReact to play."
                ).format(
                    prevaction="\n".join([i["lastmove"] for i in players.values()]),
                    player1=ctx.author.mention,
                    player2=enemy_.mention,
                    hp1=players[ctx.author]["hp"],
                    hp2=players[enemy_]["hp"],
                )
            )
        )
        await ctx.send(
            _("{winner} won the active battle vs {looser}! Congratulations!").format(
                winner=winner.mention,
                looser=looser.mention,
            )
        )


async def setup(bot):
    await bot.add_cog(Battles(bot))
