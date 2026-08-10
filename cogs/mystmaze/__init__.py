
import asyncio
import datetime
import re
import traceback
from dataclasses import dataclass
from typing import Dict, List, Optional

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
from utils.divine_familiars import DIVINE_FAMILIARS, award_divine_shards
from utils.i18n import _, locale_doc
from utils.joins import JoinView


DIVINE_SHARD_RAID_PROC_CHANCE = 0.15
DIVINE_SHARD_FAMILIAR_KEY = "primordial_familiar"
HECATE_RAID_CHANNEL_ID = 1415389846834516172
HECATE_RAID_ROLE_ID = 1415388751064076318
CLYTIUS_THEME_COLOR = 0x71368A
CLYTIUS_JOIN_IMAGE = "https://i.imgur.com/zoK4X4p.jpeg"
CLYTIUS_ATTACK_IMAGE = "https://i.imgur.com/eToOkob.png"
CLYTIUS_VICTORY_IMAGE = "https://i.imgur.com/aJUTrri.png"


def render_hp_bar(current_hp: int, max_hp: int, *, width: int = 12) -> str:
    current_hp = max(0, int(current_hp))
    max_hp = max(1, int(max_hp))
    ratio = max(0.0, min(1.0, current_hp / max_hp))
    filled = round(ratio * width)
    return f"`{'█' * filled}{'░' * (width - filled)}` {current_hp:,}/{max_hp:,}"


def stance_label(stance: str) -> str:
    return "Frontline" if stance == "frontline" else "Backline"


def stance_emoji(stance: str) -> str:
    return "🛡️" if stance == "frontline" else "✨"


@dataclass
class ClytiusPetState:
    owner: discord.Member
    name: str
    hp: int
    max_hp: int
    attack: int
    defense: int
    stance: str
    url: Optional[str] = None
    alive: bool = True
    guarding: bool = False
    total_dealt: int = 0
    total_taken: int = 0


class HecatePetJoinView(View):
    def __init__(self, bot: commands.Bot, *, timeout: int = 60 * 15):
        super().__init__(timeout=timeout)
        self.bot = bot
        self.joined: Dict[int, discord.Member] = {}

        join_button = Button(
            style=ButtonStyle.primary,
            label="Join the Hunt",
            emoji="🐾",
        )
        join_button.callback = self._join_callback
        self.add_item(join_button)

    async def _join_callback(self, interaction: Interaction):
        member = interaction.user
        if not isinstance(member, discord.Member):
            return await interaction.response.send_message(
                "This raid can only be joined from the server.",
                ephemeral=True,
            )

        if member.id in self.joined:
            return await interaction.response.send_message(
                "Your pet is already in the hunt.",
                ephemeral=True,
            )

        async with self.bot.pool.acquire() as conn:
            profile = await conn.fetchrow(
                'SELECT god FROM profile WHERE "user"=$1;',
                member.id,
            )
            if not profile or profile["god"] != "Hecate":
                return await interaction.response.send_message(
                    "Only followers of **Hecate** may answer this summons.",
                    ephemeral=True,
                )

            pet = await conn.fetchrow(
                '''
                SELECT id
                  FROM monster_pets
                 WHERE user_id=$1
                   AND equipped=TRUE
                 ORDER BY id DESC
                 LIMIT 1;
                ''',
                member.id,
            )
            if not pet:
                return await interaction.response.send_message(
                    "You need an **equipped pet** to join this raid.",
                    ephemeral=True,
                )

        self.joined[member.id] = member
        await interaction.response.send_message(
            "Hecate’s shadows gather around your pet. You joined the raid.",
            ephemeral=True,
        )


class ClytiusFormationView(View):
    def __init__(self, allowed_ids: List[int], *, timeout: int = 60):
        super().__init__(timeout=timeout)
        self.allowed_ids = set(allowed_ids)
        self.choices: Dict[int, str] = {}

        frontline_button = Button(
            style=ButtonStyle.danger,
            label="Frontline",
            emoji="🛡️",
        )
        frontline_button.callback = self._frontline_callback
        self.add_item(frontline_button)

        backline_button = Button(
            style=ButtonStyle.success,
            label="Backline",
            emoji="✨",
        )
        backline_button.callback = self._backline_callback
        self.add_item(backline_button)

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id not in self.allowed_ids:
            await interaction.response.send_message(
                "This formation choice is only for joined raiders.",
                ephemeral=True,
            )
            return False
        return True

    async def _set_choice(self, interaction: Interaction, stance: str):
        self.choices[interaction.user.id] = stance
        await interaction.response.send_message(
            f"{stance_emoji(stance)} You chose **{stance_label(stance)}**. "
            "You can still swap until the line locks.",
            ephemeral=True,
        )
        if len(self.choices) >= len(self.allowed_ids):
            self.stop()

    async def _frontline_callback(self, interaction: Interaction):
        await self._set_choice(interaction, "frontline")

    async def _backline_callback(self, interaction: Interaction):
        await self._set_choice(interaction, "backline")


