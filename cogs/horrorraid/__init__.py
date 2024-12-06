import discord
from discord.ext import commands
from discord.ui import View, Button
from discord.enums import ButtonStyle
import random
import asyncio
import datetime
from typing import Dict, List
import traceback
import functools
import uuid

from classes.converters import IntGreaterThan
from cogs.raid import raid_free
from utils.checks import is_god, is_gm
from utils.i18n import _, locale_doc


class Raid:
    def __init__(self, boss_hp, channels):
        self.boss_hp = boss_hp
        self.participants: Dict[discord.User, Dict] = {}
        self.channels = channels
        self.view = HorrorRaidView()
        self.current_turn = None
        self.turn_order: List[discord.User] = []
        self.is_choice_phase = False
        self.raid_started = False
        self.defeated_players = set()  # Track defeated players
        self.insane_players = set()  # Track insane players
        self.join_phase = True
        self.countdown_messages = {}  # Store message IDs for countdown updates

    def remove_player(self, player: discord.User):
        """Safely remove a player from all game tracking structures"""
        if player in self.participants:
            del self.participants[player]
        if player in self.turn_order:
            self.turn_order.remove(player)
        if self.current_turn == player:
            self.current_turn = None

    async def update_countdown_message(self, minutes_left: int):
        """Update or send countdown message based on time remaining"""
        countdown_embeds = {
            10: {
                "title": "🕷️ THE VOID STIRS 🕷️",
                "description": (
                    "```diff\n"
                    "- [ALERT: DIMENSIONAL TEAR DETECTED]\n"
                    "- [VOID ENERGY READINGS INCREASING]\n"
                    "- [TIME UNTIL MANIFESTATION: 10 MINUTES]\n"
                    "```\n\n"
                    "*A cold wind carries whispers of ancient horrors...*\n"
                    "*The very fabric of reality begins to warp...*"
                ),
                "color": 0x800080,
                "image": "https://i.imgur.com/YoszTlc.png"
            },
            5: {
                "title": "⚠️ REALITY FRACTURING ⚠️",
                "description": (
                    "```diff\n"
                    "! [CRITICAL ALERT: BREACH IMMINENT]\n"
                    "! [VOID CORRUPTION SPREADING]\n"
                    "! [TIME UNTIL MANIFESTATION: 5 MINUTES]\n"
                    "```\n\n"
                    "*The shadows themselves seem to breathe...*\n"
                    "*Your reflection no longer moves with you...*"
                ),
                "color": 0x800000,
                "image": "https://i.imgur.com/s5tvHMd.png"
            },
            3: {
                "title": "🌑 THE VEIL TEARS 🌑",
                "description": (
                    "```diff\n"
                    "- [SYSTEM FAILURE]\n"
                    "- [REALITY ANCHOR FAILING]\n"
                    "- [TIME UNTIL MANIFESTATION: 3 MINUTES]\n"
                    "```\n\n"
                    "*Space bends at impossible angles...*\n"
                    "*Your thoughts are no longer your own...*"
                ),
                "color": 0x000000,
                "image": "https://i.imgur.com/UpWW3fF.png"
            },
            2: {
                "title": "💀 IT COMES 💀",
                "description": (
                    "```diff\n"
                    "! [REALITY COLLAPSE IN PROGRESS]\n"
                    "! [VOID ENTITY MATERIALIZING]\n"
                    "! [TIME UNTIL MANIFESTATION: 2 MINUTES]\n"
                    "```\n\n"
                    "*The air tastes like static and metal...*\n"
                    "*Your skin crawls with invisible touches...*"
                ),
                "color": 0xFF0000,
                "image": "https://i.imgur.com/YS4A6R7.png"
            },
            1: {
                "title": "🎭 SANITY'S REQUIEM 🎭",
                "description": (
                    "```diff\n"
                    "- [REALITY STATUS: SHATTERED]\n"
                    "- [VOID MANIFESTATION: FINAL STAGE]\n"
                    "- [TIME UNTIL MANIFESTATION: 1 MINUTE]\n"
                    "```\n\n"
                    "*The laws of physics weep...*\n"
                    "*Your memories begin to unravel...*"
                ),
                "color": 0x000000,
                "image": "https://i.imgur.com/UpWW3fF.png"
            }
        }

        if minutes_left in countdown_embeds:
            embed_data = countdown_embeds[minutes_left]
            em = discord.Embed(
                title=embed_data["title"],
                description=embed_data["description"],
                color=embed_data["color"]
            )
            em.set_image(url=embed_data["image"])

            for channel in self.channels:
                if channel.id in self.countdown_messages:
                    try:
                        old_msg = await channel.fetch_message(self.countdown_messages[channel.id])
                        await old_msg.delete()
                    except:
                        pass
                new_msg = await channel.send(embed=em)
                self.countdown_messages[channel.id] = new_msg.id


