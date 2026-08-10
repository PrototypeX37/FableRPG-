"""
The IdleRPG Discord Bot
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
import discord
from discord import Embed
from discord.ext import commands
from discord.ui import Button, View
from discord.enums import ButtonStyle
from discord.interactions import Interaction

# Project-specific imports (unchanged – your project must provide these)
import utils.misc as rpgtools
from classes.classes import Raider
from classes.classes import from_string as class_from_string
from classes.converters import IntGreaterThan
from cogs.shard_communication import user_on_cooldown as user_cooldown
from utils.divine_familiars import DIVINE_FAMILIARS, award_divine_shards
from utils.i18n import _, locale_doc
from utils.joins import JoinView
from utils.checks import is_god

import random as randomm


DIVINE_SHARD_RAID_PROC_CHANCE = 0.15
DIVINE_SHARD_FAMILIAR_KEY = "sepulchure_familiar"
SPECIAL_ROLE_REWARD_WEIGHT = 2


def pick_weighted_reward_target(candidates, *favored_roles):
    favored_ids = {
        getattr(role, "id", role)
        for role in favored_roles
        if getattr(role, "id", role) is not None
    }
    weights = [
        SPECIAL_ROLE_REWARD_WEIGHT
        if getattr(candidate, "id", candidate) in favored_ids
        else 1
        for candidate in candidates
    ]
    return randomm.choices(candidates, weights=weights, k=1)[0]


# ---------- UI components ----------

class DecisionButton(Button):
    def __init__(self, label, *args, **kwargs):
        super().__init__(label=label, *args, **kwargs)

    async def callback(self, interaction: Interaction):
        view: "DecisionView" = self.view  # type: ignore
        view.value = self.custom_id
        # respond safely (avoid "interaction failed")
        try:
            await interaction.response.send_message(
                f"You selected {self.custom_id}. Shortcut back: <#1415390006457143466>",
                ephemeral=True,
            )
        except discord.InteractionResponded:
            await interaction.followup.send(
                f"You selected {self.custom_id}. Shortcut back: <#1415390006457143466>",
                ephemeral=True,
            )
        view.stop()


class DecisionView(View):
    def __init__(self, player: discord.User | discord.Member, options, timeout=60):
        super().__init__(timeout=timeout)
        self.player = player
        self.value: str | None = None
        for option in options:
            self.add_item(
                DecisionButton(
                    style=ButtonStyle.primary, label=option, custom_id=option
                )
            )

    async def interaction_check(self, interaction: Interaction) -> bool:
        return interaction.user == self.player


# ---------- AIs ----------

class ShadowChampionAI:
    """AI representation of Eurydice when AI-controlled."""

    def __init__(self):
        self.name = "Eurydice"
        self.mention = "Eurydice"
        self.display_name = "Eurydice"

    async def make_decision(self, champion_stats, guardians_stats, progress, followers_plans):
        await asyncio.sleep(2)  # AI "thinking" time

        guardian_phase = guardians_stats.get("phase", 1)
        guardian_hp_ratio = guardians_stats["hp"] / guardians_stats["max_hp"]
        guardian_enraged = guardians_stats.get("enraged", False)
        guardian_incapacitated = guardians_stats.get("incapacitated_turns", 0) > 0

        follower_boost = followers_plans.get("Boost Ritual", 0)
        follower_protect = followers_plans.get("Protect Eurydice", 0)
        follower_heal = followers_plans.get("Heal Eurydice", 0)

        # Priority 1: Survival
        if champion_stats["hp"] < champion_stats["max_hp"] * 0.25:
            if "Heal" in self.get_valid_actions(champion_stats):
                return "Heal"
            elif follower_heal > 0:
                return "Smite"

        # Priority 2: Defend if vulnerable or guardian enraged
        if (champion_stats.get("vulnerable", False) or guardian_enraged) and "Defend" in self.get_valid_actions(champion_stats):
            return "Defend"

        # Priority 3: Exploit incapacitation
        if guardian_incapacitated:
            if progress < 80 and "Haste" in self.get_valid_actions(champion_stats) and champion_stats.get("haste_cooldown", 0) == 0:
                return "Haste"
            elif progress < 70 and "Sacrifice" in self.get_valid_actions(champion_stats) and champion_stats["hp"] > champion_stats["max_hp"] * 0.5:
                return "Sacrifice"

        # Priority 4: Coordinate with follower boost
        if progress < 60 and follower_boost > 0:
            if "Haste" in self.get_valid_actions(champion_stats) and champion_stats.get("haste_cooldown", 0) == 0:
                return "Haste"

        # Priority 5: Sacrifice when protected
        if progress < 50 and champion_stats["hp"] > champion_stats["max_hp"] * 0.7 and follower_protect > 0:
            if "Sacrifice" in self.get_valid_actions(champion_stats):
                return "Sacrifice"

        # Default
        return "Smite"

    def get_valid_actions(self, champion_stats):
        actions = ["Smite", "Heal", "Defend", "Sacrifice"]
        if champion_stats.get("haste_cooldown", 0) == 0:
            actions.append("Haste")
        return actions

    async def announce_decision(self, ctx, decision, champion_stats, guardians_stats, progress):
        guardian_phase = guardians_stats.get("phase", 1)
        guardian_hp_ratio = guardians_stats["hp"] / guardians_stats["max_hp"]

        if decision == "Smite":
            if guardian_hp_ratio < 0.3:
                announcement = ("Eurydice’s form crackles with dark energy. "
                                "'The Guardian weakens! Now is the time to strike!'")
            elif guardian_phase > 1:
                announcement = ("Eurydice’s form crackles with dark energy. "
                                "'Even evolved, this Guardian cannot withstand the void!'")
            else:
                announcement = ("Eurydice’s form crackles with dark energy. "
                                "'I will strike the Guardian with the power of the void!'")
        elif decision == "Heal":
            announcement = ("Dark tendrils of shadow wrap around Eurydice. "
                            "'The shadows mend my wounds... The ritual must continue.'")
        elif decision == "Haste":
            announcement = ("Eurydice becomes ethereal. "
                            "'I will accelerate the ritual—guard me from the Guardian’s wrath!'")
        elif decision == "Defend":
            if guardians_stats.get("enraged", False):
                announcement = ("Eurydice raises shadowy barriers. "
                                "'The Guardian’s rage is palpable—I must brace!'")
            else:
                announcement = ("Eurydice raises shadowy barriers. "
                                "'I brace against the assault. The ritual will not fail.'")
        elif decision == "Sacrifice":
            announcement = ("Eurydice’s essence flickers as dark energy feeds the ritual. "
                            "'I offer my life force to advance our cause.'")
        else:
            announcement = "Eurydice prepares for action."

        await ctx.send(f"👻 **{announcement}**")


class ShadowPriestAI:
    """AI representation of Orpheus when AI-controlled."""

    def __init__(self):
        self.name = "Orpheus"
        self.mention = "Orpheus"
        self.display_name = "Orpheus"

    async def make_decision(self, priest_stats, champion_stats, guardians_stats, progress, followers_plans):
        await asyncio.sleep(2)

        guardian_phase = guardians_stats.get("phase", 1)
        guardian_enraged = guardians_stats.get("enraged", False)
        guardian_cursed = guardians_stats.get("cursed", False)

        follower_empower = followers_plans.get("Empower Orpheus", 0)
        follower_trip = followers_plans.get("Trip Tartaros", 0)
        follower_protect = followers_plans.get("Protect Eurydice", 0)

        if champion_stats["hp"] < champion_stats["max_hp"] * 0.3 and priest_stats["mana"] >= 20:
            return "Revitalize"

        if not guardian_cursed and priest_stats["mana"] >= 25:
            if guardian_enraged or guardian_phase > 1:
                return "Curse"
            elif follower_trip == 0:
                return "Curse"

        if champion_stats["hp"] < champion_stats["max_hp"] * 0.6:
            if follower_protect > 0 and priest_stats["mana"] >= 20:
                return "Bless"
            elif priest_stats["mana"] >= 20:
                return "Revitalize"

        if (guardian_enraged or guardian_phase > 1) and priest_stats["mana"] >= 30:
            if not champion_stats.get("barrier_active", False):
                return "Barrier"

        if progress < 70 and priest_stats["mana"] >= 15:
            if follower_empower > 0 or progress < 50:
                return "Channel"

        if champion_stats["hp"] > champion_stats["max_hp"] * 0.7 and priest_stats["mana"] >= 20:
            return "Bless"

        return None

    async def announce_decision(self, ctx, decision, priest_stats, champion_stats, guardians_stats, progress):
        guardian_phase = guardians_stats.get("phase", 1)
        guardian_enraged = guardians_stats.get("enraged", False)

        if decision == "Bless":
            if guardian_phase > 1:
                announcement = ("Orpheus’ eyes glow with ancient knowledge. "
                                "'I empower our Champion against this evolved Guardian!'")
            else:
                announcement = ("Orpheus’ eyes glow with ancient knowledge. "
                                "'I empower our Champion!'")
        elif decision == "Barrier":
            if guardian_enraged:
                announcement = ("Mystical runes materialize around Orpheus. "
                                "'A barrier shall protect our Champion from fury!'")
            else:
                announcement = ("Mystical runes materialize around Orpheus. "
                                "'A barrier shall protect our Champion from harm.'")
        elif decision == "Curse":
            if guardian_phase > 1:
                announcement = ("Orpheus’ voice echoes with power. "
                                "'I cast a curse upon this evolved Guardian!'")
            elif guardian_enraged:
                announcement = ("Orpheus’ voice echoes with power. "
                                "'I cast a curse upon the enraged Guardian!'")
            else:
                announcement = ("Orpheus’ voice echoes with power. "
                                "'I cast a curse upon the Guardian!'")
        elif decision == "Revitalize":
            announcement = ("Dark healing energies flow from Orpheus. "
                            "'I mend the Champion's wounds.'")
        elif decision == "Channel":
            announcement = ("Orpheus’ form pulses with ritual energy. "
                            "'I channel the will of the faithful to advance the ritual!'")
        else:
            announcement = "Orpheus gathers mystical energy for the next phase."

        await ctx.send(f"🔮 **{announcement}**")


# ---------- The Cog (standalone) ----------

class Elysium(commands.Cog):
    """Elysium raid – standalone cog (Thanatos)."""

    def __init__(self, bot):
        self.bot = bot

    async def get_player_decision(self, player, options, role, embed: discord.Embed, timeout: int = 90):
        """DM a view with buttons to the player and await their choice."""
        try:
            view = DecisionView(player, options, timeout=timeout)
            dm = await player.create_dm()
            await dm.send(embed=embed, view=view)
            for _ in range(timeout):
                await asyncio.sleep(1)
                if view.value:
                    return view.value
            return options[0]
        except discord.Forbidden:
            return options[0]

    @is_god()
    @commands.command(hidden=True, brief=_("Begin the Elysium Requiem"), name="elysium")
    async def elysium(self, ctx: commands.Context):
        """Begin the Elysium ritual for Shades of Thanatos."""

        RAID_IMG = "https://i.imgur.com/N4JtYIi.png"     # spawn + winner
        TARTAROS_IMG = "https://i.imgur.com/cxj8kaV.png" # during raid

        try:
            # Optional: ping a role & channel before the join message (allows actual ping)
            channel1 = self.bot.get_channel(1415390006457143466)
            role_id1 = 1415388580171219094
            if channel1:
                await channel1.send(
                    f"<@&{role_id1}> A requiem begins. Assemble, Shades of Thanatos.",
                    allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False),
                )

            # --- Dual join view (followers vs leaders) ---
            class DualJoinView(View):
                def __init__(self):
                    super().__init__(timeout=60 * 15)
                    self.follower_joined: list[discord.User | discord.Member] = []
                    self.leader_joined: list[discord.User | discord.Member] = []

                @discord.ui.button(label="Join as Follower Only", style=ButtonStyle.secondary, custom_id="thanatos_join_follower")
                async def follower_button(self, interaction: discord.Interaction, button: Button):
                    try:
                        await interaction.response.defer(ephemeral=True, thinking=False)
                    except discord.InteractionResponded:
                        pass
                    if interaction.user not in self.follower_joined and interaction.user not in self.leader_joined:
                        self.follower_joined.append(interaction.user)
                        await interaction.followup.send(_("You have joined as a follower."), ephemeral=True)
                    else:
                        await interaction.followup.send(_("You have already joined the ritual."), ephemeral=True)

                @discord.ui.button(label="Join as Eurydice/Orpheus", style=ButtonStyle.primary, custom_id="thanatos_join_leader")
                async def leader_button(self, interaction: discord.Interaction, button: Button):
                    try:
                        await interaction.response.defer(ephemeral=True, thinking=False)
                    except discord.InteractionResponded:
                        pass
                    if interaction.user not in self.follower_joined and interaction.user not in self.leader_joined:
                        self.leader_joined.append(interaction.user)
                        await interaction.followup.send(_("You have joined as a potential leader."), ephemeral=True)
                    else:
                        await interaction.followup.send(_("You have already joined the ritual."), ephemeral=True)

            dual_view = DualJoinView()

            # --- Opening embed ---
            embed = Embed(
                title="🌑 Beginning Raid",
                description=(
                    'You can hear the voice of Death in your head: "It is time to test your faith and conviction", '
                    "a low whisper followed by the booming laughter from the entity deep below.\n\n"
                    "Elysium is just a step ahead, but eternal damnation awaits should you fail. "
                    "Are you going to prove worthy?\n\n"
                    "**Only the Shades of Thanatos may partake in this Ritual.**\n\n"
                    "**Choose your role in this requiem:**\n"
                    "• **Join as Follower Only** — Support the ritual from the shadows\n"
                    "• **Join as Eurydice/Orpheus** — Lead the ritual and face Tartaros directly"
                ),
                color=0x550000
            )
            embed.set_image(url=RAID_IMG)
            await ctx.send(embed=embed, view=dual_view)

            # --- Countdown ---
            await asyncio.sleep(300); await ctx.send("**The darkness draws near... Get ready for the ritual in 10 minutes.**")
            await asyncio.sleep(300); await ctx.send("**The hopeful are gathering to join the fight... 5 minutes remain.**")
            await asyncio.sleep(180); await ctx.send("**Tartaros has almost reached you... 2 minutes until the ritual commences.**")
            await asyncio.sleep(60);  await ctx.send("**The air grows thick with dread... 1 minute left.**")
            await asyncio.sleep(30);  await ctx.send("**Shadows encroach you... 30 seconds.**")
            await asyncio.sleep(20);  await ctx.send("**The light flickers as Tartaros emerges... 10 seconds.**")
            await asyncio.sleep(10)

            dual_view.stop()
            await ctx.send("**💀 The ritual begins! Stand your ground! 💀**")
            await ctx.send(embed=discord.Embed(title="The Requiem Begins", color=0x550000).set_image(url=RAID_IMG))

            # --- Build participant stats (Thanatos-only) ---
            raid: dict[discord.User | discord.Member, dict] = {}
            HowMany = 0

            def progress_bar(current, total, bar_length=10):
                progress = (current / total)
                arrow = '⬛'
                space = '⬜'
                num_of_arrows = int(progress * bar_length)
                return arrow * num_of_arrows + space * (bar_length - num_of_arrows)

            async with self.bot.pool.acquire() as conn:
                for u in dual_view.follower_joined + dual_view.leader_joined:
                    profile = await conn.fetchrow('SELECT * FROM profile WHERE "user"=$1;', u.id)
                    if not profile or profile["god"] != "Thanatos":
                        continue
                    HowMany += 1
                    try:
                        dmg, deff = await self.bot.get_raidstats(
                            u,
                            atkmultiply=profile["atkmultiply"],
                            defmultiply=profile["defmultiply"],
                            classes=profile["class"],
                            race=profile["race"],
                            guild=profile["guild"],
                            god=profile["god"],
                            xp=profile["xp"],
                            conn=conn,
                        )
                    except ValueError:
                        continue
                    raid[u] = {"hp": 250, "armor": deff, "damage": dmg}

            async def is_valid_participant(user, conn):
                profile = await conn.fetchrow('SELECT * FROM profile WHERE "user"=$1;', user.id)
                return bool(profile and profile["god"] == "Thanatos")

            await ctx.send("**Gathering the faithful Shades... checking DM eligibility this may take awhile**")
            embed_message_id = None
            async with self.bot.pool.acquire() as conn:
                all_participants = dual_view.follower_joined + dual_view.leader_joined
                participants = [u for u in all_participants if await is_valid_participant(u, conn)]

            if not participants:
                await ctx.send("No valid Shades joined the ritual.")
                return

            for participant in participants.copy():
                try:
                    await participant.send("You have joined the ritual! Stay tuned for more info.")
                    await asyncio.sleep(0.5)
                except (discord.Forbidden, discord.HTTPException):
                    participants.remove(participant)
                    await ctx.send(
                        f"{participant.mention} was removed because I could not send them a DM. "
                        "Please enable your DMs or unblock the bot to participate."
                    )

            if not participants:
                await ctx.send("All participants have been removed. The ritual is canceled.")
                return

            await ctx.send(content=f"**{HowMany} followers joined!**")

            leader_participants = [u for u in dual_view.leader_joined if u in participants]
            follower_participants = [u for u in dual_view.follower_joined if u in participants]

            # --- Role assignment (AI fallback ensures both roles if none) ---
            if not leader_participants:
                shadow_champion = ShadowChampionAI()
                shadow_priest = ShadowPriestAI()
                champion = shadow_champion
                priest = shadow_priest
                followers = participants
                await ctx.send(embed=discord.Embed(
                    title="👻 Chthonic Entities Manifest 👻",
                    description=(
                        "No mortals dared to lead the ritual. From the depths of Death’s will, "
                        "chthonic entities materialize to guide the faithful through this requiem.\n\n"
                        "**Eurydice and Orpheus have emerged from the veil.**"
                    ),
                    color=0x8B0000
                ))
            else:
                champion = randomm.choice(leader_participants)
                leader_participants.remove(champion)
                priest = randomm.choice(leader_participants) if leader_participants else None
                if not priest:
                    priest = ShadowPriestAI()  # ensure Orpheus exists
                else:
                    leader_participants.remove(priest)
                followers = follower_participants + leader_participants

            # --- Announcements ---
            champion_embed = discord.Embed(
                title="👑  Eurydice  👑",
                description=f"{getattr(champion, 'mention', 'Eurydice')} has been marked by Death as **Eurydice**!",
                color=0x550000
            )
            await ctx.send(embed=champion_embed)

            if priest:
                priest_embed = discord.Embed(
                    title="🔮 Orpheus 🔮",
                    description=f"{getattr(priest, 'mention', 'Orpheus')} has been picked as **Orpheus**!",
                    color=0x550000
                )
                await ctx.send(embed=priest_embed)

            if followers:
                follower_embed = discord.Embed(
                    title="🕯️ The sneaking Shades 🕯️",
                    description="\n".join(f"{f.mention}" for f in followers),
                    color=0x550000
                )
                await ctx.send(embed=follower_embed)
            else:
                await ctx.send("**No Shades are participating. The ritual relies solely on Orpheus and Eurydice.**")

            # --- Role sheets / help ---
            EVIL_RITUAL_COLOR = discord.Color.dark_red()

            ritual_embed_help = discord.Embed(
                title="🌑 The Ritual to Elysium 🌑",
                description=("The hour is nigh. Unite your efforts to begin your first step towards the blessed Isles. "
                             "But beware, **Tartaros** will stop at nothing to prevent the completion of the requiem."),
                color=EVIL_RITUAL_COLOR
            )
            ritual_embed_help.add_field(
                name="Warning",
                value="If **Eurydice** falls, all hope is lost. Protect them with your lives!"
            )
            await ctx.send(embed=ritual_embed_help)

            champion_embed_help = discord.Embed(
                title="🛡️ Role: Eurydice 🛡️",
                description=("You are the vessel to break free from Tartaros' grip. Your survival is paramount. "
                             "Lead your Shades and withstand Tartaros's assault."),
                color=EVIL_RITUAL_COLOR
            )
            champion_embed_help.add_field(name="⚔️ Smite", value="Unleash dark power upon Tartaros.", inline=False)
            champion_embed_help.add_field(name="❤️ Heal", value="Draw upon shadows to mend your wounds.", inline=False)
            champion_embed_help.add_field(name="🌀 Haste", value="Accelerate the ritual's progress. (Cooldown applies; makes you vulnerable)", inline=False)
            champion_embed_help.add_field(name="🛡️ Defend", value="Brace yourself, reducing incoming damage next turn.", inline=False)
            champion_embed_help.add_field(name="💔 Sacrifice", value="Offer your undead life force to significantly advance the ritual.", inline=False)

            followers_embed_help = discord.Embed(
                title="🔮 Role: Shades 🔮",
                description="Your devotion fuels the ritual. Support Eurydice and Orpheus through any means necessary.",
                color=EVIL_RITUAL_COLOR
            )
            followers_embed_help.add_field(name="🌌 Boost Ritual", value="Channel your energy to hasten the ritual.", inline=False)
            followers_embed_help.add_field(name="🛡️ Protect Eurydice", value="Use your collective will to shield Eurydice.", inline=False)
            followers_embed_help.add_field(name="💥 Empower Orpheus", value="Enhance Orpheus's songs.", inline=False)
            followers_embed_help.add_field(name="🌀 Trip Tartaros", value="Undermine Tartaros's strength.", inline=False)
            followers_embed_help.add_field(name="🎵 Chant", value="Raise your voices to amplify the ritual's power.", inline=False)
            followers_embed_help.add_field(name="💉 Heal Eurydice", value="Offer some of your vitality to heal Eurydice.", inline=False)

            priest_embed_help = discord.Embed(
                title="🌙 Role: Orpheus 🌙",
                description="Master the chthonic arts to sway the requiem’s outcome. Your songs and sorrows are pivotal.",
                color=EVIL_RITUAL_COLOR
            )
            priest_embed_help.add_field(name="🔥 Bless", value="Imbue Eurydice with the strength of lament.", inline=False)
            priest_embed_help.add_field(name="🔮 Barrier", value="Raise a veil of the underworld to shield Eurydice.", inline=False)
            priest_embed_help.add_field(name="😵 Curse", value="Call upon the shadows of Hades to afflict Tartaros.", inline=False)
            priest_embed_help.add_field(name="❤️ Revitalize", value="Invoke the power of grief to restore Eurydice’s strength.", inline=False)
            priest_embed_help.add_field(name="🌟 Channel", value="Pour your song into the requiem, greatly advancing its completion.", inline=False)

            # DM role sheets
            if not isinstance(champion, ShadowChampionAI):
                try: await champion.send(embed=champion_embed_help)
                except: pass
            if priest and not isinstance(priest, ShadowPriestAI):
                try: await priest.send(embed=priest_embed_help)
                except: pass
            for follower in followers:
                try: await follower.send(embed=followers_embed_help)
                except: pass

            # --- Combat loop setup ---
            TOTAL_TURNS = 25

            default_champion_damage = 750
            champion_stats = {
                "hp": 1500,
                "damage": default_champion_damage,
                "protection": False,
                "shield_points": 0,
                "barrier_active": False,
                "max_hp": 1500,
                "healing_rate": 200,
                "haste_cooldown": 0,
                "vulnerable": False,
                "defending": False,
                "name": "Eurydice"
            }

            GUARDIAN_PHASES = {
                1: {
                    "name": "The Sentinel",
                    "description": "A towering figure emerges, cloaked in ancient armor. Its eyes glow with a cold light.",
                    "abilities": ["strike", "shield", "purify"],
                    "progress_threshold": 10
                },
                2: {
                    "name": "The Mourning Shade",
                    "description": (
                        "Tartaros wails with grief, its form dissolving into mist and shadow. "
                        "The air fills with whispers of the dead, each step heavier with sorrow."
                    ),
                    "abilities": ["strike", "corrupting_blast", "shadow_shield", "purify", "fear_aura"],
                    "progress_threshold": 30
                },
                3: {
                    "name": "The Final Requiem",
                    "description": (
                        "The final form of Tartaros rises — a void crowned in silence, a maw of eternity. "
                        "The underworld itself seems to hold its breath."
                    ),
                    "abilities": ["obliterate", "dark_aegis", "soul_drain", "apocalyptic_roar"],
                    "progress_threshold": 60
                }
            }

            guardians_stats = {
                "hp": 5000,
                "max_hp": 5000,
                "cursed": False,
                "damage_multiplier": 1.0,
                "shield_active": False,
                "base_damage": 150,
                "regeneration_rate": 500,
                "enraged": False,
                "phase": 1,
                "incapacitated_turns": 0
            }

            TIMEOUT = 90
            priest_stats = {
                "healing_boost": 1.0,
                "mana": 100,
                "max_mana": 100,
                "name": "Orpheus"
            }

            def apply_damage_with_protection(target_stats, damage):
                if target_stats.get("protection"):
                    shield_absorption = min(damage, target_stats.get("shield_points", 0))
                    target_stats["shield_points"] -= shield_absorption
                    damage_after_shield = damage - shield_absorption
                    if target_stats["shield_points"] <= 0:
                        target_stats["protection"] = False
                        target_stats["shield_points"] = 0
                else:
                    damage_after_shield = damage
                target_stats["hp"] -= damage_after_shield

            progress = 0

            # Initial Tartaros appearance
            phase_info = GUARDIAN_PHASES[guardians_stats["phase"]]
            first_appearance = discord.Embed(
                title=f"💀 {phase_info['name']} Appears 💀",
                description=phase_info["description"],
                color=0x550000
            )
            first_appearance.set_image(url=TARTAROS_IMG)
            await ctx.send(embed=first_appearance)

            # ===== Main turn loop =====
            for turn in range(TOTAL_TURNS):
                if champion_stats["hp"] <= 0:
                    await ctx.send(f"💔 {getattr(champion, 'mention', 'Eurydice')} has fallen. The requiem falters as the veil closes...")
                    return

                if progress >= 100:
                    break

                # Followers aggregate (new labels)
                follower_combined_decision = {
                    "Boost Ritual": 0,
                    "Protect Eurydice": 0,
                    "Empower Orpheus": 0,
                    "Trip Tartaros": 0,
                    "Chant": 0,
                    "Heal Eurydice": 0
                }

                # -------- Orpheus turn --------
                priest_decision = None
                if priest:
                    if isinstance(priest, ShadowPriestAI):
                        await ctx.send(f"🔮 **{priest.mention} contemplates the mystical energies...**")
                        priest_decision = await priest.make_decision(
                            priest_stats, champion_stats, guardians_stats, progress, follower_combined_decision
                        )
                        await priest.announce_decision(
                            ctx, priest_decision, priest_stats, champion_stats, guardians_stats, progress
                        )
                    else:
                        priest_abilities = {
                            "Bless":      {"description": "Strengthen Eurydice with the power of lament", "mana_cost": 20},
                            "Barrier":    {"description": "Weave a veil to protect Eurydice",              "mana_cost": 30},
                            "Curse":      {"description": "Call upon Hades’ shadows to weaken Tartaros",   "mana_cost": 25},
                            "Revitalize": {"description": "Restore Eurydice through grief’s song",         "mana_cost": 20},
                            "Channel":    {"description": "Greatly advance the requiem’s completion",      "mana_cost": 15},
                        }
                        decision_embed = discord.Embed(
                            title="🔮 Orpheus’ Turn 🔮",
                            description=f"{getattr(priest,'mention','Orpheus')}, your chthonic knowledge is needed. Choose your action:",
                            color=discord.Color.dark_purple()
                        )
                        for ability, info in priest_abilities.items():
                            if priest_stats["mana"] >= info["mana_cost"]:
                                decision_embed.add_field(
                                    name=f"{ability} (Cost: {info['mana_cost']} Mana)",
                                    value=info["description"], inline=False
                                )
                        decision_embed.set_footer(text=f"Mana: {priest_stats['mana']}/{priest_stats['max_mana']}")

                        if not isinstance(priest, ShadowPriestAI):
                            await ctx.send(f"It's {getattr(priest,'mention','Orpheus')}'s turn to make a decision, check DMs!")
                        valid_priest_options = [a for a, i in priest_abilities.items() if priest_stats["mana"] >= i["mana_cost"]]
                        if valid_priest_options:
                            try:
                                priest_decision = await asyncio.wait_for(
                                    self.get_player_decision(priest, valid_priest_options, "priest", decision_embed, TIMEOUT),
                                    timeout=TIMEOUT
                                )
                            except asyncio.TimeoutError:
                                await ctx.send(f"{getattr(priest,'mention','Orpheus')} took too long! Moving on...")
                                priest_decision = None

                    if priest_decision:
                        costs = {"Bless": 20, "Barrier": 30, "Curse": 25, "Revitalize": 20, "Channel": 15}
                        priest_stats["mana"] -= costs[priest_decision]
                        if priest_decision == "Bless":
                            champion_stats["damage"] += 200 * priest_stats["healing_boost"]
                            await ctx.send("✨ Orpheus blesses Eurydice, increasing their power!")
                        elif priest_decision == "Barrier":
                            champion_stats["barrier_active"] = True
                            await ctx.send("🛡️ Orpheus raises a veil of the underworld to shield Eurydice!")
                        elif priest_decision == "Curse":
                            guardians_stats["cursed"] = True
                            await ctx.send("🔒 Orpheus calls upon Hades' shadows to weaken Tartaros!")
                        elif priest_decision == "Revitalize":
                            heal_amount = int(300 * priest_stats["healing_boost"])
                            champion_stats["hp"] = min(champion_stats["hp"] + heal_amount, champion_stats["max_hp"])
                            await ctx.send(f"❤️ Orpheus restores Eurydice for {heal_amount} HP!")
                        elif priest_decision == "Channel":
                            progress += 5
                            await ctx.send("🌟 Orpheus pours song into the requiem, advancing it!")

                # If Tartaros is dropped to 0, mark incapacitated
                if guardians_stats["hp"] <= 0 and guardians_stats["incapacitated_turns"] == 0:
                    guardians_stats["incapacitated_turns"] = 2
                    await ctx.send("💀 Tartaros collapses, giving you a brief respite!")
                    progress += 10

                # -------- Tartaros turn --------
                if guardians_stats["incapacitated_turns"] > 0:
                    guardians_stats["incapacitated_turns"] -= 1
                    if guardians_stats["incapacitated_turns"] == 0:
                        guardians_stats["hp"] = int(guardians_stats["max_hp"] * 0.5)
                        guardians_stats["damage_multiplier"] += 0.2
                        await ctx.send("😈 Tartaros rises again, more enraged than ever!")
                        if guardians_stats["phase"] < 3:
                            guardians_stats["phase"] += 1
                            phase_info = GUARDIAN_PHASES[guardians_stats["phase"]]
                            phase_embed = discord.Embed(
                                title=f"😈 Tartaros Transforms into {phase_info['name']}!",
                                description=phase_info["description"],
                                color=0x8B0000
                            )
                            phase_embed.set_image(url=TARTAROS_IMG)
                            await ctx.send(embed=phase_embed)
                    else:
                        await ctx.send("💀 Tartaros is incapacitated and cannot act this turn.")
                else:
                    await ctx.send("💢 Tartaros takes its turn.")
                    current_phase = guardians_stats["phase"]
                    next_phase = current_phase + 1
                    phase_info = GUARDIAN_PHASES[current_phase]

                    # Phase change based on progress
                    if next_phase in GUARDIAN_PHASES:
                        phase_info_next = GUARDIAN_PHASES[next_phase]
                        if progress >= phase_info_next["progress_threshold"]:
                            guardians_stats["phase"] = next_phase
                            guardians_stats["damage_multiplier"] += 0.3
                            phase_embed = discord.Embed(
                                title=f"😈 Tartaros Transforms into {phase_info_next['name']}!",
                                description=phase_info_next["description"],
                                color=0x8B0000
                            )
                            phase_embed.set_image(url=TARTAROS_IMG)
                            await ctx.send(embed=phase_embed)
                            phase_info = phase_info_next

                    decisions = phase_info["abilities"]
                    if progress >= 80 and "purify" in decisions:
                        guardian_decision = "purify"
                    else:
                        guardian_decision = randomm.choice(decisions)

                    # Execute Tartaros action
                    if guardian_decision == "strike":
                        damage = randomm.randint(100, 250) * guardians_stats["damage_multiplier"]
                        if guardians_stats.get("enraged"):
                            damage *= 1.5
                        if champion_stats.get("barrier_active"):
                            damage *= 0.5
                            champion_stats["barrier_active"] = False
                        if champion_stats.get("defending"):
                            damage *= 0.5
                            champion_stats["defending"] = False
                        if champion_stats.get("vulnerable"):
                            damage *= 1.5
                            champion_stats["vulnerable"] = False
                        apply_damage_with_protection(champion_stats, int(damage))
                        await ctx.send(f"💥 Tartaros strikes Eurydice for **{int(damage)} damage**!")

                    elif guardian_decision == "corrupting_blast":
                        damage = randomm.randint(150, 250) * guardians_stats["damage_multiplier"]
                        champion_stats["damage"] = max(champion_stats["damage"] - 100, 0)
                        apply_damage_with_protection(champion_stats, int(damage))
                        await ctx.send(
                            f"⚡ Tartaros unleashes a Corrupting Blast, dealing **{int(damage)} damage** and reducing Eurydice's damage!"
                        )

                    elif guardian_decision == "shadow_shield":
                        guardians_stats["shield_active"] = True
                        guardians_stats["damage_multiplier"] *= 0.8
                        await ctx.send("🛡️ Tartaros cloaks itself in shadows, reducing incoming damage by **20%**!")

                    elif guardian_decision == "obliterate":
                        damage = randomm.randint(400, 900) * guardians_stats["damage_multiplier"]
                        apply_damage_with_protection(champion_stats, int(damage))
                        await ctx.send(
                            f"☠️ Tartaros attempts to obliterate Eurydice, dealing **{int(damage)} damage**!"
                        )

                    elif guardian_decision == "dark_aegis":
                        guardians_stats["shield_active"] = True
                        guardians_stats["damage_multiplier"] *= 0.5
                        await ctx.send("🔰 Tartaros conjures a Dark Aegis, greatly reducing damage by **50%**!")

                    elif guardian_decision == "soul_drain":
                        damage = randomm.randint(200, 300)
                        guardians_stats["hp"] = min(guardians_stats["hp"] + damage, guardians_stats["max_hp"])
                        apply_damage_with_protection(champion_stats, damage)
                        await ctx.send(
                            f"🩸 Tartaros uses Soul Drain, siphoning **{damage} HP** and dealing **{damage} damage**!"
                        )

                    elif guardian_decision == "purify":
                        before = progress
                        progress = max(0, progress - 20)
                        await ctx.send(
                            f"💫 Tartaros attempts to purge the requiem, reducing progress by **{before - progress}%**!"
                        )

                    elif guardian_decision == "shield":
                        guardians_stats["shield_active"] = True
                        await ctx.send("🛡️ Tartaros raises a shield, preparing to absorb incoming damage!")

                    elif guardian_decision == "fear_aura":
                        await ctx.send("😱 A suffocating fear spreads from Tartaros, unsettling the Shades!")

                    elif guardian_decision == "apocalyptic_roar":
                        damage = randomm.randint(150, 250)
                        champion_stats["hp"] -= damage
                        await ctx.send(
                            f"🌋 Tartaros unleashes an Apocalyptic Roar, dealing **{damage} damage** to Eurydice!"
                        )

                # -------- Shades turn (new labels) --------
                await ctx.send("🙏 The Shades are making their decisions.")
                follower_embed = discord.Embed(
                    title="🕯️ Shades’ Actions 🕯️",
                    description="Choose your action to support the requiem:",
                    color=discord.Color.purple()
                )
                follower_embed.add_field(name="🔆 Boost Ritual",        value="Increase the ritual's progress", inline=True)
                follower_embed.add_field(name="🛡️ Protect Eurydice",   value="Provide a shield to Eurydice",   inline=True)
                follower_embed.add_field(name="🌟 Empower Orpheus",     value="Amplify Orpheus' next action",   inline=True)
                follower_embed.add_field(name="🌀 Trip Tartaros",       value="Disrupt Tartaros' next move",    inline=True)
                follower_embed.add_field(name="🎶 Chant",               value="Contribute to the ritual's power", inline=True)
                follower_embed.add_field(name="💉 Heal Eurydice",       value="Heal Eurydice a small amount",    inline=True)

                async def get_follower_decision(follower):
                    decision = await self.get_player_decision(
                        player=follower,
                        options=list(follower_combined_decision.keys()),
                        role="follower",
                        embed=follower_embed,
                        timeout=TIMEOUT
                    )
                    return (follower, decision)

                gather_tasks = [get_follower_decision(f) for f in followers]
                results = await asyncio.gather(*gather_tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, Exception):
                        continue
                    follower, decision = result
                    if decision in follower_combined_decision:
                        follower_combined_decision[decision] += 1

                if follower_combined_decision["Boost Ritual"]:
                    delta = min(2 * follower_combined_decision["Boost Ritual"], 8)
                    progress += delta
                    await ctx.send(f"🔺 Shades boost the ritual by {delta}%!")

                if follower_combined_decision["Protect Eurydice"] > 0:
                    champion_stats["protection"] = True
                    champion_stats["shield_points"] += 50 * follower_combined_decision["Protect Eurydice"]
                    await ctx.send(f"🛡️ Shades shield Eurydice with {champion_stats['shield_points']} points!")

                if follower_combined_decision["Empower Orpheus"] > 0 and priest:
                    priest_stats["healing_boost"] += 0.1 * follower_combined_decision["Empower Orpheus"]
                    await ctx.send("✨ Shades empower Orpheus!")

                if follower_combined_decision["Trip Tartaros"] > 0:
                    guardians_stats["damage_multiplier"] -= 0.1 * follower_combined_decision["Trip Tartaros"]
                    guardians_stats["damage_multiplier"] = max(0.5, guardians_stats["damage_multiplier"])
                    await ctx.send("🌀 Shades trip up Tartaros, reducing its damage!")

                if follower_combined_decision["Chant"]:
                    delta = follower_combined_decision["Chant"]
                    progress += delta
                    await ctx.send(f"🎵 Shades chant, increasing the requiem by {delta}%!")

                if follower_combined_decision["Heal Eurydice"]:
                    total_healing = 50 * follower_combined_decision["Heal Eurydice"]
                    champion_stats["hp"] = min(champion_stats["hp"] + total_healing, champion_stats["max_hp"])
                    await ctx.send(f"💖 Shades heal Eurydice for {total_healing} HP!")

                # -------- Eurydice turn --------
                if isinstance(champion, ShadowChampionAI):
                    await ctx.send(f"👻 **{getattr(champion, 'mention', 'Eurydice')} analyzes the battlefield...**")
                    champion_decision = await champion.make_decision(
                        champion_stats, guardians_stats, progress, follower_combined_decision
                    )
                    await champion.announce_decision(ctx, champion_decision, champion_stats, guardians_stats, progress)
                else:
                    champion_embed = discord.Embed(
                        title="⚔️ Eurydice’s Turn ⚔️",
                        description=f"{getattr(champion,'mention','Eurydice')}, choose your action:",
                        color=discord.Color.red()
                    )
                    champion_embed.add_field(name="⚡ Smite", value="Deal damage to Tartaros", inline=True)
                    champion_embed.add_field(name="❤️ Heal", value="Recover some of your lost HP", inline=True)
                    haste_description = "Boost the requiem’s progress"
                    if champion_stats["haste_cooldown"] > 0:
                        haste_description += f" (Cooldown: {champion_stats['haste_cooldown']} turns)"
                    champion_embed.add_field(name="🌀 Haste", value=haste_description, inline=True)
                    champion_embed.add_field(name="🛡️ Defend", value="Reduce incoming damage next turn", inline=True)
                    champion_embed.add_field(
                        name="💔 Sacrifice",
                        value="Advance the requiem by 20% at the cost of 400 HP",
                        inline=True
                    )
                    await ctx.send(f"It's {getattr(champion,'mention','Eurydice')}'s turn to make a decision, check DMs!")
                    valid_actions = ["Smite", "Heal", "Defend", "Sacrifice"]
                    if champion_stats["haste_cooldown"] == 0:
                        valid_actions.append("Haste")
                    else:
                        try:
                            await champion.send(f"'Haste' is on cooldown for {champion_stats['haste_cooldown']} more turns.")
                        except discord.Forbidden:
                            pass

                    try:
                        champion_decision = await asyncio.wait_for(
                            self.get_player_decision(champion, valid_actions, "champion", champion_embed, TIMEOUT),
                            timeout=TIMEOUT
                        )
                    except asyncio.TimeoutError:
                        await ctx.send(f"{getattr(champion,'mention','Eurydice')} took too long to decide! Defaulting to 'Smite'.")
                        champion_decision = "Smite"

                # Execute Eurydice decision
                if champion_decision == "Smite":
                    dmg = champion_stats["damage"]
                    guardians_stats["hp"] -= dmg
                    if guardians_stats.get("shield_active"):
                        guardians_stats["hp"] += 200
                        guardians_stats["shield_active"] = False
                    await ctx.send(f"⚔️ Eurydice smites Tartaros for {dmg} damage!")

                elif champion_decision == "Heal":
                    heal_amount = 200
                    champion_stats["hp"] = min(champion_stats["hp"] + heal_amount, champion_stats["max_hp"])
                    await ctx.send(f"❤️ Eurydice heals for {heal_amount} HP!")

                elif champion_decision == "Haste":
                    progress += 15
                    champion_stats["haste_cooldown"] = 3
                    champion_stats["vulnerable"] = True
                    await ctx.send("🌀 Eurydice uses Haste, advancing the requiem but becoming vulnerable!")

                elif champion_decision == "Defend":
                    champion_stats["defending"] = True
                    await ctx.send("🛡️ Eurydice braces for the next attack!")

                elif champion_decision == "Sacrifice":
                    damage_to_self = 400
                    champion_stats["hp"] -= damage_to_self
                    progress += 20
                    await ctx.send(f"💔 Eurydice sacrifices {damage_to_self} HP to advance the requiem!")

                # Cooldowns / statuses
                if champion_stats["haste_cooldown"] > 0:
                    champion_stats["haste_cooldown"] -= 1

                # ---- Status embed ----
                phase_info = GUARDIAN_PHASES[guardians_stats["phase"]]
                progress_color = 0x4CAF50 if progress >= 80 else 0xFFC107 if progress >= 50 else 0xFF5722
                if progress >= 100 and champion_stats["hp"] > 0:
                    progress = 100
                em = discord.Embed(
                    title="🌑 Ritual Progress 🌑",
                    description=f"Turn {turn + 1}/{TOTAL_TURNS}",
                    color=progress_color
                )
                ritual_status = f"{progress_bar(progress, 100)} ({int(progress)}%)"
                champion_status = f"❤️ {int(champion_stats['hp'])}/{champion_stats['max_hp']} HP"
                guardians_status = f"😈 {phase_info['name']} ({int(guardians_stats['hp'])}/{guardians_stats['max_hp']} HP)"
                em.add_field(name="🔮 Requiem Completion", value=ritual_status, inline=False)
                em.add_field(name="🛡️ Eurydice", value=champion_status, inline=True)
                em.add_field(name="💀 Tartaros", value=guardians_status, inline=True)

                if champion_stats.get("damage") > default_champion_damage:
                    em.add_field(name="Orpheus' Blessing", value="🔥 Eurydice's power boosted", inline=True)
                if champion_stats.get("barrier_active"):
                    em.add_field(name="Orpheus' Veil", value="🔰 Eurydice Protected", inline=True)
                if guardians_stats.get("cursed"):
                    em.add_field(name="Orpheus' Curse", value="😵 Tartaros Weakened", inline=True)
                if guardians_stats.get("shield_active"):
                    em.add_field(name="Tartaros' Shield", value="🔰 Active", inline=True)
                if guardians_stats.get("enraged"):
                    em.add_field(name="Tartaros Enraged", value="🔥 Increased Damage", inline=True)
                if champion_stats.get("vulnerable"):
                    em.add_field(name="Eurydice Vulnerable", value="⚠️ Increased Damage Taken", inline=True)
                if guardians_stats.get("incapacitated_turns", 0) > 0:
                    em.add_field(
                        name="Tartaros Incapacitated",
                        value=f"🛌 Incapacitated for {guardians_stats['incapacitated_turns']} more turn(s)",
                        inline=True
                    )

                if turn != 0 and embed_message_id:
                    try:
                        old_message = await ctx.channel.fetch_message(embed_message_id)
                        await old_message.delete()
                    except discord.NotFound:
                        pass

                message = await ctx.send(embed=em)
                embed_message_id = message.id

                # Decision summary embed
                decision_embed = discord.Embed(
                    title="🕯️ Actions This Turn 🕯️",
                    description="An overview of this turn's actions.",
                    color=0x8B0000
                )
                if priest:
                    decision_embed.add_field(
                        name="🔮 Orpheus",
                        value=(priest_decision or "No action"),
                        inline=False
                    )

                guardian_action = "Incapacitated" if guardians_stats["incapacitated_turns"] > 0 else "Acted"
                followers_decisions_text = "\n".join(
                    [f"{action}: {count}" for action, count in follower_combined_decision.items() if count > 0]
                ) or "No actions taken"

                decision_embed.add_field(name="💀 Tartaros", value=guardian_action, inline=False)
                decision_embed.add_field(name="🕯️ Shades", value=followers_decisions_text, inline=False)
                decision_embed.add_field(name="🛡️ Eurydice", value=champion_decision, inline=False)
                decision_embed.set_footer(text="The requiem’s energy intensifies...")

                await ctx.send(embed=decision_embed)

                # Cleanup / regen
                guardians_stats["damage_multiplier"] = 1.0
                if guardians_stats.get("cursed"):
                    del guardians_stats["cursed"]
                if champion_stats.get("damage") > default_champion_damage:
                    champion_stats["damage"] = default_champion_damage
                if champion_stats.get("protection") and champion_stats["shield_points"] <= 0:
                    champion_stats["protection"] = False
                if priest:
                    priest_stats["mana"] = min(priest_stats["mana"] + 10, priest_stats["max_mana"])

                await asyncio.sleep(15)

            # ===== Outcome =====
            if progress >= 100 and champion_stats["hp"] > 0:
                progress = 100

                users = [u.id for u in raid] or [p.id for p in participants]
                random_user = pick_weighted_reward_target(users, champion, priest)

                async with self.bot.pool.acquire() as conn:
                    luck_query = await conn.fetchval(
                        'SELECT luck FROM profile WHERE "user" = $1;', random_user
                    )

                luck_query_float = float(luck_query or 1.0)

                gods = {
                    "Hecate":   {"boundary_low": 0.8, "boundary_high": 1.2},
                    "Thanatos": {"boundary_low": 0.4, "boundary_high": 1.6},
                    "Morpheus": {"boundary_low": 0.0, "boundary_high": 2.0},
                    "Apollo":   {"boundary_low": 0.0, "boundary_high": 2.0},
                }
                selected_god = "Thanatos"
                god_data = gods[selected_god]
                boundary_low = god_data["boundary_low"]
                boundary_high = god_data["boundary_high"]

                normalized_luck = (luck_query_float - boundary_low) / (boundary_high - boundary_low)
                normalized_luck = max(0.0, min(1.0, normalized_luck))

                weightdivine = 0.20 + (0.20 * normalized_luck)
                rounded_weightdivine = round(weightdivine, 3)

                options = ['legendary', 'fortune', 'divine']
                weights = [0.40, 0.40, rounded_weightdivine]
                crate = randomm.choices(options, weights=weights)[0]

                # Winner embed
                win = Embed(
                    title="🌟 The Path to Elysium Opens 🌟",
                    description=(
                        "With a final swell of lament, the requiem crescendos. "
                        "The gates of Elysium shimmer in the distance as Tartaros recoils.\n\n"
                        f"As a boon from Death, one among you receives a **{crate} crate**. "
                        "All Shades are granted earthly riches for their devotion."
                    ),
                    color=0x901C1C
                )
                win.set_image(url=RAID_IMG)
                await ctx.send(embed=win)

                await ctx.send(
                    f"🎉 Congratulations, <@{random_user}>! You have been chosen to receive a **{crate} crate**!"
                )
                async with self.bot.pool.acquire() as conn:
                    await conn.execute(
                        f'UPDATE profile SET "crates_{crate}" = "crates_{crate}" + 1 WHERE "user" = $1;',
                        random_user,
                    )

                cash_reward = randomm.randint(20000, 50000)
                await self.bot.pool.execute(
                    'UPDATE profile SET money=money+$1 WHERE "user"=ANY($2);',
                    cash_reward,
                    users,
                )
                await ctx.send(
                    f"💰 All participants receive **${cash_reward}** for their devotion!"
                )

                if participants and randomm.random() < DIVINE_SHARD_RAID_PROC_CHANCE:
                    shard_target = pick_weighted_reward_target(participants, champion, priest)
                    familiar_key = DIVINE_SHARD_FAMILIAR_KEY
                    familiar_name = DIVINE_FAMILIARS[familiar_key]["name"]
                    async with self.bot.pool.acquire() as conn:
                        shard_total = await award_divine_shards(
                            conn,
                            shard_target.id,
                            familiar_key,
                            1,
                        )
                    await ctx.send(
                        f"✨ Death grants **1 {familiar_name} shard** to {shard_target.mention}! "
                        f"Now: **{shard_total}/20**"
                    )
            else:
                await ctx.send("💔 The requiem failed to reach completion. The veil holds as Tartaros prevails.")

        except Exception as e:
            await ctx.send(f"An error occurred: {e}")
            import traceback; traceback.print_exc()


# ---------- Extension setup ----------

async def setup(bot: commands.Bot):
    await bot.add_cog(Elysium(bot))