class ClytiusActionView(View):
    def __init__(self, owner: discord.Member, pet: ClytiusPetState, *, timeout: int = 20):
        super().__init__(timeout=timeout)
        self.owner_id = owner.id
        self.choice: Optional[str] = None

        strike_button = Button(
            style=ButtonStyle.success,
            label="Hex Pounce",
            emoji="⚔️",
        )
        strike_button.callback = self._strike_callback
        self.add_item(strike_button)

        guard_button = Button(
            style=ButtonStyle.secondary,
            label="Guard",
            emoji="🛡️",
        )
        guard_button.callback = self._guard_callback
        self.add_item(guard_button)

        shift_label = "Fall Back" if pet.stance == "frontline" else "Push Forward"
        shift_button = Button(
            style=ButtonStyle.primary,
            label=shift_label,
            emoji="🔄",
        )
        shift_button.callback = self._shift_callback
        self.add_item(shift_button)

    async def interaction_check(self, interaction: Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "This turn belongs to another raider.",
                ephemeral=True,
            )
            return False
        return True

    async def _set_choice(self, interaction: Interaction, choice: str):
        self.choice = choice
        await interaction.response.send_message(
            "Your command has been heard.",
            ephemeral=True,
        )
        self.stop()

    async def _strike_callback(self, interaction: Interaction):
        await self._set_choice(interaction, "strike")

    async def _guard_callback(self, interaction: Interaction):
        await self._set_choice(interaction, "guard")

    async def _shift_callback(self, interaction: Interaction):
        await self._set_choice(interaction, "shift")

    async def on_timeout(self) -> None:
        self.stop()