class HorrorRaidView(View):
    def __init__(self, timeout=60 * 15):
        super().__init__(timeout=timeout)
        self.participants: Dict[discord.User, Dict] = {}


class HorrorButton(Button):
    def __init__(self, style: ButtonStyle, label: str, callback):
        super().__init__(style=style, label=label)
        self._callback = callback

    async def callback(self, interaction: discord.Interaction):
        await self._callback(interaction)


class HorrorRaid(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_raids: Dict[int, Raid] = {}
        self.raid_cooldowns = {}

    async def send_to_channels(self, embed=None, content=None, view=None, raid: Raid = None):
        target_raid = raid or next(iter(self.active_raids.values()), None)
        if target_raid:
            for channel in target_raid.channels:
                await channel.send(embed=embed, content=content, view=view)

    @is_gm()
    @raid_free()
    @commands.command(hidden=True, brief=_("Start a Drakath raid"))
    @locale_doc
    async def chaosspawnbeta(self, ctx, boss_hp: IntGreaterThan(0)):
        """[Drakath only] Starts a nightmare raid."""
        try:
            channels = [
                self.bot.get_channel(1313482408242184213),
            ]

            if not channels[0]:
                await ctx.send("Raid channel not found.")
                return

            raid = Raid(boss_hp=boss_hp, channels=channels)
            self.active_raids[ctx.channel.id] = raid

            async def join_raid_callback(interaction: discord.Interaction):
                try:
                    if not raid.join_phase:
                        await interaction.response.send_message(
                            "```diff\n- The void has already manifested. Your chance to join has passed...```",
                            ephemeral=True
                        )
                        return

                    is_drakath = await self.is_drakath_follower(interaction.user)
                    if not is_drakath:
                        await interaction.response.send_message(
                            "```diff\n- The void rejects your weak soul. Only Drakath's chosen may enter...```",
                            ephemeral=True
                        )
                        return

                    if interaction.user not in raid.participants:
                        raid.participants[interaction.user] = {
                            "hp": 250,
                            "sanity": 100,
                            "corrupted": False,
                            "cursed": False,
                            "void_touched": False,
                            "madness_points": 0
                        }

                        em = discord.Embed(
                            description=f"*{interaction.user.mention} has made a pact with the void...*",
                            color=0x000000
                        )

                        await interaction.response.send_message(
                            "```diff\n- YOUR SOUL HAS BEEN MARKED. THE VOID HUNGERS...\n- THERE IS NO ESCAPE NOW.```",
                            ephemeral=True
                        )

                        await self.send_to_channels(embed=em, raid=raid)

                    else:
                        await interaction.response.send_message(
                            "```diff\n- You have already been marked by the void...```",
                            ephemeral=True
                        )
                except Exception as e:
                    print(f"Join raid error: {e}")
                    await interaction.response.send_message(
                        f"An error occurred while joining the raid. {e}",
                        ephemeral=True
                    )

            join_button = HorrorButton(
                style=ButtonStyle.danger,
                label="🩸 SACRIFICE YOUR SANITY 🩸",
                callback=join_raid_callback
            )
            raid.view.add_item(join_button)

            em = discord.Embed(
                title="🕷️ THE VOID HUNGERS 🕷️",
                description=(
                    "```diff\n"
                    "[SYSTEM ALERT: REALITY BREACH DETECTED]\n"
                    "[CORRUPTION LEVEL: CRITICAL]\n"
                    "[DIMENSIONAL STABILITY: FAILING]\n"
                    f"[ENTITY HP: {raid.boss_hp}]\n"
                    "[THREAT LEVEL: EXTINCTION]\n"
                    "```\n\n"
                    "*The air grows thick with an otherworldly presence...*\n\n"
                    "Eclipse emerges not as a mere boss, but as a horror beyond comprehension.\n"
                    "Those who dare to face it must be prepared to sacrifice their sanity...\n\n"
                    "⚠️ **WARNING:** This raid features permanent consequences.\n"
                    "⚠️ **WARNING:** Your mind may not survive this encounter intact.\n"
                    "⚠️ **WARNING:** The void remembers those who challenge it.\n\n"
                    "**Time until manifestation: 15 minutes**"
                ),
                color=0x000000
            )
            em.set_image(url="https://i.imgur.com/YoszTlc.png")
            await self.send_to_channels(embed=em, view=raid.view, raid=raid)

            # Start countdown timer
            minutes_left = [10, 5, 3, 2, 1]
            for minute in minutes_left:
                await asyncio.sleep((15 - minute) * 60)  # Sleep until next announcement
                await raid.update_countdown_message(minute)

            # Final countdown in seconds
            for seconds in [30, 10]:
                await asyncio.sleep((60 - seconds))
                em = discord.Embed(
                    title="⚠️ REALITY BREACH IMMINENT ⚠️",
                    description=f"```diff\n- TIME UNTIL MANIFESTATION: {seconds} SECONDS\n```",
                    color=0xFF0000
                )
                await self.send_to_channels(embed=em, raid=raid)

            # Start the raid
            await asyncio.sleep(10)
            raid.join_phase = False

            if len(raid.participants) < 1:
                em = discord.Embed(
                    title="🌑 THE VOID RETREATS 🌑",
                    description=(
                        "```diff\n"
                        "- No souls were brave enough to face the horror\n"
                        "- The dimensional tear seals itself\n"
                        "- Eclipse's hunger remains unsated...\n"
                        "```"
                    ),
                    color=0x000000
                )
                await self.send_to_channels(embed=em, raid=raid)
                del self.active_raids[ctx.channel.id]
                return

            em = discord.Embed(
                title="🕷️ IT BEGINS 🕷️",
                description=(
                    "```diff\n"
                    "- REALITY BREACH SUCCESSFUL\n"
                    "- DIMENSIONAL BARRIERS COLLAPSED\n"
                    "- ECLIPSE HAS ARRIVED\n"
                    "```\n\n"
                    "*May your gods have mercy on your souls...*"
                ),
                color=0xFF0000
            )
            await self.send_to_channels(embed=em, raid=raid)

            await asyncio.sleep(5)
            raid.raid_started = True
            await self.start_next_turn(raid)

        except Exception as e:
            error_traceback = traceback.format_exc()
            error_em = discord.Embed(
                title="❌ Error",
                description=f"```python\n{error_traceback}\n```",
                color=0xFF0000
            )
            await ctx.send(embed=error_em)
            print(error_traceback)

    async def is_drakath_follower(self, user: discord.User) -> bool:
        async with self.bot.pool.acquire() as conn:
            result = await conn.fetchrow(
                'SELECT god FROM profile WHERE "user"=$1;',
                user.id
            )
            return result and result['god'] == 'Drakath'

    async def start_next_turn(self, raid: Raid):
        """Handle the transition to the next player's turn"""
        # Check if raid should end
        if not raid.participants:
            await self.handle_defeat(raid)
            return

        # Rebuild turn order if needed
        if not raid.turn_order or not set(raid.turn_order).issubset(set(raid.participants.keys())):
            raid.turn_order = list(raid.participants.keys())
            random.shuffle(raid.turn_order)
            raid.current_turn = None  # Reset current turn when rebuilding order

        # If current turn is valid, get next player
        if raid.current_turn in raid.participants:
            current_index = raid.turn_order.index(raid.current_turn)
            next_index = (current_index + 1) % len(raid.turn_order)
            raid.current_turn = raid.turn_order[next_index]
        else:
            # If current player is gone, start with first in order
            raid.current_turn = raid.turn_order[0]


        # Safety check - validate current turn is still valid
        if raid.current_turn not in raid.participants:
            await self.handle_defeat(raid)
            return

        # Create and send turn update embed
        em = discord.Embed(
            title="🕷️ NEXT TURN 🕷️",
            description=(
                f"It is {raid.current_turn.mention}'s turn!\n\n"
                f"```diff\n"
                f"Boss HP: {raid.boss_hp}\n"
                f"Active Players: {len(raid.participants)}\n"
                f"Fallen Players: {len(raid.defeated_players)}\n"
                f"Insane Players: {len(raid.insane_players)}\n"
                "```"
            ),
            color=0x800080
        )
        await self.send_to_channels(embed=em, raid=raid)

        # Brief pause before triggering horror event
        await asyncio.sleep(3)
        await self.trigger_horror_event(raid.current_turn, raid)

    async def choice_handler(self, interaction: discord.Interaction, choice_id, success_rate, action_text, target,
                             horror_image, raid: Raid):
        """Handle player choice during horror events"""
        try:
            # Validate choice is still valid
            if not raid.is_choice_phase:
                await interaction.response.send_message(
                    "*This horror has already passed...*",
                    ephemeral=True
                )
                return

            # Validate correct player is making choice
            if interaction.user != target:
                await interaction.response.send_message(
                    "*It is not your turn to face the horror...*",
                    ephemeral=True
                )
                return

            # Mark choice phase as complete
            raid.is_choice_phase = False
            player_data = raid.participants[target]

            # Handle choice outcome
            if random.random() > success_rate:
                # Track last curse application time
                current_time = datetime.datetime.utcnow()
                last_curse_time = player_data.get("last_curse_time")

                # Select consequence
                consequences = [
                    ("Your sanity shatters like glass", -30, "sanity"),
                    ("The void corrupts your flesh", -50, "hp"),
                    ("A curse marks your soul", True, "cursed"),
                    ("The void claims your essence", True, "void_touched")
                ]

                # First attempt at selecting consequence
                consequence = random.choice(consequences)

                # If it's a curse effect, check timing
                if consequence[2] == "cursed":
                    if last_curse_time and (current_time - last_curse_time).total_seconds() < 30:
                        # If cursed too recently, filter out curse option and pick again
                        non_curse_consequences = [c for c in consequences if c[2] != "cursed"]
                        consequence = random.choice(non_curse_consequences)
                    else:
                        player_data["last_curse_time"] = current_time

                # Apply the consequence
                if isinstance(consequence[1], bool):
                    player_data[consequence[2]] = consequence[1]
                else:
                    player_data[consequence[2]] += consequence[1]

                # Add madness points
                player_data["madness_points"] += random.randint(1, 3)

                # Create failure embed
                em = discord.Embed(
                    title="💀 THE HORROR CONSUMES YOU 💀",
                    description=(
                        f"*{action_text}*\n\n"
                        "**But you fail...**\n\n"
                        "```diff\n"
                        f"- {consequence[0]}\n"
                        "- The void grows stronger\n"
                        "- Your mind fractures further\n"
                        "```\n\n"
                        "**Current Status:**\n"
                        f"HP: {player_data['hp']}\n"
                        f"Sanity: {player_data['sanity']}\n"
                        f"Effects: {self.get_status_effects(player_data)}"
                    ),
                    color=0xFF0000
                )
                em.set_thumbnail(url=horror_image)
                raid.boss_hp += random.randint(100, 300)

            else:
                # Calculate damage
                damage = random.randint(200, 500)
                if player_data.get("void_touched"):
                    damage = int(damage * 1.5)
                if player_data.get("cursed"):
                    damage = int(damage * 0.7)

                # Apply damage
                raid.boss_hp -= damage

                # Create success embed
                em = discord.Embed(
                    title="⚔️ YOU RESIST THE HORROR ⚔️",
                    description=(
                        f"*{action_text}*\n\n"
                        "**And you succeed!**\n\n"
                        "```diff\n"
                        f"+ You strike at Eclipse\n"
                        "+ The horror recoils\n"
                        f"+ Damage dealt: {damage}\n"
                        "```\n\n"
                        "**Current Status:**\n"
                        f"HP: {player_data['hp']}\n"
                        f"Sanity: {player_data['sanity']}\n"
                        f"Effects: {self.get_status_effects(player_data)}"
                    ),
                    color=0x800080
                )
                em.set_thumbnail(url=horror_image)

            # Send result and check player state
            await self.send_to_channels(embed=em, raid=raid)
            await self.check_player_state(target, raid)

            # Acknowledge the interaction
            await interaction.response.defer()

            # Continue raid if conditions are met
            if len(raid.participants) > 0 and raid.boss_hp > 0:
                await asyncio.sleep(5)
                await self.start_next_turn(raid)
            elif raid.boss_hp <= 0:
                await self.handle_victory(raid)
            else:
                await self.handle_defeat(raid)

        except Exception as e:
            print(f"Choice handler error: {e}")
            error_traceback = traceback.format_exc()
            error_em = discord.Embed(
                title="❌ Error",
                description=f"```python\n{error_traceback}\n```",
                color=0xFF0000
            )
            await self.send_to_channels(embed=error_em, raid=raid)

    async def handle_insanity(self, player: discord.User, raid: Raid):
        """Handle when a player's sanity reaches 0"""
        em = discord.Embed(
            title="🎭 DESCENT INTO MADNESS 🎭",
            description=(
                f"{player.mention}'s mind shatters under the weight of cosmic horror!\n\n"
                "```diff\n"
                "- Your sanity is gone\n"
                "- The void whispers grow louder\n"
                "- Reality blends with nightmare\n"
                "```\n\n"
                "*They will never be the same...*"
            ),
            color=0x000000
        )
        em.set_image(url="https://i.imgur.com/YoszTlc.png")
        await self.send_to_channels(embed=em, raid=raid)

        # Add player to insane set before removal
        raid.insane_players.add(player)
        raid.remove_player(player)

        # Update database status
        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                'UPDATE profile SET "is_insane"=true WHERE "user"=$1;',
                player.id
            )

        # Give Eclipse power from the madness
        raid.boss_hp += random.randint(200, 500)

    async def handle_death(self, player: discord.User, raid: Raid):
        """Handle when a player's HP reaches 0"""
        em = discord.Embed(
            title="💀 CLAIMED BY THE VOID 💀",
            description=(
                f"{player.mention} has been consumed by Eclipse!\n\n"
                "```diff\n"
                "- Your body dissolves into void essence\n"
                "- Your soul is marked by chaos\n"
                "- The horror claims another victim\n"
                "```\n\n"
                "*Their screams echo through dimensions...*"
            ),
            color=0x000000
        )
        em.set_image(url="https://i.imgur.com/UpWW3fF.png")
        await self.send_to_channels(embed=em, raid=raid)

        # Add player to defeated set before removal
        raid.defeated_players.add(player)
        raid.remove_player(player)

        # Eclipse gains power from the death
        raid.boss_hp += random.randint(300, 600)

    async def check_player_state(self, player: discord.User, raid: Raid):
        player_data = raid.participants[player]

        # Check for insanity
        if player_data["sanity"] <= 0:
            await self.handle_insanity(player, raid)

            # Check if we need to end the raid
            if not raid.participants:
                await self.handle_defeat(raid)
                return

        # Check for death
        elif player_data["hp"] <= 0:
            await self.handle_death(player, raid)

            # Check if we need to end the raid
            if not raid.participants:
                await self.handle_defeat(raid)
                return

        # Check for transformation from high madness
        elif player_data["madness_points"] >= 10:
            em = discord.Embed(
                title="🎭 METAMORPHOSIS 🎭",
                description=(
                    f"{player.mention} has been transformed by the void!\n\n"
                    "```diff\n"
                    "- Your humanity slips away\n"
                    "- Your form becomes chaos\n"
                    "+ Damage multiplier increased (1.5x)\n"
                    "- Sanity drains faster\n"
                    "```"
                ),
                color=0x000000
            )
            em.set_image(url="https://i.imgur.com/YS4A6R7.png")
            await self.send_to_channels(embed=em, raid=raid)

            # Apply transformation effects
            player_data["void_touched"] = True
            player_data["madness_points"] = 0
            player_data["sanity"] = max(10, player_data["sanity"] - 20)  # Immediate sanity drain

            # Don't need to check for raid end here since this isn't a defeat condition

        # Check for curse accumulation
        elif player_data.get("cursed") and random.random() < 0.3:  # 30% chance each check
            curse_effect = random.choice([
                ("Your blood turns to acid", -20, "hp"),
                ("Your mind splits further", -15, "sanity"),
                ("The curse deepens", 2, "madness_points")
            ])

            em = discord.Embed(
                title="⚠️ CURSE MANIFESTATION ⚠️",
                description=(
                    f"{player.mention}'s curse grows stronger!\n\n"
                    f"*{curse_effect[0]}*\n\n"
                    "```diff\n"
                    f"- {curse_effect[0]}\n"
                    "```"
                ),
                color=0x800000
            )
            await self.send_to_channels(embed=em, raid=raid)

            # Apply curse effect
            if curse_effect[2] == "madness_points":
                player_data[curse_effect[2]] += curse_effect[1]
            else:
                player_data[curse_effect[2]] = max(0, player_data[curse_effect[2]] + curse_effect[1])

        # Check for void corruption effects
        if player_data.get("void_touched") and random.random() < 0.2:  # 20% chance each check
            sanity_drain = random.randint(5, 15)
            player_data["sanity"] = max(0, player_data["sanity"] - sanity_drain)

            if player_data["sanity"] > 0:  # Only show if they haven't hit 0 (which would trigger insanity)
                em = discord.Embed(
                    title="🌀 VOID CORRUPTION 🌀",
                    description=(
                        f"{player.mention}'s mind fractures further!\n\n"
                        "```diff\n"
                        f"- Sanity drain: {sanity_drain}\n"
                        f"- Current sanity: {player_data['sanity']}\n"
                        "```"
                    ),
                    color=0x000000
                )
                await self.send_to_channels(embed=em, raid=raid)

    async def handle_timeout(self, target: discord.User, raid: Raid):
        player_data = raid.participants[target]

        # Harder punishment for timeout
        sanity_loss = 20
        hp_loss = 30
        madness_gain = random.randint(2, 4)

        player_data["sanity"] -= sanity_loss
        player_data["hp"] -= hp_loss
        player_data["madness_points"] += madness_gain

        em = discord.Embed(
            title="⚠️ PARALYZED BY FEAR ⚠️",
            description=(
                f"{target.mention} freezes before the horror!\n\n"
                "```diff\n"
                f"- Sanity -{sanity_loss}\n"
                f"- HP -{hp_loss}\n"
                "- The void grows stronger\n"
                f"- Madness points +{madness_gain}\n"
                "```\n\n"
                "*Their hesitation feeds Eclipse's power...*"
            ),
            color=0xFF0000
        )
        await self.send_to_channels(embed=em, raid=raid)

        # Boss gets stronger when players timeout
        raid.boss_hp += random.randint(150, 400)

        await self.check_player_state(target, raid)

        if len(raid.participants) > 0 and raid.boss_hp > 0:
            await asyncio.sleep(5)
            await self.start_next_turn(raid)
        else:
            await self.handle_defeat(raid)

    @staticmethod
    def get_status_effects(player_data: Dict) -> str:
        """Get a formatted string of all active status effects on a player"""
        effects = []

        # Core status effects
        if player_data.get("corrupted"):
            effects.append("CORRUPTED")
        if player_data.get("cursed"):
            effects.append("CURSED")
        if player_data.get("void_touched"):
            effects.append("VOID-TOUCHED")

        # Dynamic status effects based on stats
        if player_data.get("sanity", 100) < 30:
            effects.append("DERANGED")
        if player_data.get("sanity", 100) < 15:
            effects.append("BREAKING")
        if player_data.get("hp", 250) < 50:
            effects.append("DYING")
        if player_data.get("hp", 250) < 25:
            effects.append("CRITICAL")
        if player_data.get("madness_points", 0) >= 8:
            effects.append("TRANSFORMING")

        # Return formatted string
        return " | ".join(effects) if effects else "NORMAL"

    async def trigger_horror_event(self, target: discord.User, raid: Raid):
        player_data = raid.participants[target]

        horrors = [
            {
                "name": "Void Tendril",
                "description": (
                    "A writhing tentacle emerges from the void...\n"
                    "🎯 Dodge: Safe but weak damage (70% success)\n"
                    "⚔️ Sever: Risky but high damage (50% success)\n"
                    "🌀 Embrace: Very risky, chance for void power (30% success)"
                ),
                "choices": [
                    ("🎯 Dodge", "dodge", 0.7, "You attempt to evade the horror..."),
                    ("⚔️ Sever", "attack", 0.5, "You strike at the otherworldly flesh..."),
                    ("🌀 Embrace", "corrupt", 0.3, "You allow it to touch your soul...")
                ],
                "image": "https://i.imgur.com/YS4A6R7.png"
            },
            {
                "name": "Reality Fracture",
                "description": (
                    "The fabric of space tears before you...\n"
                    "🛡️ Shield: Protect your mind (65% success)\n"
                    "👁️ Peer: Look into the void (45% success)\n"
                    "🌌 Reach: Grasp forbidden knowledge (25% success)"
                ),
                "choices": [
                    ("🛡️ Shield", "dodge", 0.65, "You try to shield your mind..."),
                    ("👁️ Peer", "attack", 0.45, "You gaze into the infinite..."),
                    ("🌌 Reach", "corrupt", 0.25, "You reach for impossible power...")
                ],
                "image": "https://i.imgur.com/UpWW3fF.png"
            },
            {
                "name": "Shadow Echo",
                "description": (
                    "Your reflection moves independently...\n"
                    "🏃 Run: Flee from your shadow (75% success)\n"
                    "⚔️ Fight: Battle your reflection (55% success)\n"
                    "🤝 Merge: Become one with shadow (35% success)"
                ),
                "choices": [
                    ("🏃 Run", "dodge", 0.75, "You try to escape your shadow..."),
                    ("⚔️ Fight", "attack", 0.55, "You strike at your dark twin..."),
                    ("🤝 Merge", "corrupt", 0.35, "You embrace the darkness...")
                ],
                "image": "https://i.imgur.com/s5tvHMd.png"
            }
        ]

        horror = random.choice(horrors)
        raid.is_choice_phase = True

        horror_event_view = View(timeout=30)

        for choice_text, choice_id, success_rate, action_text in horror["choices"]:
            callback = functools.partial(
                self.choice_handler,
                choice_id=choice_id,
                success_rate=success_rate,
                action_text=action_text,
                target=target,
                horror_image=horror["image"],
                raid=raid
            )

            choice_button = Button(
                style=ButtonStyle.danger,
                label=choice_text,
                custom_id=f"{choice_id}_{uuid.uuid4()}"
            )
            choice_button.callback = callback
            horror_event_view.add_item(choice_button)

        em = discord.Embed(
            title=f"🕷️ {horror['name']} 🕷️",
            description=(
                f"{target.mention}, it's your turn to face the horror!\n\n"
                f"*{horror['description']}*\n\n"
                "```diff\n"
                f"Your Status:\n"
                f"- HP: {player_data['hp']}\n"
                f"- Sanity: {player_data['sanity']}\n"
                f"Effects: {self.get_status_effects(player_data)}\n"
                "```\n"
                "You have 30 seconds to choose..."
            ),
            color=0x000000
        )
        em.set_thumbnail(url=horror["image"])

        await self.send_to_channels(embed=em, view=horror_event_view, raid=raid)

        await asyncio.sleep(30)

        if raid.is_choice_phase:
            raid.is_choice_phase = False
            await self.handle_timeout(target, raid)


    async def handle_victory(self, raid: Raid):
        survivors = [p for p, data in raid.participants.items() if data["hp"] > 0]
        if survivors:
            winner = random.choice(survivors)
            cursed_rewards = {
                'fortune': {
                    'chance': 0.4,
                    'effect': 'Corrupts your inventory with void essence...',
                    'bonus': 'void_essence'
                },
                'legendary': {
                    'chance': 0.3,
                    'effect': 'Fragments of lost minds swirl within...',
                    'bonus': 'madness_shard'
                },
                'divine': {
                    'chance': 0.3,
                    'effect': 'Pure horror in material form...',
                    'bonus': 'horror_fragment'
                }
            }

            reward = random.choices(
                list(cursed_rewards.keys()),
                weights=[r['chance'] for r in cursed_rewards.values()]
            )[0]

            async with self.bot.pool.acquire() as conn:
                await conn.execute(
                    f'UPDATE profile SET "crates_{reward}"="crates_{reward}"+1 WHERE "user"=$1;',
                    winner.id
                )

            em = discord.Embed(
                title="🕷️ THE HORROR ENDS... FOR NOW 🕷️",
                description=(
                    f"{winner.mention} survives with their body intact... mostly.\n\n"
                    f"**Reward: {reward}**\n"
                    f"*{cursed_rewards[reward]['effect']}*\n\n"
                    f"**Bonus: {cursed_rewards[reward]['bonus']}**\n\n"
                    "```diff\n"
                    "- The void remembers those who challenged it...\n"
                    "- Your nightmares will never be the same...\n"
                    "- Eclipse will return...stronger...hungrier...\n"
                    "```"
                ),
                color=0x000000
            )
            em.set_image(url="https://i.imgur.com/s5tvHMd.png")
            await self.send_to_channels(embed=em, raid=raid)
        else:
            await self.handle_defeat(raid)

        if raid.channels[0].id in self.active_raids:
            del self.active_raids[raid.channels[0].id]

    async def handle_defeat(self, raid: Raid):
        em = discord.Embed(
            title="💀 TOTAL PARTY KILL 💀",
            description=(
                "```diff\n"
                "- No survivors remain\n"
                "- The void consumes all\n"
                "- Eclipse grows stronger\n"
                "```\n"
                "*The screams of the fallen echo through dimensions...*"
            ),
            color=0xFF0000
        )
        em.set_image(url="https://i.imgur.com/UpWW3fF.png")
        await self.send_to_channels(embed=em, raid=raid)

        if raid.channels[0].id in self.active_raids:
            del self.active_raids[raid.channels[0].id]


async def setup(bot):
    await bot.add_cog(HorrorRaid(bot))