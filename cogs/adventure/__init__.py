"""
The IdleRPG Discord Bot
Copyright (C) 2018-2021 Diniboy and Gelbpunkt

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

from datetime import datetime, timedelta
from enum import Enum
from functools import partial
from typing import Literal

import discord

from discord.enums import ButtonStyle
from discord.ext import commands
from discord.interactions import Interaction
from discord.ui.button import Button

from classes.classes import Ritualist
from classes.classes import from_string as class_from_string
from classes.context import Context
from classes.converters import IntFromTo
from classes.enums import DonatorRank
from cogs.shard_communication import user_on_cooldown as user_cooldown
from utils import items
from utils import misc as rpgtools
from utils import random
import random as randomm
from utils.checks import has_adventure, has_char, has_no_adventure, is_class, is_gm
from utils.i18n import _, locale_doc, use_current_gettext
from utils.maze import Cell, Maze

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
    SantasHelper,
)

ADVENTURE_NAMES = {
    1: "Mystic Grove",
    2: "Rising Mist Bridge",
    3: "Moonlit Solitude",
    4: "Orcish Ambush",
    5: "Trials of Conviction",
    6: "Canyon of Flames",
    7: "Sentinel Spire",
    8: "Abyssal Sanctum",
    9: "Shadowmancer's Citadel",
    10: "Dragon's Bane: Arzagor's End",
    11: "Quest for Avalon's Blade",
    12: "Seekers of Lemuria",
    13: "Phoenix's Embrace",
    14: "Requiem of Shadows",
    15: "Abysswalker's Challenge",
    16: "Vecca's Legacy",
    17: "Gemstone Odyssey",
    18: "Shrek's Swamp",
    19: "Kord's Resurgence",
    20: "Arena of Endurance",
    21: "Quest for the Astral Relic",
    22: "Nocturnal Enigma",
    23: "Luminous Quest",
    24: "Web of Betrayal",
    25: "Realm of Indolence",
    26: "Forgotten Valley",
    27: "Temple of the Sirens",
    28: "Osiris' Judgment",
    29: "War God's Parley",
    30: "Divine Convergence",
    31: "Shadow Convergence",
    32: "Abyssal Titans",
    33: "Cursed Bloodmoon",
    34: "Pandemonium Rifts",
    35: "Dread Plague",
    36: "Apocalypse Eclipse",
    37: "Eldritch Horrors",
    38: "Crimson Pact",
    39: "Serpent's Dominion",
    40: "Chrono Reckoning",
    41: "Cursed Ascendancy",
    42: "Elder Eclipse",
    43: "Netherstorm Siege",
    44: "Ragnarok's Awakening",
    45: "Abyssal Inferno",
    46: "Eclipse of Oblivion",
    47: "Voidwalker's Dominion",
    48: "Doomsday Eclipse",
    49: "Worldbreaker Cataclysm",
    50: "Elder God's Reckoning",
    51: "Cataclysmic Eruption",
    52: "Abyssal Cataclysm",
    53: "Infernal Collapse",
    54: "Titan's Wrath",
    55: "Demonic Ruination",
    56: "Armageddon's Echo",
    57: "Cosmic Decay",
    58: "Hellfire Conflagration",
    59: "Chaos Ascendant",
    60: "Realm's End",
    61: "Pestilent Apocalypse",
    62: "Void Annihilation",
    63: "Darkstar Convergence",
    64: "Solar Destruction",
    65: "Endless Nightfall",
    66: "Pandora's Fury",
    67: "Eternal Dread",
    68: "Blood Moon Despair",
    69: "Ruins of Despair",
    70: "Wyrm's Cataclysm",
    71: "Harbinger of Doom",
    72: "Annihilator's Onslaught",
    73: "Infinite Void",
    74: "Endbringer's Wrath",
    75: "Calamity's Dawn",
    76: "Eldritch Devastation",
    77: "Doom Herald's Reign",
    78: "Hellgate Incursion",
    79: "Maelstrom's Core",
    80: "Dark Realm's Cataclysm",
    81: "Chthonic End",
    82: "Cosmic Ruin",
    83: "Endless Oblivion",
    84: "Eternal Oblivion",
    85: "Demon King's Reign",
    86: "Soulfire Cataclysm",
    87: "Abyssal Ruination",
    88: "Eclipse of Despair",
    89: "Nightmare's End",
    90: "Ragnarok's Fall",
    91: "Hellstorm's Wrath",
    92: "Doomsday's Demise",
    93: "Oblivion's Maw",
    94: "Netherworld Collapse",
    95: "Eldritch Cataclysm",
    96: "Dread Overlord's Fury",
    97: "Infernal Apocalypse",
    98: "Darkstar's End",
    99: "World's End Catastrophe",
    100: "End of All Things",

}

DIRECTION = Literal["n", "e", "s", "w"]
ALL_DIRECTIONS: set[DIRECTION] = {"n", "e", "s", "w"}


class ActiveAdventureAction(Enum):
    MoveNorth = 0
    MoveEast = 1
    MoveSouth = 2
    MoveWest = 3

    AttackEnemy = 4
    Defend = 5
    Recover = 6


class ActiveAdventureDirectionView(discord.ui.View):
    def __init__(
            self,
            user: discord.User,
            future: asyncio.Future[ActiveAdventureAction],
            possible_actions: set[ActiveAdventureAction],
            *args,
            **kwargs,
    ):
        super().__init__(*args, **kwargs)

        self.user = user
        self.future = future

        north = Button(
            style=ButtonStyle.primary,
            label=_("North"),
            disabled=ActiveAdventureAction.MoveNorth not in possible_actions,
            emoji="\U00002b06",
            row=0,
        )
        east = Button(
            style=ButtonStyle.primary,
            label=_("East"),
            disabled=ActiveAdventureAction.MoveEast not in possible_actions,
            emoji="\U000027a1",
            row=0,
        )
        south = Button(
            style=ButtonStyle.primary,
            label=_("South"),
            disabled=ActiveAdventureAction.MoveSouth not in possible_actions,
            emoji="\U00002b07",
            row=0,
        )
        west = Button(
            style=ButtonStyle.primary,
            label=_("West"),
            disabled=ActiveAdventureAction.MoveWest not in possible_actions,
            emoji="\U00002b05",
            row=0,
        )

        attack = Button(
            style=ButtonStyle.secondary,
            label=_("Attack"),
            disabled=ActiveAdventureAction.AttackEnemy not in possible_actions,
            emoji="\U00002694",
            row=1,
        )
        defend = Button(
            style=ButtonStyle.secondary,
            label=_("Defend"),
            disabled=ActiveAdventureAction.Defend not in possible_actions,
            emoji="\U0001f6e1",
            row=1,
        )
        recover = Button(
            style=ButtonStyle.secondary,
            label=_("Recover"),
            disabled=ActiveAdventureAction.Recover not in possible_actions,
            emoji="\U00002764",
            row=1,
        )

        north.callback = partial(self.handle, action=ActiveAdventureAction.MoveNorth)
        east.callback = partial(self.handle, action=ActiveAdventureAction.MoveEast)
        south.callback = partial(self.handle, action=ActiveAdventureAction.MoveSouth)
        west.callback = partial(self.handle, action=ActiveAdventureAction.MoveWest)
        attack.callback = partial(self.handle, action=ActiveAdventureAction.AttackEnemy)
        defend.callback = partial(self.handle, action=ActiveAdventureAction.Defend)
        recover.callback = partial(self.handle, action=ActiveAdventureAction.Recover)

        self.add_item(north)
        self.add_item(east)
        self.add_item(south)
        self.add_item(west)
        self.add_item(attack)
        self.add_item(defend)
        self.add_item(recover)

    async def interaction_check(self, interaction: Interaction) -> bool:
        return interaction.user.id == self.user.id

    async def handle(
            self, interaction: Interaction, action: ActiveAdventureAction
    ) -> None:
        self.stop()
        self.future.set_result(action)
        msg = await interaction.response.defer()
        await msg.edit(content="New content")

    async def on_timeout(self) -> None:
        self.future.set_exception(asyncio.TimeoutError())


class ActiveAdventure:
    def __init__(
            self, ctx: Context, attack: int, defense: int, width: int = 15, height: int = 15
    ) -> None:
        self.ctx = ctx

        self.original_hp = attack * 100
        self.original_enemy_hp = attack * 10

        self.width = width
        self.height = height
        self.maze = Maze.generate(width=width, height=height)
        self.player_x = 0
        self.player_y = 0
        self.attack = attack
        self.defense = defense
        self.hp = attack * 100

        self.heal_hp = round(attack * 0.25) or 1
        self.min_dmg = round(attack * 0.5)
        self.max_dmg = round(attack * 1.5)

        self.enemy_hp: int | None = None

        self.message: discord.Message | None = None
        self.status_text: str | None = _("The active adventure has started.")

    def move(self, action: ActiveAdventureAction) -> None:
        if action == ActiveAdventureAction.MoveNorth:
            self.player_y -= 1
        elif action == ActiveAdventureAction.MoveEast:
            self.player_x += 1
        elif action == ActiveAdventureAction.MoveSouth:
            self.player_y += 1
        elif action == ActiveAdventureAction.MoveWest:
            self.player_x -= 1

        self.maze.player = (self.player_x, self.player_y)

        if self.enemy_hp:
            status_1 = None
            status_2 = None

            enemy_action = random.choice(
                [
                    ActiveAdventureAction.AttackEnemy,
                    ActiveAdventureAction.Defend,
                    ActiveAdventureAction.Recover,
                ]
            )

            if enemy_action == ActiveAdventureAction.Recover:
                self.enemy_hp += self.heal_hp
                self.enemy_hp = (
                    self.original_enemy_hp
                    if self.enemy_hp > self.original_enemy_hp
                    else self.enemy_hp
                )
                status_1 = ("The Enemy healed themselves for {hp} HP").format(
                    hp=self.heal_hp
                )

            if action == ActiveAdventureAction.Recover:
                self.hp += self.heal_hp
                self.hp = self.original_hp if self.hp > self.original_hp else self.hp
                status_2 = _("You healed yourself for {hp} HP").format(hp=self.heal_hp)

            if (
                    enemy_action == ActiveAdventureAction.AttackEnemy
                    and action == ActiveAdventureAction.Defend
            ) or (
                    enemy_action == ActiveAdventureAction.Defend
                    and action == ActiveAdventureAction.AttackEnemy
            ):
                status_1 = _("Attack blocked.")
            else:
                if enemy_action == ActiveAdventureAction.AttackEnemy:
                    eff = random.randint(self.min_dmg, self.max_dmg)
                    self.hp -= eff
                    status_1 = _("The Enemy hit you for {dmg} damage").format(dmg=eff)
                if action == ActiveAdventureAction.AttackEnemy:
                    self.enemy_hp -= self.attack
                    status_2 = _("You hit the enemy for {dmg} damage").format(
                        dmg=self.attack
                    )

            if status_1 and status_2:
                self.status_text = f"{status_1}\n{status_2}"
            elif status_1:
                self.status_text = status_1
            elif status_2:
                self.status_text = status_2

    async def reward(self, treasure: bool = True) -> int:
        val = self.attack + self.defense
        if treasure:
            money = random.randint(1200, val * 80)
        else:
            # The adventure end reward
            money = random.randint(val * 80, val * 215)
        async with self.ctx.bot.pool.acquire() as conn:
            await conn.execute(
                'UPDATE profile SET "money"="money"+$1 WHERE "user"=$2;',
                money,
                self.ctx.author.id,
            )
            await self.ctx.bot.log_transaction(
                self.ctx,
                from_=1,
                to=self.ctx.author.id,
                subject="AA Reward",
                data={"Gold": money},
                conn=conn,
            )

        return money

    async def run(self) -> None:
        while not self.is_at_exit and self.hp > 0:
            try:
                move = await self.get_move()
            except asyncio.TimeoutError:
                return await self.message.edit(content=_("Timed out."))

            # Reset status message since it'll change now
            self.status_text = None

            self.move(move)

            if self.enemy_hp is not None and self.enemy_hp <= 0:
                self.status_text = _("You defeated the enemy.")
                self.enemy_hp = None
                self.cell.enemy = False

            # Handle special cases of cells

            if self.cell.trap:
                damage = random.randint(self.original_hp // 10, self.original_hp // 8)
                self.hp -= damage
                self.status_text = _(
                    "You stepped on a trap and took {damage} damage!"
                ).format(damage=damage)
                self.cell.trap = False
            elif self.cell.treasure:
                money_rewarded = await self.reward()
                self.status_text = _(
                    "You found a treasure with **${money}** inside!"
                ).format(money=money_rewarded)
                self.cell.treasure = False
            elif self.cell.enemy and self.enemy_hp is None:
                self.enemy_hp = self.original_enemy_hp

        if self.hp <= 0:
            await self.message.edit(content=_("You died."))
            return

        money_rewarded = await self.reward(treasure=False)

        await self.message.edit(
            content=_(
                "You have reached the exit and were rewarded **${money}** for getting"
                " out!"
            ).format(money=money_rewarded),
            view=None,
        )

    @property
    def player_hp_bar(self) -> str:
        fields = int(self.hp / self.original_hp * 10)
        return f"[{'▯' * fields}{'▮' * (10 - fields)}]"

    @property
    def enemy_hp_bar(self) -> str:
        fields = int(self.enemy_hp / self.original_enemy_hp * 10)
        return f"[{'▯' * fields}{'▮' * (10 - fields)}]"

    async def get_move(self) -> ActiveAdventureAction:
        explanation_text = _("`@` - You, `!` - Enemy, `*` - Treasure")

        if self.enemy_hp is None:
            hp_text = _("You are on {hp} HP").format(hp=self.hp)

            if self.status_text is not None:
                text = f"{self.status_text}```\n{self.maze}\n```\n{explanation_text}\n{hp_text}"
            else:
                text = f"```\n{self.maze}\n```\n{explanation_text}\n{hp_text}"
        else:
            enemy = _("Enemy")
            hp = _("HP")
            fight_status = f"""```
{self.ctx.disp}
{"-" * len(self.ctx.disp)}
{self.player_hp_bar}  {self.hp} {hp}

{enemy}
{"-" * len(enemy)}
{self.enemy_hp_bar}  {self.enemy_hp} {hp}
```"""

            if self.status_text is not None:
                text = f"{self.status_text}```\n{self.maze}\n```\n{explanation_text}\n{fight_status}"
            else:
                text = f"```\n{self.maze}\n```\n{explanation_text}\n{fight_status}"

        possible = set()

        if self.enemy_hp is not None:
            possible.add(ActiveAdventureAction.AttackEnemy)
            possible.add(ActiveAdventureAction.Defend)
            possible.add(ActiveAdventureAction.Recover)
        else:
            free = self.free
            if "n" in free:
                possible.add(ActiveAdventureAction.MoveNorth)
            if "e" in free:
                possible.add(ActiveAdventureAction.MoveEast)
            if "s" in free:
                possible.add(ActiveAdventureAction.MoveSouth)
            if "w" in free:
                possible.add(ActiveAdventureAction.MoveWest)

        future = asyncio.Future()
        view = ActiveAdventureDirectionView(self.ctx.author, future, possible)

        if self.message:
            await self.message.edit(content=text, view=view)
        else:
            self.message = await self.ctx.send(content=text, view=view)

        return await future

    @property
    def free(self) -> set[DIRECTION]:
        return ALL_DIRECTIONS - self.cell.walls

    @property
    def is_at_exit(self) -> bool:
        return self.player_x == self.width - 1 and self.player_y == self.height - 1

    @property
    def cell(self) -> Cell:
        return self.maze[self.player_x, self.player_y]


class Adventure(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @has_char()
    @commands.command(
        aliases=["missions", "dungeons"], brief=_("Shows adventures and your chances")
    )
    @locale_doc
    async def adventures(self, ctx):
        damage, defense = await self.bot.get_damage_armor_for(ctx.author)
        level = rpgtools.xptolevel(ctx.character_data["xp"])
        luck_booster = await self.bot.get_booster(ctx.author, "luck")

        embeds = []
        levels_per_page = 10
        level_count = 1

        while level_count <= 100:
            embed = discord.Embed(title="Adventure Success Chances")
            for _ in range(levels_per_page):
                if level_count >= 101:
                    break
                success = rpgtools.calcchance(
                    damage,
                    defense,
                    level_count,
                    int(level),
                    ctx.character_data["luck"],
                    booster=luck_booster,
                    returnsuccess=False,
                )
                embed.add_field(name=f"Level {level_count}", value=f"Success Chance: {success}%", inline=False)
                level_count += 1
            embeds.append(embed)

        # Use your paginator to display the list of embeds
        await self.bot.paginator.Paginator(extras=embeds).paginate(ctx)

    @has_char()
    @has_no_adventure()
    @commands.command(
        aliases=["mission", "a"], brief=_("Sends your character on an adventure.")
    )
    @locale_doc
    async def adventure(self, ctx, adventure_number: IntFromTo(1, 100)):
        _(
            """`<adventure_number>` - a whole number from 1 to 100

            Send your character on an adventure with the difficulty `<adventure_number>`.
            The adventure will take `<adventure_number>` hours if no time booster is used, and half as long if a time booster is used.

            If you are in an alliance which owns a city with adventure buildings, your adventure time will be reduced by the adventure building level in %.

            Be sure to check `{prefix}status` to check how much time is left, or to check if you survived or died."""
        )
        if adventure_number > rpgtools.xptolevel(ctx.character_data["xp"]):
            return await ctx.send(
                _("You must be on level **{level}** to do this adventure.").format(
                    level=adventure_number
                )
             )
        time = timedelta(hours=adventure_number)

        if buildings := await self.bot.get_city_buildings(ctx.character_data["guild"]):
            time -= time * (buildings["adventure_building"] / 100)
        if user_rank := await self.bot.get_donator_rank(ctx.author.id):
            if user_rank >= DonatorRank.emerald:
                time = time * 0.75
            elif user_rank >= DonatorRank.gold:
                time = time * 0.9
            elif user_rank >= DonatorRank.silver:
                time = time * 0.95
        if await self.bot.get_booster(ctx.author, "time"):
            time = time / 2



        await self.bot.start_adventure(ctx.author, adventure_number, time)

        await ctx.send(
            _(
                "Successfully sent your character out on an adventure. Use"
                " `{prefix}status` to see the current status of the mission."
            ).format(prefix=ctx.clean_prefix)
        )

        async with self.bot.pool.acquire() as conn:
            remind_adv = await conn.fetchval(
                'SELECT "adventure_reminder" FROM user_settings WHERE "user"=$1;',
                ctx.author.id,
            )
            if remind_adv:
                subject = f"{adventure_number}"
                finish_time = datetime.utcnow() + time
                await self.bot.cogs["Scheduling"].create_reminder(
                    subject,
                    ctx,
                    finish_time,
                    type="adventure",
                    conn=conn,
                )

    @has_char()
    @user_cooldown(7200)
    @commands.command(aliases=["aa"], brief=_("Go out on an active adventure."))
    @locale_doc
    async def activeadventure(self, ctx):
        _(
            # xgettext: no-python-format
            """Active adventures will put you into a randomly generated maze. You will begin in the top left corner and your goal is to find the exit in the bottom right corner.
            You control your character with the arrow buttons below the message.

            You have a fixed amount of HP based on your items. The adventure ends when you find the exit or your HP drop to zero.
            You can lose HP by getting damaged by traps or enemies.

            The maze contains safe spaces and treasures but also traps and enemies.
            Each space has a 10% chance of being a trap. If a space does not have a trap, it has a 10% chance of having an enemy.
            Each maze has 5 treasure chests.

            Traps can damage you from 1/10 of your total HP to up to 1/8 of your total HP.
            Enemy damage is based on your own damage. During enemy fights, you can attack (⚔️), defend (🛡️) or recover HP (❤️)
            Treasure chests can have gold up to 25 times your attack + defense.

            If you reach the end, you will receive a special treasure with gold up to 100 times your attack + defense.

            (This command has a cooldown of 30 minutes)"""
        )
        if not await ctx.confirm(
                _(
                    "You are going to be in a labyrinth. There are enemies,"
                    " treasures and hidden traps. Reach the exit in the bottom right corner"
                    " for a huge extra bonus!\nAre you ready?\n\nTip: Use a silent channel"
                    " for this, you may want to read all the messages I will send."
                )
        ):
            return

        attack, defense = await self.bot.get_damage_armor_for(ctx.author)

        await ActiveAdventure(ctx, int(attack), int(defense), width=12, height=12).run()

    async def get_blessed_value(self, user_id):
        """Retrieve the blessed value from Redis or use default of 1."""
        value = await self.bot.redis.get(str(user_id))
        return float(value) if value else 1.0

    async def get_blessing_ttl(self, user_id):
        """Retrieve the TTL of a blessing from Redis."""
        ttl = await self.bot.redis.ttl(str(user_id))
        return ttl

    @has_char()
    @commands.command(aliases=["isblessed"], brief=_("check your bless"))
    @locale_doc
    async def checkbless(self, ctx, user: discord.Member = None):
        _(
            """Check the blessing value of a user. If no user is mentioned, check the command caller."""
        )

        # If no user is mentioned, check the command caller.
        if not user:
            user = ctx.author

        # Get the blessing value
        value = await self.get_blessed_value(user.id)
        ttl = await self.get_blessing_ttl(user.id)

        # Convert the TTL into hours and minutes
        hours, remainder = divmod(ttl, 3600)
        minutes, _ = divmod(remainder, 60)

        if value == 1:
            await ctx.send(f"{user.name} has no current blessing.")
        else:
            if ttl > 0:
                await ctx.send(
                    f"{user.name} is blessed with a value of {value} for the next {hours} hours and {minutes} minutes!")
            else:
                await ctx.send(f"{user.name} has no current blessing.")

    @is_class(Paladin)
    @has_char()
    @user_cooldown(86400)
    @commands.command(aliases=["bl"], brief=_("Blesses a User"))
    @locale_doc
    async def bless(self, ctx, blessed_user: discord.Member):
        _(
            """**[PALADINS ONLY]**
            
            This command allows Paladins to bestow blessings upon other users. When a user is blessed, they receive a multiplier that can grant bonus XP on adventures. The strength of the blessing is determined by the grade of the Paladin bestowing it.

            You can use the `$checkbless` command to see the current bless status of a user. This command will show if a user is blessed, the strength of their blessing, and the remaining duration of the blessing.

            Be cautious! You cannot bless yourself, and once you bless someone, the blessing remains active for 24 hours."""
        )
        try:
            """Bless a user by setting their blessing value in Redis."""

            grade = 0
            for class_ in ctx.character_data["class"]:
                c = class_from_string(class_)
                if c and c.in_class_line(Paladin):
                    grade = c.class_grade()
            BlessMultiplier = grade * 1 * 0.25 + 1

            # Check if the author is trying to bless themselves
            if ctx.author.id == blessed_user.id:
                await ctx.send("You cannot bless yourself!")
                return await self.bot.reset_cooldown(ctx)

            # Check if the user is already blessed
            current_bless_value = await self.bot.redis.get(str(blessed_user.id))
            if current_bless_value:
                await ctx.send(f"{blessed_user.mention} is already blessed!")
                return await self.bot.reset_cooldown(ctx)

            # Ask for confirmation
            # Create a visually appealing embed for the confirmation message
            embed = discord.Embed(
                title="🌟 Bless Confirmation 🌟",
                description=f"{blessed_user.mention}, {ctx.author.mention} wants to bestow a blessing upon you. Do you accept?",
                color=0x4CAF50
            )
            embed.set_thumbnail(
                url="https://i.ibb.co/cDH4MMT/bless-spell-baldursgate3-wiki-guide-150px-2.png")
            embed.add_field(name="User", value=blessed_user.mention, inline=True)
            embed.add_field(name="Blessing Value", value=BlessMultiplier, inline=True)
            embed.set_footer(text=f"Requested by {ctx.author}",
                             icon_url="https://i.ibb.co/cDH4MMT/bless-spell-baldursgate3-wiki-guide-150px-2.png")
            embed.timestamp = ctx.message.created_at

            embed_msg = await ctx.send(embed=embed)

            # Ask the user to confirm by reacting to the message
            confirmation_prompt = f"{blessed_user.mention} Please react below to confirm or decline."
            try:
                if not await ctx.confirm(message=confirmation_prompt, user=blessed_user):
                    await embed_msg.delete()
                    await ctx.send("Blessing cancelled.")
                    await self.bot.reset_cooldown(ctx)
                    return
            except Exception as e:
                await self.bot.reset_cooldown(ctx)
                await embed_msg.delete()

            # If confirmation received, proceed with the rest of the code
            await embed_msg.delete()  # delete the embed message
            if current_bless_value:
                await ctx.send(f"{blessed_user.mention} is already blessed!")
                return await self.bot.reset_cooldown(ctx)
            # Set the value in Redis with a TTL of 24 hours (86400 seconds)
            await self.bot.redis.setex(str(blessed_user.id), 86400, BlessMultiplier)

            # Send a confirmation message
            await ctx.send(f"{blessed_user.mention} has been blessed by {ctx.author.mention}!")

        except Exception as e:
            await self.bot.reset_cooldown(ctx)
            await ctx.send("Blessing timed out.")

    @has_char()
    @has_adventure()
    @commands.command(aliases=["s"], brief=_("Checks your adventure status."))
    @locale_doc
    async def status(self, ctx):
        _(
            """Checks the remaining time of your adventures, or if you survived or died. Your chance is checked here, not in `{prefix}adventure`.
            Your chances are determined by your equipped items, race and class bonuses, your level, God-given luck and active luck boosters.

            If you are in an alliance which owns a city with an adventure building, your chance will be increased by 1% per building level.

            If you survive on your adventure, you will receive gold up to the adventure number times 60, XP up to 500 times the adventure number and either a loot or gear item.
            The chance of loot is dependent on the adventure number and whether you use the Ritualist class, [check our wiki](https://wiki.idlerpg.xyz/index.php?title=Loot) for the exact chances.

            God given luck affects the amount of gold and the gear items' damage/defense and value.

            If you are in a guild, its guild bank will receive 10% of the amount of gold extra.
            If you are married, your partner will receive a portion of your gold extra as well, [check the wiki](https://wiki.idlerpg.xyz/index.php?title=Family#Adventure_Bonus) for the exact portion."""
        )
        try:
            num, time, done = ctx.adventure_data

            if not done:
                # TODO: Embeds ftw
                return await ctx.send(
                    embed=discord.Embed(
                        title=_("Adventure Status"),
                        description=_(
                            "You are currently on an adventure with difficulty"
                            " **{difficulty}**.\nTime until it completes:"
                            " **{time_left}**\nAdventure name: **{adventure}**"
                        ).format(
                            difficulty=num,
                            time_left=time,
                            adventure=ADVENTURE_NAMES[num],
                        ),
                        colour=self.bot.config.game.primary_colour,
                    )
                )

            damage, armor = await self.bot.get_damage_armor_for(ctx.author)

            luck_booster = await self.bot.get_booster(ctx.author, "luck")
            current_level = int(rpgtools.xptolevel(ctx.character_data["xp"]))
            luck_multiply = ctx.character_data["luck"]
            if buildings := await self.bot.get_city_buildings(ctx.character_data["guild"]):
                bonus = buildings["adventure_building"]
            else:
                bonus = 0

            if current_level > 30:
                bonus = 5

            success = rpgtools.calcchance(
                damage,
                armor,
                num,
                current_level,
                luck_multiply,
                returnsuccess=True,
                booster=bool(luck_booster),
                bonus=bonus,
            )
            await self.bot.delete_adventure(ctx.author)

            if not success:
                await self.bot.pool.execute(
                    'UPDATE profile SET "deaths"="deaths"+1 WHERE "user"=$1;', ctx.author.id
                )
                return await ctx.send(
                    embed=discord.Embed(
                        title=_("Adventure Failed"),
                        description=_("You died on your mission. Try again!"),
                        colour=0xFF0000,
                    )
                )

            gold = round(random.randint(20 * num, 60 * num) * luck_multiply)

            if await self.bot.get_booster(ctx.author, "money"):
                gold = int(gold * 1.25)

            # Get the bless multiplier from Redis
            bless_multiplier = await self.bot.redis.get(str(ctx.author.id))
            if bless_multiplier:
                bless_multiplier = float(bless_multiplier)
            else:
                bless_multiplier = 1.0

            # Calculate XP with the blessing multiplier
            xp = round(random.randint(250 * num, 500 * num) * bless_multiplier)
            if current_level < 30:
                xp = xp * 1.25

            chance_of_loot = 5 if num == 1 else 5 + 1.5 * num

            classes = [class_from_string(c) for c in ctx.character_data["class"]]
            if any(c.in_class_line(Ritualist) for c in classes if c):
                chance_of_loot *= 2  # can be 100 in a 30

            async with self.bot.pool.acquire() as conn:
                if (random.randint(1, 1000)) > chance_of_loot * 10:
                    minstat = round(num * luck_multiply)
                    maxstat = round(5 + int(num * 1.5) * luck_multiply)

                    item = await self.bot.create_random_item(
                        minstat=(minstat if minstat > 0 else 1) if minstat < 35 else 35,
                        maxstat=(maxstat if maxstat > 0 else 1) if maxstat < 35 else 35,
                        minvalue=round(num * luck_multiply),
                        maxvalue=round(num * 50 * luck_multiply),
                        owner=ctx.author,
                        conn=conn,
                    )
                    storage_type = "inventory"

                else:
                    item = items.get_item()
                    await conn.execute(
                        'INSERT INTO loot ("name", "value", "user") VALUES ($1, $2, $3);',
                        item["name"],
                        item["value"],
                        ctx.author.id,
                    )
                    storage_type = "loot"

                if guild := ctx.character_data["guild"]:
                    await conn.execute(
                        'UPDATE guild SET "money"="money"+$1 WHERE "id"=$2;',
                        int(gold / 10),
                        guild,
                    )

                # EASTER
                # ---------------
                #eggs = int(num ** 1.2 * random.randint(3, 6))


                # Halloween
                # ---------------
                #bones = int(num ** 1.2 * random.randint(1, 8))

                await conn.execute(
                    'UPDATE profile SET "money"="money"+$1, "xp"="xp"+$2,'
                    ' "completed"="completed"+1 WHERE "user"=$3;',
                    gold,
                    xp,
                    ctx.author.id,
                )

                if partner := ctx.character_data["marriage"]:
                    await conn.execute(
                        'UPDATE profile SET "money"="money"+($1*(1+"lovescore"/1000000))'
                        ' WHERE "user"=$2;',
                        int(gold / 2),
                        partner,
                    )

                await self.bot.log_transaction(
                    ctx,
                    from_=1,
                    to=ctx.author.id,
                    subject="adventure",
                    data={
                        "Gold": gold,
                        "Item": item["name"],  # compare against loot names if necessary
                        "Value": item["value"],
                    },
                    conn=conn,
                )

                # float_snowflakes = randomm.uniform(num * 10, num * 25)
                # snowflakes = round(float_snowflakes)

                await ctx.send(
                    embed=discord.Embed(
                        title=_("Adventure Completed"),
                        description=_(
                            "You have completed your adventure and received **${gold}** as"
                            " well as a new item:\n**{item}** added to your"
                            " `{prefix}{storage_type}`\nType: **{type}**\n{stat}Value:"
                            " **{value}**\nExperience gained: **{xp}**."
                            #"\nEggs found: **{eggs}**"
                            # "\nSnowflakes gained: **{snowflakes}**."
                             #"\nBones gained: **{bones}**."
                        ).format(
                            gold=gold,
                            type=_("Loot item") if storage_type == "loot" else item["type"],
                            item=item["name"],
                            stat=""
                            if storage_type == "loot"
                            else _("Damage: **{damage}**\n").format(damage=item["damage"])
                            if item["damage"]
                            else _("Armor: **{armor}**\n").format(armor=item["armor"]),
                            value=item["value"],
                            prefix=ctx.clean_prefix,
                            storage_type=storage_type,
                            xp=xp,
                            #eggs=eggs,
                            #bones=bones,
                        ),
                        colour=0x00FF00,
                    )
                )

                # await conn.execute(
                # 'UPDATE profile SET "snowflakes"="snowflakes"+$1 WHERE "user"=$2',
                # snowflakes,
                # ctx.author.id,
                # )

                new_level = int(rpgtools.xptolevel(ctx.character_data["xp"] + xp))

                if current_level != new_level:
                    await self.bot.process_levelup(ctx, new_level, current_level)

        except Exception as e:
            await ctx.send(f"{e}")
            pass

    @has_char()
    @has_adventure()
    @commands.command(brief=_("Cancels your current adventure."))
    @locale_doc
    async def cancel(self, ctx):
        _(
            """Cancels your ongoing adventure and allows you to start a new one right away. You will not receive any rewards if you cancel your adventure."""
        )
        if not await ctx.confirm(
                _("Are you sure you want to cancel your current adventure?")
        ):
            return await ctx.send(
                _("Did not cancel your adventure. The journey continues...")
            )
        await self.bot.delete_adventure(ctx.author)

        id = await self.bot.pool.fetchval(
            'DELETE FROM reminders WHERE "user"=$1 AND "type"=$2 RETURNING "id";',
            ctx.author.id,
            "adventure",
        )

        if id is not None:
            await self.bot.cogs["Scheduling"].remove_timer(id)

        await ctx.send(
            _(
                "Canceled your mission. Use `{prefix}adventure [missionID]` to start a"
                " new one!"
            ).format(prefix=ctx.clean_prefix)
        )

    @has_char()
    @commands.command(brief=_("Show some adventure stats"))
    @locale_doc
    async def deaths(self, ctx):
        _(
            """Shows your overall adventure death and completed count, including your success rate."""
        )
        deaths, completed = (
            ctx.character_data["deaths"],
            ctx.character_data["completed"],
        )
        if (deaths + completed) != 0:
            rate = round(completed / (deaths + completed) * 100, 2)
        else:
            rate = 100
        await ctx.send(
            _(
                "Out of **{total}** adventures, you died **{deaths}** times and"
                " survived **{completed}** times, which is a success rate of"
                " **{rate}%**."
            ).format(
                total=deaths + completed, deaths=deaths, completed=completed, rate=rate
            )
        )


async def setup(bot):
    await bot.add_cog(Adventure(bot))