class ClytiusRaidEngine:
    def __init__(self, bot: commands.Bot, ctx: commands.Context, send_to_channels, *, boss_hp: Optional[int] = None):
        self.bot = bot
        self.ctx = ctx
        self.send_to_channels = send_to_channels
        self.pets: Dict[discord.Member, ClytiusPetState] = {}
        self.boss_name = "Clytius"
        self.requested_boss_hp = boss_hp
        self.boss_hp = int(boss_hp or 0)
        self.boss_max_hp = int(boss_hp or 0)
        self.avg_attack = 0
        self.avg_defense = 0
        self.avg_hp = 0
        self.round_no = 0
        self.last_hitter: Optional[discord.Member] = None

    async def enroll(self, users: List[discord.Member], formation_choices: Dict[int, str]):
        async with self.bot.pool.acquire() as conn:
            for user in users:
                pet = await conn.fetchrow(
                    '''
                    SELECT name, hp, attack, defense, url
                      FROM monster_pets
                     WHERE user_id=$1
                       AND equipped=TRUE
                     ORDER BY id DESC
                     LIMIT 1;
                    ''',
                    user.id,
                )
                if not pet:
                    continue

                max_hp = int(pet["hp"] or 500)
                attack = int(pet["attack"] or 120)
                defense = int(pet["defense"] or 80)
                if max_hp <= 0:
                    max_hp = 500
                if attack <= 0:
                    attack = 120
                if defense < 0:
                    defense = 0

                chosen_stance = formation_choices.get(user.id)
                if chosen_stance not in {"frontline", "backline"}:
                    chosen_stance = self.default_stance(max_hp=max_hp, attack=attack, defense=defense)

                self.pets[user] = ClytiusPetState(
                    owner=user,
                    name=pet["name"] or "Your Pet",
                    hp=max_hp,
                    max_hp=max_hp,
                    attack=attack,
                    defense=defense,
                    stance=chosen_stance,
                    url=pet["url"] or None,
                )

        if self.pets:
            self.avg_attack = max(80, int(sum(p.attack for p in self.pets.values()) / len(self.pets)))
            self.avg_defense = max(40, int(sum(p.defense for p in self.pets.values()) / len(self.pets)))
            self.avg_hp = max(350, int(sum(p.max_hp for p in self.pets.values()) / len(self.pets)))
            auto_hp = max(
                12000,
                int(sum(p.attack for p in self.pets.values()) * 18 + sum(p.max_hp for p in self.pets.values()) * 1.2),
            )
            self.boss_max_hp = int(self.requested_boss_hp or auto_hp)
            self.boss_hp = self.boss_max_hp

    @staticmethod
    def default_stance(*, max_hp: int, attack: int, defense: int) -> str:
        frontline_weight = defense + int(max_hp * 0.16)
        backline_weight = int(attack * 1.25)
        return "frontline" if frontline_weight >= backline_weight else "backline"

    def boss_bar(self) -> str:
        return render_hp_bar(self.boss_hp, self.boss_max_hp, width=18)

    def living_entries(self) -> List[tuple[discord.Member, ClytiusPetState]]:
        return [(owner, pet) for owner, pet in self.pets.items() if pet.alive and pet.hp > 0]

    def survivors(self) -> List[discord.Member]:
        return [owner for owner, pet in self.pets.items() if pet.alive and pet.hp > 0]

    def mvp_entry(self) -> Optional[tuple[discord.Member, ClytiusPetState]]:
        if not self.pets:
            return None
        return max(self.pets.items(), key=lambda item: item[1].total_dealt, default=None)

    def formation_lines(self) -> Dict[str, List[str]]:
        lines = {"frontline": [], "backline": []}
        for owner, pet in self.pets.items():
            lines[pet.stance].append(f"{owner.mention} • **{pet.name}**")
        return lines

    def leaderboard_lines(self, *, limit: int = 5) -> List[str]:
        ranked = sorted(self.pets.items(), key=lambda item: item[1].total_dealt, reverse=True)
        lines = []
        for idx, (owner, pet) in enumerate(ranked[:limit], start=1):
            lines.append(
                f"**{idx}.** {owner.mention}'s **{pet.name}** — "
                f"**{pet.total_dealt:,}** dealt / **{pet.total_taken:,}** taken"
            )
        return lines

    async def announce_opening(self):
        lines = self.formation_lines()
        embed = discord.Embed(
            title="The Hunt Begins",
            description=(
                "Clytius crashes through the temple stones as Hecate’s beasts circle in. "
                "Frontline pets draw the giant’s brutal blows, while backline pets strike harder."
            ),
            color=CLYTIUS_THEME_COLOR,
        )
        embed.add_field(
            name="🛡️ Frontline",
            value="\n".join(lines["frontline"]) if lines["frontline"] else "No pets stepped forward.",
            inline=False,
        )
        embed.add_field(
            name="✨ Backline",
            value="\n".join(lines["backline"]) if lines["backline"] else "No pets held the rear.",
            inline=False,
        )
        embed.add_field(name="Boss HP", value=self.boss_bar(), inline=False)
        embed.set_image(url=CLYTIUS_JOIN_IMAGE)
        await self.send_to_channels(embed=embed)

    async def run(self) -> dict:
        while self.boss_hp > 0 and self.living_entries() and self.round_no < 60:
            self.round_no += 1
            owner, pet = randomm.choice(self.living_entries())
            await self._player_turn(owner, pet)
            if self.boss_hp <= 0:
                break
            await self._boss_turn()
            self._clear_guard_states()
            await asyncio.sleep(3)

        victory = self.boss_hp <= 0
        if victory:
            finish = discord.Embed(
                title="Clytius Falls",
                description=(
                    "The giant buckles as claws, shadows, and witchfire tear through him. "
                    "Hecate’s faithful hold the chamber long enough to claim the spoils."
                ),
                color=discord.Color.from_rgb(121, 81, 148),
            )
            finish.add_field(name="Boss HP", value=self.boss_bar(), inline=False)
            finish.set_image(url=CLYTIUS_VICTORY_IMAGE)
            await self.send_to_channels(embed=finish)
        else:
            finish = discord.Embed(
                title="Clytius Endures",
                description=(
                    "The cavern shudders apart under the giant’s rage, forcing Hecate’s companions "
                    "to retreat before the ceiling buries them all."
                ),
                color=discord.Color.red(),
            )
            finish.add_field(name="Boss HP Remaining", value=self.boss_bar(), inline=False)
            finish.set_thumbnail(url=CLYTIUS_ATTACK_IMAGE)
            await self.send_to_channels(embed=finish)

        return {
            "victory": victory,
            "survivors": self.survivors(),
            "mvp": self.mvp_entry(),
            "leaderboard": self.leaderboard_lines(),
        }

    async def _player_turn(self, owner: discord.Member, pet: ClytiusPetState):
        prompt = discord.Embed(
            title=f"Round {self.round_no} • {pet.name} awaits your command",
            description=f"Only {owner.mention} can choose the next move.",
            color=CLYTIUS_THEME_COLOR,
        )
        prompt.add_field(
            name="Current Stance",
            value=(
                f"{stance_emoji(pet.stance)} **{stance_label(pet.stance)}**\n"
                f"{'Higher defense and more aggro.' if pet.stance == 'frontline' else 'Higher damage, riskier special targeting.'}"
            ),
            inline=True,
        )
        prompt.add_field(name="Pet HP", value=render_hp_bar(pet.hp, pet.max_hp), inline=True)
        prompt.add_field(name="Boss HP", value=self.boss_bar(), inline=False)
        if pet.url:
            prompt.set_thumbnail(url=pet.url)
        else:
            prompt.set_thumbnail(url=CLYTIUS_ATTACK_IMAGE)

        view = ClytiusActionView(owner, pet)
        message = await self.ctx.send(embed=prompt, view=view)
        await view.wait()
        try:
            await message.edit(view=None)
        except Exception:
            pass

        choice = view.choice or ("guard" if pet.stance == "frontline" else "strike")
        attack_multiplier = 1.25 if pet.stance == "backline" else 1.0
        action_text = ""

        if choice == "shift":
            previous = pet.stance
            pet.stance = "backline" if pet.stance == "frontline" else "frontline"
            attack_multiplier *= 0.80
            action_text = (
                f"{stance_emoji(previous)} {pet.name} slips out of the {stance_label(previous).lower()} "
                f"and takes the {stance_label(pet.stance).lower()}."
            )
        elif choice == "guard":
            pet.guarding = True
            attack_multiplier *= 0.65
            action_text = f"🛡️ {pet.name} braces for impact, trading power for protection."
        else:
            attack_multiplier *= 1.15
            action_text = f"⚔️ {pet.name} lunges with Hecate-blessed fury."

        damage = max(1, int(pet.attack * attack_multiplier * randomm.uniform(0.90, 1.10)))
        self.boss_hp = max(0, self.boss_hp - damage)
        pet.total_dealt += damage
        if self.boss_hp <= 0:
            self.last_hitter = owner

        result = discord.Embed(
            title=f"{pet.name} strikes Clytius",
            description=action_text,
            color=discord.Color.magenta(),
        )
        result.add_field(name="Damage", value=f"**{damage:,}**", inline=True)
        result.add_field(
            name="Now Standing",
            value=f"{stance_emoji(pet.stance)} **{stance_label(pet.stance)}**",
            inline=True,
        )
        result.add_field(name="Boss HP", value=self.boss_bar(), inline=False)
        if pet.url:
            result.set_thumbnail(url=pet.url)
        else:
            result.set_thumbnail(url=CLYTIUS_ATTACK_IMAGE)
        await self.send_to_channels(embed=result)

    async def _boss_turn(self):
        if randomm.random() < 0.65:
            await self._normal_attack()
        else:
            await self._hp_rend()

    def _pick_target(self, *, prefer_stance: str, preference_chance: float) -> Optional[tuple[discord.Member, ClytiusPetState]]:
        alive = self.living_entries()
        if not alive:
            return None
        preferred = [(owner, pet) for owner, pet in alive if pet.stance == prefer_stance]
        if preferred and randomm.random() < preference_chance:
            return randomm.choice(preferred)
        return randomm.choice(alive)

    async def _normal_attack(self):
        chosen = self._pick_target(prefer_stance="frontline", preference_chance=0.78)
        if not chosen:
            return

        owner, pet = chosen
        raw_low = int(self.avg_attack * 0.55 + self.avg_hp * 0.08)
        raw_high = int(self.avg_attack * 0.90 + self.avg_hp * 0.14)
        raw = randomm.randint(max(1, raw_low), max(raw_low + 1, raw_high))

        effective_defense = pet.defense * (1.35 if pet.stance == "frontline" else 1.0)
        if pet.guarding:
            effective_defense *= 1.45

        damage = max(1, raw - int(effective_defense))
        pet.hp = max(0, pet.hp - damage)
        pet.total_taken += damage
        if pet.hp <= 0:
            pet.alive = False

        embed = discord.Embed(
            title="Clytius swings a titan-forged pillar",
            description=f"{owner.mention}'s **{pet.name}** is caught in the shockwave.",
            color=discord.Color.orange(),
        )
        embed.add_field(name="Damage", value=f"**{damage:,}**", inline=True)
        embed.add_field(name="Pet HP", value=render_hp_bar(pet.hp, pet.max_hp), inline=True)
        embed.add_field(name="Boss HP", value=self.boss_bar(), inline=False)
        if pet.hp <= 0:
            embed.add_field(
                name="Fallen",
                value=f"**{pet.name}** is knocked out of the hunt.",
                inline=False,
            )
        embed.set_thumbnail(url=CLYTIUS_ATTACK_IMAGE)
        await self.send_to_channels(embed=embed)

    async def _hp_rend(self):
        chosen = self._pick_target(prefer_stance="backline", preference_chance=0.72)
        if not chosen:
            return

        owner, pet = chosen
        percent = randomm.randint(25, 55)
        damage = max(1, int(pet.max_hp * (percent / 100)))
        if pet.stance == "frontline":
            damage = max(1, int(damage * 0.75))
        if pet.guarding:
            damage = max(1, int(damage * 0.75))

        pet.hp = max(0, pet.hp - damage)
        pet.total_taken += damage
        if pet.hp <= 0:
            pet.alive = False

        embed = discord.Embed(
            title="Clytius tears at the bond itself",
            description=(
                f"A crushing curse rips **{percent}%** of {owner.mention}'s **{pet.name}** max health away."
            ),
            color=discord.Color.dark_red(),
        )
        embed.add_field(name="Damage", value=f"**{damage:,}**", inline=True)
        embed.add_field(name="Pet HP", value=render_hp_bar(pet.hp, pet.max_hp), inline=True)
        embed.add_field(name="Boss HP", value=self.boss_bar(), inline=False)
        if pet.hp <= 0:
            embed.add_field(
                name="Fallen",
                value=f"**{pet.name}** collapses under the curse.",
                inline=False,
            )
        embed.set_thumbnail(url=CLYTIUS_ATTACK_IMAGE)
        await self.send_to_channels(embed=embed)

    def _clear_guard_states(self):
        for pet in self.pets.values():
            pet.guarding = False


