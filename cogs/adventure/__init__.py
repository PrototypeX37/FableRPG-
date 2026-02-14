"""
The IdleRPG Discord Bot
Copyright (C) 2018-2021 Diniboy and Gelbpunkt
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

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from enum import Enum
from functools import partial
from time import time
from typing import Literal

import discord
from discord.enums import ButtonStyle
from discord.ext import commands
from discord.interactions import Interaction
from discord.ui.button import Button

from classes.classes import from_string as class_from_string
from classes.context import Context
from classes.converters import IntFromTo
from classes.enums import DonatorRank
from cogs.shard_communication import user_on_cooldown as user_cooldown
from utils import items
from utils import misc as rpgtools
from utils import random
from utils.checks import has_adventure, has_char, has_no_adventure, is_class
from utils.i18n import _, locale_doc
from utils.maze import Cell, Maze

from classes.classes import (
    Mage,
    Paladin,
    Paragon,
    Raider,
    Ranger,
    Ritualist,
    SantasHelper,
    Thief,
    Warrior,
)

# --------------------
# Constants
# --------------------

ADVENTURE_NAMES: dict[int, str] = {
    1: "Olive Grove of Athena",
    2: "Bridge of Echoes",
    3: "Moonlit Oracle",
    4: "Ambush at Thermopylae",
    5: "Trial of the Labors",
    6: "Canyon of Hephaestus",
    7: "Spire of Helios",
    8: "Sanctum of the Furies",
    9: "Citadel of Hades",
    10: "Slaying of the Nemean Lion",
    11: "Quest for Perseus' Blade",
    12: "Voyage to Lemnos",
    13: "Embrace of the Phoenix",
    14: "Dirge of the Sirens",
    15: "Challenge of the Hydra",
    16: "Legacy of Odysseus",
    17: "Odyssey of Gemstones",
    18: "Swamp of the Lamia",
    19: "Resurgence of Achilles",
    20: "Arena of Sparta",
    21: "Relic of the Parthenon",
    22: "Mystery of Delphi",
    23: "Quest of the Golden Fleece",
    24: "Web of Arachne",
    25: "Fields of Lethe",
    26: "Valley of Forgotten Heroes",
    27: "Temple of the Sirens",
    28: "Judgment of Minos",
    29: "Parley of Ares",
    30: "Convergence of the Olympians",
    31: "Shadow of Tartarus",
    32: "Wrath of the Titans",
    33: "Bloodmoon over Thebes",
    34: "Rift of Chaos",
    35: "Plague of Apollo",
    36: "Eclipse of Helios",
    37: "Horrors of Nyx",
    38: "Pact of the Erinyes",
    39: "Dominion of the Serpent",
    40: "Reckoning of Chronos",
    41: "Ascension of the Cursed",
    42: "Eclipse of Gaia",
    43: "Siege of the Underworld",
    44: "Awakening of Typhon",
    45: "Inferno of Tartarus",
    46: "Oblivion of Nyx",
    47: "Dominion of Hades",
    48: "Doom of Olympus",
    49: "Cataclysm of Titans",
    50: "Reckoning of the Gods",
    51: "Eruption of Etna",
    52: "Cataclysm of Chaos",
    53: "Collapse of Atlantis",
    54: "Wrath of Poseidon",
    55: "Ruination of Ares",
    56: "Echoes of Armageddon",
    57: "Decay of the Cosmos",
    58: "Conflagration of Hephaestus",
    59: "Ascendance of Chaos",
    60: "The Final Odyssey",
    61: "Apocalypse of Pestilence",
    62: "Annihilation of the Void",
    63: "Convergence of the Dark Star",
    64: "Destruction of the Sun Chariot",
    65: "Nightfall Eternal",
    66: "Fury of Pandora",
    67: "Endless Dread",
    68: "Bloodmoon over Athens",
    69: "Ruins of Troy",
    70: "Cataclysm of the Wyrm",
    71: "Herald of Doom",
    72: "Onslaught of the Annihilator",
    73: "The Infinite Void",
    74: "Wrath of the Endbringer",
    75: "Dawn of Calamity",
    76: "Devastation of the Elders",
    77: "Reign of the Doom Herald",
    78: "Incursion of the Hekatoncheires",
    79: "Core of the Maelstrom",
    80: "Cataclysm of the Dark Realm",
    81: "Chthonic End",
    82: "Ruin of the Cosmos",
    83: "Oblivion Without End",
    84: "Eternal Night of Nyx",
    85: "Reign of the Demon King",
    86: "Cataclysm of Soulfire",
    87: "Ruination of Tartarus",
    88: "Despair’s Eclipse",
    89: "End of Nightmares",
    90: "Fall of Ragnarok",
    91: "Wrath of the Hellstorm",
    92: "Demise of Doomsday",
    93: "Maw of Oblivion",
    94: "Collapse of the Netherworld",
    95: "Cataclysm of the Eldritch",
    96: "Fury of the Dread Overlord",
    97: "Apocalypse of Inferno",
    98: "End of the Dark Star",
    99: "Catastrophe of the World’s End",
    100: "The End of All Things",
}

BLESS_DURATION_SECONDS = 86400
BLESS_ACCEPT_TIMEOUT_SECONDS = 60
BLESS_MULTIPLIER_REDIS_KEY_PREFIX = "bless:"  # distinct from cooldown keys

DIRECTION = Literal["n", "e", "s", "w"]
ALL_DIRECTIONS: set[DIRECTION] = {"n", "e", "s", "w"}


# --------------------
# Active Adventure (UI)
# --------------------

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

    async def handle(self, interaction: Interaction, action: ActiveAdventureAction) -> None:
        # Important: defer() returns None; do not await edit on it.
        await interaction.response.defer()
        self.stop()
        if not self.future.done():
            self.future.set_result(action)

    async def on_timeout(self) -> None:
        if not self.future.done():
            self.future.set_exception(asyncio.TimeoutError())


class ActiveAdventure:
    def __init__(self, ctx: Context, attack: int, defense: int, width: int = 15, height: int = 15) -> None:
        self.ctx = ctx

        self.original_hp = max(attack * 100, 1)
        self.original_enemy_hp = max(attack * 10, 1)

        self.width = width
        self.height = height
        self.maze = Maze.generate(width=width, height=height)
        self.player_x = 0
        self.player_y = 0
        self.attack = attack
        self.defense = defense
        self.hp = self.original_hp

        self.heal_hp = round(attack * 0.25) or 1
        self.min_dmg = max(round(attack * 0.5), 1)
        self.max_dmg = max(round(attack * 1.5), 1)

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
                self.enemy_hp = min(self.enemy_hp, self.original_enemy_hp)
                status_1 = (_("The Enemy healed themselves for {hp} HP")).format(hp=self.heal_hp)

            if action == ActiveAdventureAction.Recover:
                self.hp += self.heal_hp
                self.hp = min(self.hp, self.original_hp)
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
                    status_2 = _("You hit the enemy for {dmg} damage").format(dmg=self.attack)

            if status_1 and status_2:
                self.status_text = f"{status_1}\n{status_2}"
            elif status_1:
                self.status_text = status_1
            elif status_2:
                self.status_text = status_2

    async def reward(self, treasure: bool = True) -> int:
        val = max(self.attack + self.defense, 1)
        if treasure:
            money = random.randint(1200, val * 80)
        else:
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
                if self.message:
                    return await self.message.edit(content=_('Timed out.'), view=None)
                return

            self.status_text = None
            self.move(move)

            if self.enemy_hp is not None and self.enemy_hp <= 0:
                self.status_text = _("You defeated the enemy.")
                self.enemy_hp = None
                self.cell.enemy = False

            if self.cell.trap:
                damage = random.randint(self.original_hp // 10, max(self.original_hp // 8, 1))
                self.hp -= damage
                self.status_text = _("You stepped on a trap and took {damage} damage!").format(damage=damage)
                self.cell.trap = False
            elif self.cell.treasure:
                money_rewarded = await self.reward(treasure=True)
                self.status_text = _("You found a treasure with **${money}** inside!").format(money=money_rewarded)
                self.cell.treasure = False
            elif self.cell.enemy and self.enemy_hp is None:
                self.enemy_hp = self.original_enemy_hp

        if self.hp <= 0:
            if self.message:
                await self.message.edit(content=_('You died.'), view=None)
            return

        money_rewarded = await self.reward(treasure=False)
        if self.message:
            await self.message.edit(
                content=_('You have reached the exit and were rewarded **${money}** for getting out!').format(
                    money=money_rewarded
                ),
                view=None,
            )

    @property
    def player_hp_bar(self) -> str:
        fields = int(self.hp / self.original_hp * 10)
        fields = max(0, min(fields, 10))
        return f"[{'▯' * fields}{'▮' * (10 - fields)}]"

    @property
    def enemy_hp_bar(self) -> str:
        if not self.enemy_hp:
            return "[▯▯▯▯▯▯▯▯▯▯]"
        fields = int(self.enemy_hp / self.original_enemy_hp * 10)
        fields = max(0, min(fields, 10))
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
{'-' * len(self.ctx.disp)}
{self.player_hp_bar}  {self.hp} {hp}

{enemy}
{'-' * len(enemy)}
{self.enemy_hp_bar}  {self.enemy_hp} {hp}
```"""

            if self.status_text is not None:
                text = f"{self.status_text}```\n{self.maze}\n```\n{explanation_text}\n{fight_status}"
            else:
                text = f"```\n{self.maze}\n```\n{explanation_text}\n{fight_status}"

        possible: set[ActiveAdventureAction] = set()

        if self.enemy_hp is not None:
            possible.update(
                {
                    ActiveAdventureAction.AttackEnemy,
                    ActiveAdventureAction.Defend,
                    ActiveAdventureAction.Recover,
                }
            )
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

        future: asyncio.Future[ActiveAdventureAction] = asyncio.Future()
        view = ActiveAdventureDirectionView(self.ctx.author, future, possible, timeout=120)

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


