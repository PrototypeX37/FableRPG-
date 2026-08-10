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

from utils.checks import is_god
from utils.divine_familiars import DIVINE_FAMILIARS, award_divine_shards
from utils.i18n import _
import random as randomm


DIVINE_SHARD_RAID_PROC_CHANCE = 0.15
DIVINE_SHARD_FAMILIAR_KEY = "astraea_familiar"
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


# ---------- Reusable decision UI ----------

class DecisionButton(Button):
    def __init__(self, label, *args, **kwargs):
        super().__init__(label=label, *args, **kwargs)

    async def callback(self, interaction: Interaction):
        view: "DecisionView" = self.view  # type: ignore
        view.value = self.custom_id
        try:
            await interaction.response.send_message(
                f"You selected {self.custom_id}. Shortcut back: <#1415389637047750729>",
                ephemeral=True,
            )
        except discord.InteractionResponded:
            await interaction.followup.send(
                f"You selected {self.custom_id}. Shortcut back: <#1415389637047750729>",
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


# ---------- Apollo Cog ----------

class ApolloSong(commands.Cog):
    """Apollo – The Subduing Song vs Python."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def get_player_decision(self, player, options, role, embed: discord.Embed, timeout: int = 90):
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

    # --- the command (PREFIX) ---

    @is_god()
    @commands.command(hidden=True, name="apollosong", brief=_("Begin the Subduing Song"))
    async def apollosong(self, ctx: commands.Context):
        """
        Begin the Subduing Song for the Chosen of Apollon.
        """

        # Images
        IMG_START = "https://i.imgur.com/OR5AeC8.png"   # start
        IMG_DURING = "https://i.imgur.com/89hkEwv.png"  # during (Python attacks)
        IMG_END = "https://i.imgur.com/s8lWT3m.png"     # ending

        # Announce/ping
        announce_channel_id = 1415389637047750729
        ping_role_id = 1415388833037418677

        try:
            channel = self.bot.get_channel(announce_channel_id)
            if channel:
                await channel.send(
                    f"<@&{ping_role_id}> **Chosen of Apollon**—assemble for the Subduing Song!",
                    allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False),
                )

            # ---- Join view ----
            class DualJoinView(View):
                def __init__(self):
                    super().__init__(timeout=60 * 15)
                    self.chosen_joined: list[discord.User | discord.Member] = []
                    self.leader_joined: list[discord.User | discord.Member] = []

                @discord.ui.button(label="Join as Chosen (Support)", style=ButtonStyle.secondary, custom_id="apollo_join_chosen")
                async def chosen_button(self, interaction: discord.Interaction, button: Button):
                    try:
                        await interaction.response.defer(ephemeral=True, thinking=False)
                    except discord.InteractionResponded:
                        pass
                    if interaction.user not in self.chosen_joined and interaction.user not in self.leader_joined:
                        self.chosen_joined.append(interaction.user)
                        await interaction.followup.send(_("You have joined the Subduing Song! Stay tuned for more info."), ephemeral=True)
                    else:
                        await interaction.followup.send(_("You have already joined the Song."), ephemeral=True)

                @discord.ui.button(label="Join as Champion/Priest", style=ButtonStyle.primary, custom_id="apollo_join_leader")
                async def leader_button(self, interaction: discord.Interaction, button: Button):
                    try:
                        await interaction.response.defer(ephemeral=True, thinking=False)
                    except discord.InteractionResponded:
                        pass
                    if interaction.user not in self.chosen_joined and interaction.user not in self.leader_joined:
                        self.leader_joined.append(interaction.user)
                        await interaction.followup.send(_("You have joined as a potential leader."), ephemeral=True)
                    else:
                        await interaction.followup.send(_("You have already joined the Song."), ephemeral=True)

            join_view = DualJoinView()

            # ---- Opening embed ----
            opening = Embed(
                title="🎶 The Subduing Song’s Call 🎶",
                description=(
                    "Chosen of **Apollon**, heed my call. The **Monstrous Serpent Python** has come to reclaim the Oracle—"
                    "this cannot come to pass!\n\n"
                    "Raise your weapons and voices to **subdue the Primordial Beast of Earth and Filth**.\n\n"
                    "_Apollo’s devout followers may raise their voices in song._\n\n"
                    "**Choose your role:**\n"
                    "• **Join as Chosen (Support)** — Lend your voice and aid\n"
                    "• **Join as Champion/Priest** — Lead the Song and face Python directly"
                ),
                color=0xF6C453
            )
            opening.set_image(url=IMG_START)
            await ctx.send(embed=opening, view=join_view)

            # ---- Countdown ----
            await asyncio.sleep(300); await ctx.send("**The earth shakes beneath your feet… The song begins in 10 minutes.**")
            await asyncio.sleep(300); await ctx.send("**A foul stench permeates the air... 5 minutes remain.**")
            await asyncio.sleep(180); await ctx.send("**A glimpse of scales, the eerie glow of Python’s eyes... 2 minutes to raise your voices.**")
            await asyncio.sleep(60);  await ctx.send("**Warmth permeates your skin, the air less filthy, the music begins to play... 1 minute left.**")
            await asyncio.sleep(30);  await ctx.send("**Light shines as the music begins to swell... 30 seconds.**")
            await asyncio.sleep(20);  await ctx.send("**You breathe, feeling the music in your heart and lungs... 10 seconds.**")
            await asyncio.sleep(10)

            join_view.stop()
            await ctx.send("**🎼  The voices rise as the Song begins! Python coils around the Temple, ensnaring the Oracle... 🐍**")

            # ---- Build participants (Apollo only) ----
            async def is_valid_apollo(user, conn):
                profile = await conn.fetchrow('SELECT * FROM profile WHERE "user"=$1;', user.id)
                return bool(profile and profile["god"] == "Apollo")

            chosen_total = 0
            raid_stats: dict[discord.User | discord.Member, dict] = {}

            async with self.bot.pool.acquire() as conn:
                for u in join_view.chosen_joined + join_view.leader_joined:
                    profile = await conn.fetchrow('SELECT * FROM profile WHERE "user"=$1;', u.id)
                    if not profile or profile["god"] != "Apollo":
                        continue
                    chosen_total += 1
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
                    raid_stats[u] = {"hp": 250, "armor": deff, "damage": dmg}

            await ctx.send("**Gathering Chosen... checking DM eligibility this may take awhile**")

            async with self.bot.pool.acquire() as conn:
                all_participants = join_view.chosen_joined + join_view.leader_joined
                participants = [u for u in all_participants if await is_valid_apollo(u, conn)]

            if not participants:
                await ctx.send("No valid participants joined the Song.")
                return

            for p in participants.copy():
                try:
                    await p.send("You have joined the Subduing Song! Stay tuned for more info.")
                    await asyncio.sleep(0.4)
                except (discord.Forbidden, discord.HTTPException):
                    participants.remove(p)
                    await ctx.send(
                        f"{p.mention} was removed because I could not send them a DM. "
                        "Please enable your DMs or unblock the bot to participate."
                    )

            if not participants:
                await ctx.send("All participants have been removed. The Song is canceled.")
                return

            await ctx.send(f"**{chosen_total} Chosen joined!**")

            leader_pool = [u for u in join_view.leader_joined if u in participants]
            support_pool = [u for u in join_view.chosen_joined if u in participants]

            # Roles (ensure both exist with simple AI placeholders if needed)
            if not leader_pool:
                champion = "The Prophecized Champion"
                priest = "The Delphic Priest"
                followers = participants
                await ctx.send(embed=discord.Embed(
                    title="☀️ Radiant Avatars Manifest ☀️",
                    description=(
                        "None stepped forward to lead. By Apollo’s grace, luminous avatars arise to guide the Song.\n\n"
                        "**The Prophecized Champion and the Delphic Priest take their places.**"
                    ),
                    color=0xF6C453
                ))
            else:
                champion = randomm.choice(leader_pool)
                leader_pool.remove(champion)
                priest = randomm.choice(leader_pool) if leader_pool else None
                if not priest:
                    priest = "The Delphic Priest"  # AI placeholder
                else:
                    leader_pool.remove(priest)
                followers = support_pool + leader_pool

            # Announcements
            champion_embed = discord.Embed(
                title="👑 The Prophecized Champion 👑",
                description=f"{getattr(champion, 'mention', 'The Prophecized Champion')} is given an aura of bright light as **The Prophecized Champion**!",
                color=0xF6C453
            )
            await ctx.send(embed=champion_embed)

            if priest:
                priest_embed = discord.Embed(
                    title="☀️  The Delphic Priest ☀️",
                    description=f"{getattr(priest, 'mention', 'The Delphic Priest')} has embraced the light as the music swells—they shall be our **Priest**!",
                    color=0xF6C453
                )
                await ctx.send(embed=priest_embed)
            else:
                await ctx.send("**No Priest was chosen. The battle will be more perilous without one.**")

            if followers:
                flw = discord.Embed(
                    title="🏹  The Chosen of Apollon 🏹 ",
                    description="\n".join(f"{f.mention}" for f in followers),
                    color=0xF6C453
                )
                await ctx.send(embed=flw)
            else:
                await ctx.send("**No Chosen are participating. The Song relies solely on the Champion and Priest’s determination.**")

            # Role sheets
            help_song = discord.Embed(
                title="🎶 The Subduing Song’s Beginning 🎶",
                description=(
                    "The time has come. Bring your voices together to subdue **Python**.\n"
                    "Stay vigilant—Python will strike and hiss to prevent the sacred song from reaching his ears."
                ),
                color=0xF6C453
            )
            help_song.add_field(
                name="Warning",
                value="Should the **Champion** fall, all will be lost. Protect them at all cost—for this is how we win!"
            )
            await ctx.send(embed=help_song)

            champion_help = discord.Embed(
                title="🛡️ Role: Champion 🛡️",
                description=(
                    "You are the subject of the prophecy. Your survival is paramount. "
                    "Lead the Chosen of Apollon and withstand **Python's** assault."
                ),
                color=0xF6C453
            )
            champion_help.add_field(name="⚔️ Smite", value="Unleash **piercing radiant light** upon Python.", inline=False)
            champion_help.add_field(name="❤️ Heal", value="Feel the warmth of the Sun and call on Apollo to mend your wounds.", inline=False)
            champion_help.add_field(name="🌀 Haste", value="Accelerate the Song's tempo, increasing progress. (Cooldown applies; makes you vulnerable)", inline=False)
            champion_help.add_field(name="🛡️ Defend", value="Brace yourself, reducing incoming damage next turn.", inline=False)
            champion_help.add_field(name="💔 Sacrifice", value="Offer your life force to significantly advance the Song as you’re struck with plague arrows.", inline=False)

            chosen_help = discord.Embed(
                title="🏹  Role: Chosen 🏹 ",
                description="Your voices and faith fuel the Song. Support the Champion and Priest through any means necessary.",
                color=0xF6C453
            )
            chosen_help.add_field(name="🌌 Boost Song", value="Harmonize with your voices to hasten the Song.", inline=False)
            chosen_help.add_field(name="🛡️ Protect Champion", value="Use your collective faith to shield the Champion.", inline=False)
            chosen_help.add_field(name="💥 Empower Priest", value="Enhance the Priest's holy authority.", inline=False)
            chosen_help.add_field(name="🔥 Sabotage Python", value="Undermine Python's strength.", inline=False)
            chosen_help.add_field(name="🎵 Crescendo", value="Raise your voices to amplify the Song's power.", inline=False)
            chosen_help.add_field(name="💉 Heal Champion", value="Offer some of your vitality to heal the Champion.", inline=False)

            priest_help = discord.Embed(
                title="🎼 Role: Priest 🌤️",
                description="Master the prophetic arts of music and light to sway the Song's outcome. Your spells are pivotal.",
                color=0xF6C453
            )
            priest_help.add_field(name="🔥 Bless", value="Imbue the Champion with prophetic might.", inline=False)
            priest_help.add_field(name="☀️  Barrier", value="Conjure a bright shield around the Champion.", inline=False)
            priest_help.add_field(name="😵 Curse", value="Afflict Python with Arrows of Plague.", inline=False)
            priest_help.add_field(name="❤️ Revitalize", value="Call on Apollo to heal the Champion.", inline=False)
            priest_help.add_field(name="🔮 Channel", value="Focus your energy to significantly boost the Song’s progress.", inline=False)

            # DM sheets
            if not isinstance(champion, str):
                try: await champion.send(embed=champion_help)
                except: pass
            if priest and not isinstance(priest, str):
                try: await priest.send(embed=priest_help)
                except: pass
            for f in followers:
                try: await f.send(embed=chosen_help)
                except: pass

            # Combat setup
            TOTAL_TURNS = 25
            default_champion_damage = 750
            champ_stats = {
                "hp": 1500, "max_hp": 1500, "damage": default_champion_damage,
                "barrier_active": False, "defending": False,
                "protection": False, "shield_points": 0,
                "haste_cooldown": 0, "vulnerable": False,
                "name": "Champion"
            }
            priest_stats = {"mana": 100, "max_mana": 100, "healing_boost": 1.0, "name": "Priest"}
            guardians = {
                "hp": 5000, "max_hp": 5000, "phase": 1,
                "damage_multiplier": 1.0, "shield_active": False,
                "cursed": False, "enraged": False, "incapacitated_turns": 0
            }

            PHASES = {
                1: {
                    "name": "Python",
                    "description": "Python looks upon you, wretched scales shimmering in sunlight. Its eyes glow a menacing red.",
                    "abilities": ["strike", "shield", "purify"],
                    "progress_threshold": 10
                },
                2: {
                    "name": "Python, Son of Gaia",
                    "description": "The earth shakes as Python uncoils. Buildings crumble as it prepares to strike.",
                    "abilities": ["strike", "corrupting_blast", "stoneskin_shield", "purify", "fear_aura"],
                    "progress_threshold": 30
                },
                3: {
                    "name": "Python, The Sun’s Eclipse",
                    "description": "Night descends upon Delphi. Python rises, eclipsing the Sun—terror grips the heart.",
                    "abilities": ["obliterate", "stone_aegis", "soul_drain", "apocalyptic_roar"],
                    "progress_threshold": 60
                }
            }

            def apply_damage_with_protection(target, dmg: int):
                if target.get("protection"):
                    absorb = min(dmg, target.get("shield_points", 0))
                    target["shield_points"] -= absorb
                    dmg -= absorb
                    if target["shield_points"] <= 0:
                        target["protection"] = False
                        target["shield_points"] = 0
                target["hp"] -= dmg

            def bar(current, total, length=10):
                p = (current / total)
                return "⬛" * int(p * length) + "⬜" * (length - int(p * length))

            progress = 0
            status_msg_id = None

            # First appearance (with DURING image)
            ph = PHASES[guardians["phase"]]
            appear = discord.Embed(
                title=f"🐍 {ph['name']} Appears",
                description=ph["description"],
                color=0xF6C453
            )
            appear.set_image(url=IMG_DURING)
            await ctx.send(embed=appear)

            TIMEOUT = 90

            # ===== Turn loop =====
            for turn in range(TOTAL_TURNS):
                if champ_stats["hp"] <= 0:
                    await ctx.send(f"💔 {getattr(champion, 'mention', 'The Champion')} has fallen. The Song fails as Python claims the Temple—for now…")
                    return
                if progress >= 100:
                    break

                # Priest turn
                priest_decision = None
                if priest:
                    if isinstance(priest, str):
                        if champ_stats["hp"] < champ_stats["max_hp"] * 0.3 and priest_stats["mana"] >= 20:
                            priest_decision = "Revitalize"
                        elif PHASES[guardians["phase"]]["name"] != "Python" and priest_stats["mana"] >= 30 and not champ_stats["barrier_active"]:
                            priest_decision = "Barrier"
                        elif progress < 70 and priest_stats["mana"] >= 15:
                            priest_decision = "Channel"
                        elif priest_stats["mana"] >= 20:
                            priest_decision = "Bless"
                    else:
                        abilities = {
                            "Bless":      {"description": "Boost the Champion's power", "mana_cost": 20},
                            "Barrier":    {"description": "Protect the Champion",        "mana_cost": 30},
                            "Curse":      {"description": "Weaken Python",               "mana_cost": 25},
                            "Revitalize": {"description": "Heal the Champion",           "mana_cost": 20},
                            "Channel":    {"description": "Significantly increase Song progress", "mana_cost": 15},
                        }
                        emb = discord.Embed(
                            title="🎼 Priest’s Turn",
                            description=f"{priest.mention}, your prophetic power is needed. Choose your action:",
                            color=0xF6C453
                        )
                        for a, info in abilities.items():
                            if priest_stats["mana"] >= info["mana_cost"]:
                                emb.add_field(name=f"{a} (Cost: {info['mana_cost']} Mana)", value=info["description"], inline=False)
                        emb.set_footer(text=f"Mana: {priest_stats['mana']}/{priest_stats['max_mana']}")
                        await ctx.send(f"It's {priest.mention}'s turn to choose—check DMs!")

                        opts = [a for a, i in abilities.items() if priest_stats["mana"] >= i["mana_cost"]]
                        if opts:
                            try:
                                priest_decision = await asyncio.wait_for(
                                    self.get_player_decision(priest, opts, "priest", emb, TIMEOUT),
                                    timeout=TIMEOUT
                                )
                            except asyncio.TimeoutError:
                                priest_decision = None

                    if priest_decision:
                        costs = {"Bless": 20, "Barrier": 30, "Curse": 25, "Revitalize": 20, "Channel": 15}
                        priest_stats["mana"] -= costs[priest_decision]
                        if priest_decision == "Bless":
                            champ_stats["damage"] += int(200 * priest_stats["healing_boost"])
                            await ctx.send("✨ The Priest blesses the Champion, radiant might surges!")
                        elif priest_decision == "Barrier":
                            champ_stats["barrier_active"] = True
                            await ctx.send("☀️ A luminous barrier surrounds the Champion!")
                        elif priest_decision == "Curse":
                            guardians["cursed"] = True
                            await ctx.send("🏹 Arrows of plague pierce Python’s hide—its strength wanes!")
                        elif priest_decision == "Revitalize":
                            heal = int(300 * priest_stats["healing_boost"])
                            champ_stats["hp"] = min(champ_stats["hp"] + heal, champ_stats["max_hp"])
                            await ctx.send(f"❤️ Apollo’s warmth mends {heal} HP!")
                        elif priest_decision == "Channel":
                            progress += 5
                            await ctx.send("🔮 The Priest channels divine rhythm—**Song progress +5%**!")

                # Python incapacitation recovery
                if guardians["hp"] <= 0 and guardians["incapacitated_turns"] == 0:
                    guardians["incapacitated_turns"] = 2
                    await ctx.send("💫 Python reels and collapses—your voices surge!")
                    progress += 10

                # Python turn
                if guardians["incapacitated_turns"] > 0:
                    guardians["incapacitated_turns"] -= 1
                    if guardians["incapacitated_turns"] == 0:
                        guardians["hp"] = int(guardians["max_hp"] * 0.5)
                        guardians["damage_multiplier"] += 0.2
                        await ctx.send("😡 Python rises more furious than before!")
                        if guardians["phase"] < 3:
                            guardians["phase"] += 1
                            ph = PHASES[guardians["phase"]]
                            t = discord.Embed(
                                title=f"😈 Python transforms into {ph['name']}!",
                                description=ph["description"],
                                color=0xF6C453
                            )
                            t.set_image(url=IMG_DURING)
                            await ctx.send(embed=t)
                    else:
                        await ctx.send("🐍 Python is staggered and cannot act this turn.")
                    action = "incapacitated"
                else:
                    await ctx.send("💢 Python strikes!")
                    current = guardians["phase"]
                    nextp = current + 1
                    ph = PHASES[current]

                    # phase up by song progress
                    if nextp in PHASES and progress >= PHASES[nextp]["progress_threshold"]:
                        guardians["phase"] = nextp
                        guardians["damage_multiplier"] += 0.3
                        ph = PHASES[nextp]
                        t = discord.Embed(
                            title=f"😈 Python transforms into {ph['name']}!",
                            description=ph["description"],
                            color=0xF6C453
                        )
                        t.set_image(url=IMG_DURING)
                        await ctx.send(embed=t)

                    decisions = ph["abilities"]
                    if progress >= 80 and "purify" in decisions:
                        action = "purify"
                    else:
                        action = randomm.choice(decisions)

                    # execute
                    if action == "strike":
                        dmg = int(randomm.randint(100, 250) * guardians["damage_multiplier"])
                        if champ_stats["barrier_active"]:
                            dmg = int(dmg * 0.5); champ_stats["barrier_active"] = False
                        if champ_stats["defending"]:
                            dmg = int(dmg * 0.5); champ_stats["defending"] = False
                        if champ_stats["vulnerable"]:
                            dmg = int(dmg * 1.5); champ_stats["vulnerable"] = False
                        apply_damage_with_protection(champ_stats, dmg)
                        await ctx.send(f"💥 Python lashes out for **{dmg}** damage!")

                    elif action == "corrupting_blast":
                        dmg = int(randomm.randint(150, 250) * guardians["damage_multiplier"])
                        champ_stats["damage"] = max(champ_stats["damage"] - 100, 0)
                        apply_damage_with_protection(champ_stats, dmg)
                        await ctx.send(f"🪨 A hail of debris batters you for **{dmg}**—your offense weakens!")

                    elif action == "stoneskin_shield":
                        guardians["shield_active"] = True
                        guardians["damage_multiplier"] *= 0.8
                        await ctx.send("🛡️ Python’s scales harden like stone—incoming damage reduced **20%**!")

                    elif action == "obliterate":
                        dmg = int(randomm.randint(400, 900) * guardians["damage_multiplier"])
                        apply_damage_with_protection(champ_stats, dmg)
                        await ctx.send(f"☀️🌑 Eclipse strike! Python deals **{dmg}** damage!")

                    elif action == "stone_aegis":
                        guardians["shield_active"] = True
                        guardians["damage_multiplier"] *= 0.5
                        await ctx.send("🧱 Stone Aegis rises—damage reduced **50%**!")

                    elif action == "soul_drain":
                        dmg = randomm.randint(200, 300)
                        guardians["hp"] = min(guardians["hp"] + dmg, guardians["max_hp"])
                        apply_damage_with_protection(champ_stats, dmg)
                        await ctx.send(f"🩸 Python siphons **{dmg} HP** and deals **{dmg}**!")

                    elif action == "purify":
                        before = progress
                        progress = max(0, progress - 20)
                        await ctx.send(f"💫 Python disrupts the melody—**Song progress -{before - progress}%**!")

                    elif action == "shield":
                        guardians["shield_active"] = True
                        await ctx.send("🛡️ Python coils defensively, dampening your blows!")

                    elif action == "fear_aura":
                        await ctx.send("😱 A primal dread radiates from Python—your chorus falters!")

                    elif action == "apocalyptic_roar":
                        dmg = randomm.randint(150, 250)
                        champ_stats["hp"] -= dmg
                        await ctx.send(f"🌋 A world-shaking roar deals **{dmg}** damage to the Champion!")

                # Chosen (followers) turn
                await ctx.send("🏹 The Chosen are making their decisions.")
                fol_embed = discord.Embed(
                    title="🎵 Chosen’s Actions",
                    description="Choose your action to support the Song:",
                    color=0xF6C453
                )
                fol_embed.add_field(name="🌌 Boost Song", value="Harmonize to hasten the Song.", inline=True)
                fol_embed.add_field(name="🛡️ Protect Champion", value="Provide a shield to the Champion.", inline=True)
                fol_embed.add_field(name="🌟 Empower Priest", value="Amplify the Priest’s next action.", inline=True)
                fol_embed.add_field(name="💥 Sabotage Python", value="Disrupt Python’s next move.", inline=True)
                fol_embed.add_field(name="🎵 Crescendo", value="Raise your voices to amplify the Song.", inline=True)
                fol_embed.add_field(name="💉 Heal Champion", value="Heal the Champion a small amount.", inline=True)

                bucket = {
                    "Boost Song": 0,
                    "Protect Champion": 0,
                    "Empower Priest": 0,
                    "Sabotage Python": 0,
                    "Crescendo": 0,
                    "Heal Champion": 0
                }

                async def get_choice(f):
                    choice = await self.get_player_decision(
                        f,
                        list(bucket.keys()),
                        "chosen",
                        fol_embed,
                        TIMEOUT
                    )
                    return (f, choice)

                results = await asyncio.gather(*[get_choice(f) for f in followers], return_exceptions=True)
                for r in results:
                    if isinstance(r, Exception): continue
                    _, choice = r
                    if choice in bucket: bucket[choice] += 1

                if bucket["Boost Song"]:
                    delta = min(2 * bucket["Boost Song"], 8)
                    progress += delta
                    await ctx.send(f"🔺 The chorus swells—**Song progress +{delta}%**!")

                if bucket["Protect Champion"] > 0:
                    champ_stats["protection"] = True
                    champ_stats["shield_points"] += 50 * bucket["Protect Champion"]
                    await ctx.send(f"🛡️ The faithful shield the Champion—**{champ_stats['shield_points']}** shield points!")

                if bucket["Empower Priest"] > 0 and priest:
                    priest_stats["healing_boost"] += 0.1 * bucket["Empower Priest"]
                    await ctx.send("✨ The Priest is empowered by the chorus!")

                if bucket["Sabotage Python"] > 0:
                    guardians["damage_multiplier"] = max(0.5, guardians["damage_multiplier"] - 0.1 * bucket["Sabotage Python"])
                    await ctx.send("🌀 Python’s power is undermined!")

                if bucket["Crescendo"]:
                    progress += bucket["Crescendo"]
                    await ctx.send(f"🎶 Crescendo rises—**Song progress +{bucket['Crescendo']}%**!")

                if bucket["Heal Champion"]:
                    heal = 50 * bucket["Heal Champion"]
                    champ_stats["hp"] = min(champ_stats["hp"] + heal, champ_stats["max_hp"])
                    await ctx.send(f"💖 The Champion is healed for **{heal} HP**!")

                # Champion turn
                if isinstance(champion, str):
                    if champ_stats["hp"] < champ_stats["max_hp"] * 0.3:
                        champ_choice = "Heal"
                    elif progress < 60 and champ_stats["haste_cooldown"] == 0:
                        champ_choice = "Haste"
                    else:
                        champ_choice = "Smite"
                else:
                    emb = discord.Embed(
                        title="⚔️ Champion’s Turn",
                        description=f"{champion.mention}, choose your action:",
                        color=0xF6C453
                    )
                    haste_desc = "Boost the Song’s progress"
                    if champ_stats["haste_cooldown"] > 0:
                        haste_desc += f" (Cooldown: {champ_stats['haste_cooldown']} turns)"
                    emb.add_field(name="⚡ Smite", value="Strike Python with piercing radiant light.", inline=True)
                    emb.add_field(name="❤️ Heal", value="Recover some of your lost HP.", inline=True)
                    emb.add_field(name="🌀 Haste", value=haste_desc, inline=True)
                    emb.add_field(name="🛡️ Defend", value="Reduce incoming damage next turn.", inline=True)
                    emb.add_field(name="💔 Sacrifice", value="Advance the Song by 20% at the cost of 400 HP.", inline=True)
                    await ctx.send(f"It’s {champion.mention}'s turn—check DMs!")
                    opts = ["Smite", "Heal", "Defend", "Sacrifice"] + ([] if champ_stats["haste_cooldown"] > 0 else ["Haste"])
                    try:
                        champ_choice = await asyncio.wait_for(
                            self.get_player_decision(champion, opts, "champion", emb, TIMEOUT),
                            timeout=TIMEOUT
                        )
                    except asyncio.TimeoutError:
                        champ_choice = "Smite"

                # Execute champion choice
                if champ_choice == "Smite":
                    dmg = champ_stats["damage"]
                    guardians["hp"] -= dmg
                    if guardians.get("shield_active"):
                        guardians["hp"] += 200
                        guardians["shield_active"] = False
                    await ctx.send(f"⚔️ Radiant strike hits Python for **{dmg}**!")

                elif champ_choice == "Heal":
                    heal = 200
                    champ_stats["hp"] = min(champ_stats["hp"] + heal, champ_stats["max_hp"])
                    await ctx.send(f"❤️ The Sun restores **{heal} HP**!")

                elif champ_choice == "Haste":
                    progress += 15
                    champ_stats["haste_cooldown"] = 3
                    champ_stats["vulnerable"] = True
                    await ctx.send("🌀 The tempo quickens—**Song progress +15%** (Champion vulnerable)!")

                elif champ_choice == "Defend":
                    champ_stats["defending"] = True
                    await ctx.send("🛡️ The Champion braces for impact!")

                elif champ_choice == "Sacrifice":
                    champ_stats["hp"] -= 400
                    progress += 20
                    await ctx.send("💔 Plague-tipped arrows bite deep—**Song progress +20%** at great cost!")

                # tick haste cd
                if champ_stats["haste_cooldown"] > 0:
                    champ_stats["haste_cooldown"] -= 1

                # Status embed (update)
                ph = PHASES[guardians["phase"]]
                pcolor = 0x4CAF50 if progress >= 80 else 0xFFC107 if progress >= 50 else 0xFF5722
                if progress >= 100 and champ_stats["hp"] > 0:
                    progress = 100

                status = discord.Embed(
                    title="🎵 Song Progress",
                    description=f"Turn {turn + 1}/{TOTAL_TURNS}",
                    color=pcolor
                )
                status.add_field(name="Progress", value=f"{bar(progress, 100)} ({int(progress)}%)", inline=False)
                status.add_field(name="Champion", value=f"❤️ {int(champ_stats['hp'])}/{champ_stats['max_hp']} HP", inline=True)
                status.add_field(name="Python", value=f"🐍 {ph['name']} ({int(guardians['hp'])}/{guardians['max_hp']} HP)", inline=True)
                if champ_stats.get("damage") > default_champion_damage:
                    status.add_field(name="Blessing", value="🔥 Champion empowered", inline=True)
                if champ_stats.get("barrier_active"):
                    status.add_field(name="Barrier", value="🔰 Champion Protected", inline=True)
                if guardians.get("cursed"):
                    status.add_field(name="Plague", value="😵 Python Weakened", inline=True)
                if guardians.get("shield_active"):
                    status.add_field(name="Python’s Shield", value="🔰 Active", inline=True)
                if guardians.get("incapacitated_turns", 0) > 0:
                    status.add_field(name="Python Staggered", value=f"🛌 {guardians['incapacitated_turns']} turn(s) left", inline=True)

                if status_msg_id:
                    try:
                        old = await ctx.channel.fetch_message(status_msg_id)
                        await old.delete()
                    except discord.NotFound:
                        pass
                msg = await ctx.send(embed=status)
                status_msg_id = msg.id

                # ===== NEW: Actions summary embed (per turn) =====
                summary = discord.Embed(
                    title="🎼 Actions This Turn",
                    description="A quick summary of what everyone did.",
                    color=0xF6C453
                )
                # Priest
                summary.add_field(
                    name="🎼 Priest",
                    value=(priest_decision or "No action"),
                    inline=False
                )
                # Python
                python_action_label = "Incapacitated" if action == "incapacitated" else action.capitalize()
                summary.add_field(
                    name="🐍 Python",
                    value=python_action_label,
                    inline=False
                )
                # Chosen (show only non-zero)
                chosen_lines = [
                    f"{k}: {v}" for k, v in {
                        "Boost Song": bucket["Boost Song"],
                        "Protect Champion": bucket["Protect Champion"],
                        "Empower Priest": bucket["Empower Priest"],
                        "Sabotage Python": bucket["Sabotage Python"],
                        "Crescendo": bucket["Crescendo"],
                        "Heal Champion": bucket["Heal Champion"],
                    }.items() if v > 0
                ]
                summary.add_field(
                    name="🏹 Chosen",
                    value=("\n".join(chosen_lines) if chosen_lines else "No actions taken"),
                    inline=False
                )
                # Champion
                summary.add_field(
                    name="👑 Champion",
                    value=champ_choice,
                    inline=False
                )
                summary.set_footer(text="The music swells…")
                await ctx.send(embed=summary)
                # ===== End summary embed =====

                # Priest mana regen, reset modifiers
                guardians["damage_multiplier"] = 1.0
                if guardians.get("cursed"):
                    del guardians["cursed"]
                if champ_stats.get("damage") > default_champion_damage:
                    champ_stats["damage"] = default_champion_damage
                if champ_stats.get("protection") and champ_stats["shield_points"] <= 0:
                    champ_stats["protection"] = False
                if priest:
                    priest_stats["mana"] = min(priest_stats["mana"] + 10, priest_stats["max_mana"])

                await asyncio.sleep(15)

            # ===== Outcome =====
            if progress >= 100 and champ_stats["hp"] > 0:
                win = Embed(
                    title="☀️ The Oracle is Safe ☀️",
                    description=(
                        "Your chorus subdues the serpent. The Temple stands and the Oracle’s voice returns.\n\n"
                        "As Apollo’s favor, one among you is granted a special crate; all Chosen receive earthly riches."
                    ),
                    color=0xF6C453
                )
                win.set_image(url=IMG_END)
                await ctx.send(embed=win)

                users = [u.id for u in (raid_stats.keys() or participants)]
                lucky = pick_weighted_reward_target(users, champion, priest)

                async with self.bot.pool.acquire() as conn:
                    luck_val = await conn.fetchval('SELECT luck FROM profile WHERE "user"=$1;', lucky)
                luck_val = float(luck_val or 1.0)
                boundary_low, boundary_high = 0.0, 2.0
                norm = max(0.0, min(1.0, (luck_val - boundary_low) / (boundary_high - boundary_low)))
                weight_divine = round(0.20 + 0.20 * norm, 3)
                options, weights = ["legendary", "fortune", "divine"], [0.40, 0.40, weight_divine]
                crate = randomm.choices(options, weights=weights)[0]

                await ctx.send(f"🎁 Congratulations, <@{lucky}>! You receive a **{crate} crate**!")
                async with self.bot.pool.acquire() as conn:
                    await conn.execute(
                        f'UPDATE profile SET "crates_{crate}" = "crates_{crate}" + 1 WHERE "user" = $1;',
                        lucky,
                    )

                import random as _r
                cash = _r.randint(20000, 50000)
                await self.bot.pool.execute(
                    'UPDATE profile SET money=money+$1 WHERE "user"=ANY($2);',
                    cash,
                    users,
                )
                await ctx.send(f"💰 All participants receive **${cash}**!")

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
                        f"✨ Apollo’s light grants **1 {familiar_name} shard** to {shard_target.mention}! "
                        f"Now: **{shard_total}/20**"
                    )
            else:
                await ctx.send("💔 The Song falters—Python slithers back into the caverns. The Temple endures… for now.")

        except Exception as e:
            await ctx.send(f"An error occurred: {e}")
            import traceback; traceback.print_exc()


# ---------- Extension setup ----------

async def setup(bot: commands.Bot):
    await bot.add_cog(ApolloSong(bot))