class MystMaze(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.raid_active = False

    async def set_raid_timer(self):
        self.raid_active = True

    async def clear_raid_timer(self):
        self.raid_active = False

    def _pick_hecate_crate(self, luck_value: Optional[float]) -> str:
        gods = {
            "Hecate": {"boundary_low": 0.8, "boundary_high": 1.2},
            "Thanatos": {"boundary_low": 0.4, "boundary_high": 1.6},
            "Morpheus": {"boundary_low": 0, "boundary_high": 2.0},
            "Apollo": {"boundary_low": 0, "boundary_high": 2.0},
        }
        selected_god = "Hecate"
        god_data = gods[selected_god]
        boundary_low = god_data["boundary_low"]
        boundary_high = god_data["boundary_high"]
        luck_query_float = float(luck_value or 1.0)
        normalized_luck = (luck_query_float - boundary_low) / (boundary_high - boundary_low)
        normalized_luck = max(0.0, min(1.0, normalized_luck))
        weightdivine = 0.20 + (0.20 * normalized_luck)
        rounded_weightdivine = round(weightdivine, 3)
        options = ["legendary", "fortune", "divine"]
        weights = [0.40, 0.40, rounded_weightdivine]
        return randomm.choices(options, weights=weights)[0]

    def _hecate_channels(self, ctx: commands.Context) -> List[discord.abc.Messageable]:
        channel = self.bot.get_channel(HECATE_RAID_CHANNEL_ID)
        return [channel or ctx.channel]

    async def _hecate_countdown(self, send_to_channels, raid_name: str):
        for delay, message in [
            (300, f"**{raid_name} stirs in 10 minutes**"),
            (300, f"**{raid_name} stirs in 5 minutes**"),
            (180, f"**{raid_name} stirs in 2 minutes**"),
            (60, f"**{raid_name} stirs in 1 minute**"),
            (30, f"**{raid_name} stirs in 30 seconds**"),
            (20, f"**{raid_name} stirs in 10 seconds**"),
        ]:
            await asyncio.sleep(delay)
            await send_to_channels(content=message)

    @is_god()
    @commands.command(hidden=True, brief=_("Starts Mystical Labyrinth"))
    async def spawnmaze(self, ctx):
        """[Hecate only] Starts a Mystical Labyrinth."""
        await self.set_raid_timer()

        try:
            view = JoinView(
                Button(style=ButtonStyle.primary, label="Join the Labyrinth!"),
                message=_("You joined the Labyrinth."),
                timeout=60 * 15,
            )

            channels = [
                self.bot.get_channel(1415389846834516172),  # This is the current channel where the command was invoked
             ]

            channel1 = self.bot.get_channel(1415389846834516172)
            role_id1 = 1415388751064076318

            if channel1:
                role1 = ctx.guild.get_role(role_id1)
                if role1:
                    await channel1.send(content=f"{role1.mention}", allowed_mentions=discord.AllowedMentions(roles=True))

            
            # Message content, organized for better formatting
            message_intro = """
            Seekers of knowledge and secrets,
            Enter the Labyrinth at your own peril.
            Perceive the divine signs of thy guide,
            Retrieve the artifact, or meet thyn demise.

            """

            message_trial = """
            **__Pursuers of knowledge,stand before the threshold.__**
            The Threshold closes in 15 minutes
            """

            message_note = """
            **Only followers of Hecate may join.**
            """

            # Create the embed with structured fields
            embed = discord.Embed(
                title="Champions of Truth ",
                color=discord.Color.purple()
            )
            embed.add_field(name="Hecate's wisdom", value=message_intro, inline=False)
            embed.add_field(name="Labyrinth Information", value=message_trial, inline=False)
            embed.add_field(name="Notice", value=message_note, inline=False)
            embed.set_footer(text="Prepare yourself the Labyrinth awaits.")
            embed.timestamp = discord.utils.utcnow()

            # Attach the file (image)
            embed.set_image(url="https://i.imgur.com/zoK4X4p.jpeg")

            # Updated helper function to send to both channels and handle file closing issue
            async def send_to_channels(embed=None, content=None, view=None):
                """Helper function to send a message to all channels."""
                for channel in channels:
                    if channel is not None:  # Ensure the channel is valid
                        try:
                            await channel.send(embed=embed, content=content, view=view)
                        except Exception as e:
                            await ctx.send(f"Failed to send message to {channel.name}: {str(e)}")
                    else:
                        await ctx.send("One of the channels could not be found.")

            # Call this function with file_path
            await send_to_channels(embed=embed, content=None, view=view)

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
                await send_to_channels(content="**The Labyrinth’s threshold closes in 10 minutes**")
                await asyncio.sleep(300)
                await send_to_channels(content="**The Labyrinth’s threshold closes in 5  minutes**")
                await asyncio.sleep(180)
                await send_to_channels(content="**The Labyrinth’s threshold closes in 2 minutes**")
                await asyncio.sleep(60)
                await send_to_channels(content="**The Labyrinth’s threshold closes in 1 minute**")
                await asyncio.sleep(30)
                await send_to_channels(content="**The Labyrinth’s threshold closes in 30 seconds**")
                await asyncio.sleep(20)
                await send_to_channels(content="**The Labyrinth’s threshold closes in 10 seconds**")

            view.stop()

            await send_to_channels(content="**Mystical Labyrinth will commence! Fetch participant data... Hang on!**")

            async with self.bot.pool.acquire() as conn:
                raid = []
                HowMany = 0
                for u in view.joined:
                    if (
                            not (
                                    profile := await conn.fetchrow(
                                        'SELECT * FROM profile WHERE "user"=$1;', u.id
                                    )
                            )
                            or profile["god"] != "Hecate"
                    ):
                        continue
                    HowMany = HowMany + 1
                    raid.append(u)
                participants = list(raid)

            await send_to_channels(content="**Done getting data!**")
            await send_to_channels(content=f"**{HowMany} followers joined!**")

            while len(raid) > 1:
                time = random.choice(["day", "night"])
                if time == "day":
                    em = discord.Embed(
                        title="The torches brighten,illuminating the way",
                        description="The presence of Hecate is obvious, her signs linger in thy sight, "
                                    "her guidance leading to better paths.",
                        colour=0x71368a,
                    )
                else:
                    em = discord.Embed(
                        title="The torches diminish, the shadows bring uncertainty",
                        description="Darkness covers the halls, each step brings more and more unknown, "
                                    "do thy eyes deceive you?",
                        colour=0x71368a,
                    )
                em.set_thumbnail(url="https://i.imgur.com/eToOkob.png")
                await send_to_channels(embed=em)
                await asyncio.sleep(5)
                target = random.choice(raid)
                if time == "day":
                    event = random.choice(
                        [
                            {
                                "text": "You come before a crossroads",
                                "win": 80,
                                "win_text": "You take note of a torch hanging overhead in one of the directions."
                                            "You follow the sign.",
                                "lose_text": "Despite your best efforts to look, you are unable to notice the signs."
                                             "You lose your way.",
                            },
                            {
                                "text": "The darkness envelopes you, you hear shuffling in the dark around you and feel something with fur.",
                                "win": 50,
                                "win_text": "You reach for the loyal guide who shows the way forward." ,
                                "lose_text": "You jump back in fright at the creature near you running in a random direction.",
                            },
                            {
                                "text": "Getting to a fork in the road words hang before you “The Past is a place of…",
                                "win": 60,
                                "win_text": "You take the path that forms the words 'Reference' as the past should always be referenced." ,
                                "lose_text": "You take the path that forms the words 'Residence' as you end up back on a path you had already taken before.",
                             },
                        ]
                    )
                else:
                    event = random.choice(
                        [
                            {
                                "text": "Before you are two paths, the wall in front of you read’s “The future is something you…",
                                "win": 50,
                                "win_text": "You look at the options before you and choose “Create” as the future can not truly come unless you aid to create it yourself." ,
                                "lose_text": "You see one of the sides says “Enter” you decide to follow its advice, but now you don’t know where to go from here.",
                            },
                            {
                                "text": "As you are walking you end up falling down a trap door, at the bottom of the drop there is an engraving of words before you “Yesterday’s the past, tomorrow is the future, but today is a…”",
                                "win": 45,
                                "win_text": "Gift, as the words leave your mouth a rope descends from above.",
                                "lose_text": "Mystery, as the words leave your mouth you fall further and further into the Labyrinth.",
                            },
                            {
                                "text": "Before you is a large drop with only a thin ledge to try to use to pass.",
                                "win": 20,
                                "win_text": "You notice twin snake’s eyes on you, watching how they slither above "
                                            "and on the ledge you notice places to put your hands and feet to safely cross",
                                "lose_text": "Facing the ledge you try to go head first at it, but end up going head first "
                                             "down the drop instead.",
                            },
                            {
                                "text": "A door stands in your way, a lock keeps you from entering. Keys, items, and weapons lay scattered around the door.",
                                "win": 50,
                                "win_text": "A dusty, but elegant onyx dagger catches your eye, you place it in the lock and it opens.",
                                "lose_text": "You pick up an axe and attempt to break the lock, in doing so you fall into a trap.",
                            },
                            {
                                "text": "You are no longer sure where to go and you come across a crossroads.",
                                "win": 50,
                                "win_text": "ou notice the subtle markings of a serpent’s body going in the right direction and follow it.",
                                "lose_text": "You pick a random direction as you do the calculations on if it's a good path, but you aren’t the best at math…",
                            },
                            {
                                "text": "You see a list of commands that say to follow them.",
                                "win": 50,
                                "win_text": "Noticing that some of the commands have twin torches next to them you only follow those and get to the next area.",
                                "lose_text": "You follow all the commands blindly, finding yourself very lost.",
                            },
                            {
                                "text": "A howl rings out through the corridors.",
                                "win": 50,
                                "win_text": "You follow the howl in the right direction, leading you back on path.",
                                "lose_text": "You turn tail and go in the opposite direction hoping to avoid a beast, but end up lost.",
                            },
                            {
                                "text": "There are three goblet’s filled with varying amounts of liquid, the archway a head reads “One chance, all must be equal, but you may not touch the goblets”",
                                "win": 50,
                                "win_text": "ou take note of the pedestal that the goblets reside on, there is a serpent spiraling around it, you choose to use your hand to follow it in turn knocking the pedestal over emptying all the goblets. ",
                                "lose_text": "You pace around the room trying to think of something when your robe catches one of the goblets knocking it over, making the room begin to cave in",
                            },
                            {
                                "text": "ou reach a dead end.",
                                "win": 50,
                                "win_text": "Investigating the wall reveals a slight engraving of a torch, you bring one nearby closer, as the light hits the direct area the path forward opens.",
                                "lose_text": "You do not see a way forward, you turn back.",
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
                    colour=0x71368a,
                )
                em.set_author(name=f"{target}", icon_url=target.display_avatar.url)
                em.set_footer(text=f"{len(raid)} followers remain")
                em.set_thumbnail(url="https://i.imgur.com/zoK4X4p.jpeg")
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

            # Define gods with their boundaries
            gods = {
                "Hecate": {"boundary_low": 0.8, "boundary_high": 1.2},
                "Thanatos": {"boundary_low": 0.4, "boundary_high": 1.6},
                "Morpheus": {"boundary_low": 0, "boundary_high": 2.0},
                "Apollo": {"boundary_low": 0, "boundary_high": 2.0},
            }

            # Replace 'selected_god' with the actual selected god name (e.g., "Astraea")
            selected_god = "Hecate"  # Example, replace dynamically
            god_data = gods.get(selected_god)

            if not god_data:
                raise ValueError(f"God {selected_god} not found.")

            boundary_low = god_data["boundary_low"]
            boundary_high = god_data["boundary_high"]

            # Normalize the user's luck value
            normalized_luck = (luck_query_float - boundary_low) / (boundary_high - boundary_low)
            normalized_luck = max(0.0, min(1.0, normalized_luck))  # Clamp between 0.0 and 1.0

            # Scale the divine weight
            weightdivine = 0.20 + (0.20 * normalized_luck)  # Example scaling factor
            rounded_weightdivine = round(weightdivine, 3)

            # Define weights for crate selection
            options = ['legendary', 'fortune', 'divine']
            weights = [0.40, 0.40, rounded_weightdivine]

            # Select a crate based on weights
            crate = randomm.choices(options, weights=weights)[0]

            # Build an embed for the final reward
            final_embed = discord.Embed(
                title="The Labyrinth Collapses",
                description=(
                    f"Before them, atop an ancient runic pedestal, rests the **{crate} crate**. "
                    f"The dust that once covered it is brushed away by gentle hands.\n\n"
                    f"Once lifted, the Labyrinth begins to crumble as they take an arcane gate to safety."
                ),
                color=discord.Color.purple()
            )

            # Add your image
            final_embed.set_image(url="https://i.imgur.com/aJUTrri.png")

            # Send with actual mention in content so it pings
            await send_to_channels(
                content=f"{winner.mention}",
                embed=final_embed
            )


            # Update the profile and clear the raid timer
            async with self.bot.pool.acquire() as conn:
                await conn.execute(
                    f'UPDATE profile SET "crates_{crate}" = "crates_{crate}" + 1 WHERE "user" = $1;',
                    winner.id,
                )

            if participants and randomm.random() < DIVINE_SHARD_RAID_PROC_CHANCE:
                shard_target = randomm.choice(participants)
                familiar_key = DIVINE_SHARD_FAMILIAR_KEY
                familiar_name = DIVINE_FAMILIARS[familiar_key]["name"]
                async with self.bot.pool.acquire() as conn:
                    shard_total = await award_divine_shards(
                        conn,
                        shard_target.id,
                        familiar_key,
                        1,
                    )
                await send_to_channels(
                    content=(
                        f"✨ Hecate’s maze leaves behind **1 {familiar_name} shard** for "
                        f"{shard_target.mention}. Now: **{shard_total}/20**"
                    )
                )

            await self.clear_raid_timer()
        except Exception as e:
            import traceback
            error_message = f"Error occurred: {e}\n"
            error_message += traceback.format_exc()
            await ctx.send(error_message)
            print(error_message)

    @is_god()
    @commands.command(hidden=True, brief=_("Starts Clytius' pet raid"))
    async def spawnclytius(self, ctx, boss_hp: IntGreaterThan(0)):
        """[Hecate only] Starts a pet-only raid against Clytius."""
        if self.raid_active:
            return await ctx.send("A Hecate raid is already running.")

        await self.set_raid_timer()
        channels = self._hecate_channels(ctx)

        async def send_to_channels(*, embed=None, content=None, view=None):
            for channel in channels:
                if channel is None:
                    continue
                await channel.send(embed=embed, content=content, view=view)

        try:
            join_view = HecatePetJoinView(self.bot, timeout=60 * 15)

            role = ctx.guild.get_role(HECATE_RAID_ROLE_ID)
            if role:
                await channels[0].send(
                    content=role.mention,
                    allowed_mentions=discord.AllowedMentions(roles=True),
                )

            intro = discord.Embed(
                title="Hecate Opens the Hunt",
                description=(
                    "Clytius has clawed his way into the crossroads beneath the witch-queen’s halls.\n\n"
                    "**This is a pet-only raid.** Your equipped pet does the fighting while you guide it.\n"
                    "Before battle, each raider will choose a line:\n"
                    "🛡️ **Frontline**: sturdier, more likely to draw Clytius’s normal blows.\n"
                    "✨ **Backline**: hits harder, but Clytius’s curse reaches deeper there."
                ),
                color=CLYTIUS_THEME_COLOR,
            )
            intro.add_field(
                name="How It Works",
                value=(
                    "Each round, one raider must choose whether their pet lunges, guards, or shifts lines. "
                    "If nobody answers in time, the pet acts on instinct."
                ),
                inline=False,
            )
            intro.add_field(
                name="Boss HP",
                value=f"**{boss_hp:,}**",
                inline=False,
            )
            intro.add_field(
                name="Notice",
                value="Only followers of **Hecate** with an **equipped pet** may join.",
                inline=False,
            )
            intro.set_image(url=CLYTIUS_JOIN_IMAGE)
            intro.set_footer(text="The threshold remains open for 15 minutes.")
            intro.timestamp = discord.utils.utcnow()

            await send_to_channels(embed=intro, view=join_view)
            await self._hecate_countdown(send_to_channels, "Clytius")
            join_view.stop()

            joined_members = list(join_view.joined.values())
            if not joined_members:
                await send_to_channels(content="No followers answered Hecate’s summons. Clytius fades back into the dark.")
                return

            await send_to_channels(
                content=(
                    f"**{len(joined_members)} raiders answered.** "
                    "Set your pet’s place in the formation now."
                )
            )

            formation_view = ClytiusFormationView([member.id for member in joined_members], timeout=60)
            formation_embed = discord.Embed(
                title="Choose Your Line",
                description=(
                    "🛡️ **Frontline** gains a defensive edge and draws more of Clytius’s direct force.\n"
                    "✨ **Backline** gains an offensive edge but is more tempting for his max-health curse.\n\n"
                    "If you do not choose, your pet will be placed automatically."
                ),
                color=CLYTIUS_THEME_COLOR,
            )
            formation_embed.set_thumbnail(url=CLYTIUS_ATTACK_IMAGE)
            await send_to_channels(embed=formation_embed, view=formation_view)
            await formation_view.wait()

            engine = ClytiusRaidEngine(self.bot, ctx, send_to_channels, boss_hp=boss_hp)
            await engine.enroll(joined_members, formation_view.choices)
            if not engine.pets:
                await send_to_channels(content="No valid equipped pets remained by the time the hunt began.")
                return

            await engine.announce_opening()
            result = await engine.run()
            valid_participants = list(engine.pets.keys())

            if result["victory"]:
                mvp_entry = result["mvp"]
                bonus_crate = None
                cash_reward = randomm.randint(20000, 50000)

                async with self.bot.pool.acquire() as conn:
                    if valid_participants:
                        await conn.execute(
                            'UPDATE profile SET money=money+$1 WHERE "user"=ANY($2);',
                            cash_reward,
                            [participant.id for participant in valid_participants],
                        )
                    if mvp_entry is not None:
                        mvp_owner, _mvp_pet = mvp_entry
                        luck_value = await conn.fetchval(
                            'SELECT luck FROM profile WHERE "user"=$1;',
                            mvp_owner.id,
                        )
                        bonus_crate = self._pick_hecate_crate(luck_value)
                        await conn.execute(
                            f'UPDATE profile SET "crates_{bonus_crate}" = "crates_{bonus_crate}" + 1 WHERE "user" = $1;',
                            mvp_owner.id,
                        )

                reward_embed = discord.Embed(
                    title="Spoils of the Crossroads",
                    description=(
                        "As with Hecate’s labyrinth, only one champion is singled out for the final prize."
                    ),
                    color=discord.Color.purple(),
                )
                if mvp_entry is not None and bonus_crate is not None:
                    mvp_owner, mvp_pet = mvp_entry
                    reward_embed.add_field(
                        name="Champion of the Hunt",
                        value=(
                            f"{mvp_owner.mention}'s **{mvp_pet.name}** led the hunt and earned "
                            f"**1 {bonus_crate} crate**."
                        ),
                        inline=False,
                    )
                else:
                    reward_embed.add_field(
                        name="Champion of the Hunt",
                        value="No champion could be determined for the crate reward.",
                        inline=False,
                    )
                reward_embed.add_field(
                    name="Top Damage",
                    value="\n".join(result["leaderboard"]) if result["leaderboard"] else "No battle data recorded.",
                    inline=False,
                )
                reward_embed.add_field(
                    name="Shared Payout",
                    value=(
                        f"💰 All participants receive **${cash_reward:,}** for driving Clytius back."
                        if valid_participants
                        else "No participants remained eligible for a payout."
                    ),
                    inline=False,
                )
                reward_embed.set_thumbnail(url=CLYTIUS_ATTACK_IMAGE)
                await send_to_channels(embed=reward_embed)

                if valid_participants and randomm.random() < DIVINE_SHARD_RAID_PROC_CHANCE:
                    shard_target = randomm.choice(valid_participants)
                    familiar_key = DIVINE_SHARD_FAMILIAR_KEY
                    familiar_name = DIVINE_FAMILIARS[familiar_key]["name"]
                    async with self.bot.pool.acquire() as conn:
                        shard_total = await award_divine_shards(
                            conn,
                            shard_target.id,
                            familiar_key,
                            1,
                        )
                    await send_to_channels(
                        content=(
                            f"✨ In the dust of Clytius’s fall, **1 {familiar_name} shard** settles into "
                            f"{shard_target.mention}'s hands. Now: **{shard_total}/20**"
                        )
                    )
            else:
                defeat_embed = discord.Embed(
                    title="The Hunt Breaks",
                    description=(
                        "Clytius remains standing. Hecate’s companions escape with their pets, "
                        "but no crates are won this time."
                    ),
                    color=discord.Color.dark_red(),
                )
                defeat_embed.add_field(
                    name="Top Damage",
                    value="\n".join(result["leaderboard"]) if result["leaderboard"] else "No battle data recorded.",
                    inline=False,
                )
                await send_to_channels(embed=defeat_embed)
        except Exception as e:
            error_message = f"Error occurred: {e}\n"
            error_message += traceback.format_exc()
            await ctx.send(error_message)
            print(error_message)
        finally:
            await self.clear_raid_timer()

async def setup(bot):
    await bot.add_cog(MystMaze(bot))