# --------------------
# Adventure Cog
# --------------------

class Adventure(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        # Your narrative table is huge; keep it as-is from your existing file.
        # For this rewrite, we keep the attribute name the same so the rest of your code works.
        self.ADVENTURE_EVENTS: dict[str, list[str]] = {}

    # ----------
    # Bless helpers
    # ----------

    @staticmethod
    def _bless_key(user_id: int) -> str:
        # Bless effect key (for XP multiplier on status)
        return f"{BLESS_MULTIPLIER_REDIS_KEY_PREFIX}{user_id}"

    async def get_blessed_value(self, user_id: int) -> float:
        val = await self.bot.redis.get(self._bless_key(user_id))
        if not val:
            return 1.0
        try:
            return float(val)
        except Exception:
            return 1.0

    async def get_blessing_ttl(self, user_id: int) -> int:
        ttl = await self.bot.redis.execute_command("TTL", self._bless_key(user_id))
        # Redis TTL returns:
        #  -2: key does not exist
        #  -1: exists without expire (shouldn't happen for bless)
        if ttl in (-2, -1) or ttl is None:
            return 0
        return int(ttl)

    # ----------
    # Commands
    # ----------

    @has_char()
    @commands.command(aliases=["missions", "dungeons"], brief=_("Shows adventures and your chances"))
    @locale_doc
    async def adventures(self, ctx: Context):
        # left as you had it (simulation-based), but note this is very expensive.
        await ctx.send(_("Calculating please wait..."))

        damage, defense = await self.bot.get_damage_armor_for(ctx.author)
        level = int(rpgtools.xptolevel(ctx.character_data["xp"]))
        luck_booster = await self.bot.get_booster(ctx.author, "luck")

        embeds: list[discord.Embed] = []
        levels_per_page = 10
        level_count = 1

        while level_count <= 100:
            embed = discord.Embed(
                title=_('Adventure Success Chances'),
                description=_(
                    'The success chance is calculated based on your stats, luck, and the difficulty of each adventure level.'
                ),
                colour=self.bot.config.game.primary_colour,
            )

            for _ in range(levels_per_page):
                if level_count > 100:
                    break

                success_count = 0
                simulations = 5000
                for _i in range(simulations):
                    if rpgtools.calcchance(
                        damage,
                        defense,
                        level_count,
                        level,
                        ctx.character_data["luck"],
                        booster=bool(luck_booster),
                        returnsuccess=True,
                    ):
                        success_count += 1

                true_success_rate = round((success_count / simulations) * 100)
                if success_count == simulations:
                    true_success_rate = 100
                elif success_count == 0:
                    true_success_rate = 0

                embed.add_field(
                    name=_('Level {n}').format(n=level_count),
                    value=_('**Success Chance:** {p}%').format(p=true_success_rate),
                    inline=False,
                )
                level_count += 1

            embeds.append(embed)

        await self.bot.paginator.Paginator(extras=embeds).paginate(ctx)

    @has_char()
    @has_no_adventure()
    @commands.command(aliases=["mission", "a"], brief=_("Sends your character on an adventure."))
    @locale_doc
    async def adventure(self, ctx: Context, adventure_number: IntFromTo(1, 100)):
        _(
            """`<adventure_number>` - a whole number from 1 to 100

            Send your character on an adventure with the difficulty `<adventure_number>`.
            The adventure will take `<adventure_number>` hours if no time booster is used, and half as long if a time booster is used.

            Be sure to check `{prefix}status` to check how much time is left, or to check if you survived or died."""
        )

        if adventure_number > rpgtools.xptolevel(ctx.character_data["xp"]):
            return await ctx.send(
                _("You must be on level **{level}** to do this adventure.").format(level=adventure_number)
            )

        adv_time = timedelta(hours=int(adventure_number))

        if buildings := await self.bot.get_city_buildings(ctx.character_data["guild"]):
            adv_time -= adv_time * (buildings["adventure_building"] / 100)

        if user_rank := await self.bot.get_donator_rank(ctx.author.id):
            if user_rank >= DonatorRank.emerald:
                adv_time = adv_time * 0.75
            elif user_rank >= DonatorRank.gold:
                adv_time = adv_time * 0.9
            elif user_rank >= DonatorRank.silver:
                adv_time = adv_time * 0.95

        if await self.bot.get_booster(ctx.author, "time"):
            adv_time = adv_time / 2

        await self.bot.start_adventure(ctx.author, adventure_number, adv_time)

        await ctx.send(
            _(
                "Successfully sent your character out on an adventure. Use `{prefix}status` to see the current status of the mission."
            ).format(prefix=ctx.clean_prefix)
        )

        async with self.bot.pool.acquire() as conn:
            remind_adv = await conn.fetchval(
                'SELECT "adventure_reminder" FROM user_settings WHERE "user"=$1;',
                ctx.author.id,
            )
            if remind_adv:
                subject = f"{adventure_number}"
                finish_time = datetime.now(timezone.utc) + adv_time
                await self.bot.cogs["Scheduling"].create_reminder(
                    subject,
                    ctx,
                    finish_time,
                    kind="adventure",
                    conn=conn,
                )

    @has_char()
    @commands.command(aliases=["isblessed"], brief=_("check your bless"))
    @locale_doc
    async def checkbless(self, ctx: Context, user: discord.Member | None = None):
        if not user:
            user = ctx.author

        value = await self.get_blessed_value(user.id)
        ttl = await self.get_blessing_ttl(user.id)

        if ttl <= 0 or value == 1.0:
            return await ctx.send(_("{name} has no current blessing.").format(name=user.display_name))

        hours, remainder = divmod(ttl, 3600)
        minutes, _ = divmod(remainder, 60)

        await ctx.send(
            _(
                "{name} is blessed with a value of {value} for the next {hours} hours and {minutes} minutes!"
            ).format(name=user.display_name, value=value, hours=hours, minutes=minutes)
        )

    # IMPORTANT CHANGE:
    # - We force the cooldown identifier to be exactly "bless" so Redis key becomes cd:<id>:bless.
    # - That makes it show up in your existing cooldown list (timers command) and be reset the same way.
    @is_class(Paladin)
    @has_char()
    @user_cooldown(BLESS_DURATION_SECONDS, identifier="bless")
    @commands.command(aliases=["bl"], brief=_("Blesses a User"))
    @locale_doc
    async def bless(self, ctx: Context, blessed_user: discord.Member):
        _(
            """**[PALADINS ONLY]**

            This command allows Paladins to bestow blessings upon other users.
            Blessing grants a multiplier to Adventure XP for 24 hours.

            You cannot bless yourself. If the target is already blessed, the command is cancelled.
            """
        )

        # Compute paladin grade multiplier
        grade = 0
        for class_ in ctx.character_data.get("class", []):
            c = class_from_string(class_)
            if c and c.in_class_line(Paladin):
                grade = max(grade, c.class_grade())
        bless_multiplier = grade * 0.25 + 1

        if ctx.author.id == blessed_user.id:
            await ctx.send(_("You cannot bless yourself!"))
            return await self.bot.reset_cooldown(ctx)

        # If already blessed, do not consume cooldown
        current_value = await self.bot.redis.get(self._bless_key(blessed_user.id))
        if current_value:
            await ctx.send(_("{user} is already blessed!").format(user=blessed_user.mention))
            return await self.bot.reset_cooldown(ctx)

        embed = discord.Embed(
            title=_('🌟 Bless Confirmation 🌟'),
            description=_(
                "{target}, {author} wants to bestow a blessing upon you. Do you accept?"
            ).format(target=blessed_user.mention, author=ctx.author.mention),
            color=0x4CAF50,
        )
        embed.set_thumbnail(url="https://i.ibb.co/cDH4MMT/bless-spell-baldursgate3-wiki-guide-150px-2.png")
        embed.add_field(name=_("User"), value=blessed_user.mention, inline=True)
        embed.add_field(name=_("Blessing Value"), value=str(bless_multiplier), inline=True)
        embed.set_footer(text=_("Requested by {name}").format(name=str(ctx.author)))
        embed.timestamp = ctx.message.created_at

        embed_msg = await ctx.send(embed=embed)

        # Confirm flow
        try:
            confirmation_prompt = _("{user} Please react below to confirm or decline.").format(user=blessed_user.mention)
            accepted = await ctx.confirm(message=confirmation_prompt, user=blessed_user, timeout=BLESS_ACCEPT_TIMEOUT_SECONDS)
            if not accepted:
                try:
                    await embed_msg.delete()
                except Exception:
                    pass
                await ctx.send(_("Blessing cancelled."))
                return await self.bot.reset_cooldown(ctx)
        except Exception:
            # Any timeout/error: don't consume cooldown
            try:
                await embed_msg.delete()
            except Exception:
                pass
            await self.bot.reset_cooldown(ctx)
            return await ctx.send(_("Blessing timed out."))

        try:
            await embed_msg.delete()
        except Exception:
            pass

        # Re-check after confirm (race-condition safe)
        current_value = await self.bot.redis.get(self._bless_key(blessed_user.id))
        if current_value:
            await ctx.send(_("{user} is already blessed!").format(user=blessed_user.mention))
            return await self.bot.reset_cooldown(ctx)

        # Store bless effect as its own key (separate from cooldown)
        await self.bot.redis.setex(self._bless_key(blessed_user.id), BLESS_DURATION_SECONDS, bless_multiplier)
        await ctx.send(_("{user} has been blessed by {by}!").format(user=blessed_user.mention, by=ctx.author.mention))

    def get_adventure_narrative(self, adventure_level: int, adventure_name: str, success: bool = True) -> str:
        # You can keep your existing narrative logic.
        tier = f"tier_{min(10, max(1, (adventure_level - 1) // 10 + 1))}"
        events = self.ADVENTURE_EVENTS.get(tier, [])
        if not events:
            return _("Your tale is unwritten...")

        num_events = 4 if success else 2
        # Guard: avoid sample larger than population
        num_events = min(num_events, len(events))
        chosen_events = random.sample(events, num_events)

        if success:
            return "• " + "\n\n• ".join(chosen_events) + "\n\n" + _("Against all odds, you emerged victorious!")
        return "• " + "\n\n• ".join(chosen_events) + "\n\n" + _("Unfortunately, you didn't survive what came next...")

    @has_char()
    @has_adventure()
    @commands.command(aliases=["s"], brief=_("Checks your adventure status."))
    @locale_doc
    async def status(self, ctx: Context):
        _(
            """Checks the remaining time of your adventures, or if you survived or died."""
        )

        num, remaining, done = ctx.adventure_data

        if not done:
            return await ctx.send(
                embed=discord.Embed(
                    title=_('Adventure Status'),
                    description=_(
                        "You are currently on an adventure with difficulty **{difficulty}**.\n"
                        "Time until it completes: **{time_left}**\n"
                        "Adventure name: **{adventure}**"
                    ).format(
                        difficulty=num,
                        time_left=remaining,
                        adventure=ADVENTURE_NAMES.get(num, str(num)),
                    ),
                    colour=self.bot.config.game.primary_colour,
                )
            )

        # Adventure finished: resolve and delete.
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
                'UPDATE profile SET "deaths"="deaths"+1 WHERE "user"=$1;',
                ctx.author.id,
            )
            return await ctx.send(
                embed=discord.Embed(
                    title=_('Adventure Failed'),
                    description=self.get_adventure_narrative(num, ADVENTURE_NAMES.get(num, str(num)), False),
                    colour=0xFF0000,
                )
            )

        gold = round(random.randint(20 * num, 60 * num) * luck_multiply)
        if await self.bot.get_booster(ctx.author, "money"):
            gold = int(gold * 1.25)

        bless_multiplier = await self.get_blessed_value(ctx.author.id)
        xp = round(random.randint(250 * num, 500 * num) * bless_multiplier)
        if current_level < 29:
            xp = xp * 1.25

        chance_of_loot = 5 if num == 1 else 5 + 1.5 * num

        classes = [class_from_string(c) for c in ctx.character_data["class"]]
        if any(c and c.in_class_line(Ritualist) for c in classes):
            chance_of_loot *= 2

        async with self.bot.pool.acquire() as conn:
            if random.randint(1, 1000) > chance_of_loot * 10:
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
                storage_type = "armory"
            else:
                item = items.get_item(adventure_level=num)
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

            await conn.execute(
                'UPDATE profile SET "money"="money"+$1, "xp"="xp"+$2, "completed"="completed"+1 WHERE "user"=$3;',
                gold,
                xp,
                ctx.author.id,
            )

            if partner := ctx.character_data["marriage"]:
                await conn.execute(
                    'UPDATE profile SET "money"="money"+($1*(1+"lovescore"/1000000)) WHERE "user"=$2;',
                    int(gold / 2),
                    partner,
                )

            await self.bot.log_transaction(
                ctx,
                from_=1,
                to=ctx.author.id,
                subject="adventure",
                data={"Gold": gold, "Item": item["name"], "Value": item["value"]},
                conn=conn,
            )

        await ctx.send(
            embed=discord.Embed(
                title=_('Adventure Completed'),
                description=_(
                    "**During your adventure:**\n"
                    "{narrative}\n\n"
                    "─────── Rewards ───────\n"
                    "🔱 Gold: **${gold}**\n"
                    "⚔️ Item: **{item}** (`{prefix}{storage_type}`)\n"
                    "✧ Type: **{type}**\n"
                    "{stat}"
                    "💎 Value: **{value}**\n"
                    "⭐ Experience: **{xp}**\n"
                ).format(
                    narrative=self.get_adventure_narrative(num, ADVENTURE_NAMES.get(num, str(num)), True),
                    gold=gold,
                    item=item["name"],
                    type=_("Loot item") if storage_type == "loot" else item.get("type", ""),
                    stat=""
                    if storage_type == "loot"
                    else (_("⚔️ Damage: **{damage}**\n").format(damage=item["damage"]) if item.get("damage") else _("🛡️ Armor: **{armor}**\n").format(armor=item.get("armor"))),
                    value=item["value"],
                    xp=int(xp),
                    prefix=ctx.clean_prefix,
                    storage_type=storage_type,
                ),
                colour=0x00FF00,
            )
        )

        if current_level >= 15:
            iscompleted = True
            self.bot.dispatch("adventure_completion", ctx, iscompleted)
            self.bot.dispatch("raid_completion", ctx, iscompleted, ctx.author.id)

        new_level = int(rpgtools.xptolevel(ctx.character_data["xp"] + xp))
        if current_level != new_level:
            await self.bot.process_levelup(ctx, new_level, current_level)

    @has_char()
    @has_adventure()
    @commands.command(brief=_("Cancels your current adventure."))
    @locale_doc
    async def cancel(self, ctx: Context):
        _(
            """Cancels your ongoing adventure and allows you to start a new one right away."""
        )
        if not await ctx.confirm(_("Are you sure you want to cancel your current adventure?")):
            return await ctx.send(_("Did not cancel your adventure. The journey continues..."))

        await self.bot.delete_adventure(ctx.author)

        id_ = await self.bot.pool.fetchval(
            'DELETE FROM reminders WHERE "user"=$1 AND "type"=$2 RETURNING "id";',
            ctx.author.id,
            "adventure",
        )
        if id_ is not None:
            await self.bot.cogs["Scheduling"].remove_timer(id_)

        await ctx.send(
            _("Canceled your mission. Use `{prefix}adventure [missionID]` to start a new one!").format(
                prefix=ctx.clean_prefix
            )
        )

    @has_char()
    @commands.command(brief=_("Show some adventure stats"))
    @locale_doc
    async def deaths(self, ctx: Context):
        deaths, completed = (ctx.character_data["deaths"], ctx.character_data["completed"])
        if (deaths + completed) != 0:
            rate = round(completed / (deaths + completed) * 100, 2)
        else:
            rate = 100
        await ctx.send(
            _(
                "Out of **{total}** adventures, you died **{deaths}** times and survived **{completed}** times, which is a success rate of **{rate}%**."
            ).format(total=deaths + completed, deaths=deaths, completed=completed, rate=rate)
        )


async def setup(bot):
    await bot.add_cog(Adventure(bot))
