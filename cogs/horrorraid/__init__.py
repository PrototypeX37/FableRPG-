import discord
from discord.ext import commands
from discord.ui import View, Button
from discord.enums import ButtonStyle
import asyncio
import random
import datetime
import traceback
import uuid

# If you have a custom "is_gm" check:
from utils.checks import is_gm


# ------------------------------------------------
#                 RAID CLASS
# ------------------------------------------------
class Raid:
    def __init__(self, boss_hp, channels):
        self.initial_boss_hp = boss_hp
        self.boss_hp = boss_hp
        self.channels = channels
        self.participants = {}
        self.defeated_players = set()
        self.insane_players = set()
        self.join_phase = True
        self.raid_started = False
        self.turn_order = []
        self.current_turn = None
        self.is_choice_phase = False
        self.view = View(timeout=None)

        # Boss states
        self.current_form = 1
        self.group_sanity = 1000
        self.synergy_triggered = False
        self.final_summary_posted = False
        self.aggro_table = {}

        # Mid-raid objectives
        self.mid_raid_objectives = [
            {
                "name": "Seal the Dark Altar",
                "description": "A pulsating altar from the void must be neutralized before it empowers Eclipse further.",
                "completed": False,
                "reward": 200
            },
            {
                "name": "Banish the Wailing Specters",
                "description": "Ghostly apparitions roam the field, draining everyone's sanity.",
                "completed": False,
                "reward": 150
            }
        ]
        self.current_objective = None
        self.objective_deadline = None

        # Boss turn toggles
        self.is_boss_turn = False

    def remove_player(self, player: discord.User):
        """Remove a player from the raid participants and turn order."""
        if player in self.participants:
            del self.participants[player]
        if player in self.turn_order:
            self.turn_order.remove(player)
        if player in self.aggro_table:
            del self.aggro_table[player]


# ------------------------------------------------
#           HORROR BUTTON CLASS
# ------------------------------------------------
class HorrorButton(Button):
    def __init__(self, style, label, callback):
        super().__init__(style=style, label=label)
        self.callback_func = callback

    async def callback(self, interaction: discord.Interaction):
        await self.callback_func(interaction)


# ------------------------------------------------
#           HORROR RAID COG
# ------------------------------------------------
class HorrorRaid(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_raids = {}

    # --------------------------------------------
    #      BASIC UTILS: Sending Messages
    # --------------------------------------------
    async def send_to_channels(self, *, embed=None, content=None, view=None, raid: Raid = None):
        """Send a message to all channels associated with a given raid."""
        for ch in raid.channels:
            if ch:
                try:
                    await ch.send(embed=embed, content=content, view=view)
                except Exception as e:
                    print(f"[send_to_channels] Error: {e}")

    async def create_lore_thread(self, raid: Raid):
        """Optional: create a separate thread for lore or side-narration."""
        main_channel = raid.channels[0]
        try:
            lore_thread = await main_channel.create_thread(
                name="Raid-Lore-Thread",
                auto_archive_duration=60
            )
            return lore_thread
        except Exception as e:
            print(f"Error creating thread: {e}")
            return None

    # --------------------------------------------
    #  Chat Reading & Thematic Reaction (50% CHANCE)
    # --------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """
        Checks if there's an active raid in this channel.
        If certain keywords appear, there's a 50% chance Eclipse responds.
        Even after the first manifest, we keep listening until the raid ends.
        """
        if message.author.bot:
            return

        raid = self.active_raids.get(message.channel.id)
        # Keep listening the entire raid, so check `raid` and `raid.raid_started`
        if not raid or not raid.raid_started:
            return

        content_lower = message.content.lower()
        triggered_keywords = ["attack", "focus", "guard", "betray", "plan", "strategy", "dark gift"]

        if any(kw in content_lower for kw in triggered_keywords):
            if random.random() < 0.50:  # 50% chance
                if not raid.participants:
                    return

                victim = random.choice(list(raid.participants.keys()))
                data = raid.participants[victim]
                chat_taunts = [
                    f"**Eclipse**: *I hear your schemes, {message.author.mention}. They are futile...*",
                    f"**Eclipse**: *Your words amuse me, {message.author.mention}... keep talking.*",
                    f"**Eclipse**: *Whisper louder, {message.author.mention}... let the void listen.*",
                    f"**Eclipse**: *Your 'plans' feed the darkness, {message.author.mention}.*",
                    f"**Eclipse**: *Your mortal plotting, {message.author.mention}, tastes of fear.*"
                ]
                chosen_line = random.choice(chat_taunts)

                guard_active = data.pop("guard_active", False)
                sanity_loss = random.randint(5, 10)
                if guard_active:
                    sanity_loss //= 2
                data["sanity"] = max(0, data["sanity"] - sanity_loss)

                em = discord.Embed(
                    title="ECLIPSE HEARS YOU",
                    description=(
                        f"{chosen_line}\n\n"
                        f"{victim.mention} feels a psychic lance pierce their mind!\n"
                        f"```diff\n- {sanity_loss} Sanity\n```"
                    ),
                    color=0x8B008B
                )
                await self.send_to_channels(embed=em, raid=raid)
                # Check if it kills or breaks them
                await self.check_player_state(victim, raid)

    # --------------------------------------------
    #    Drakath Follower Check (DB Example)
    # --------------------------------------------
    async def is_drakath_follower(self, user: discord.User) -> bool:
        async with self.bot.pool.acquire() as conn:
            result = await conn.fetchrow(
                'SELECT god FROM profile WHERE "user"=$1;',
                user.id
            )
            return result and result['god'] == 'Drakath'

    # --------------------------------------------
    #  MAIN COMMAND: Start the ECLIPSE Raid
    # --------------------------------------------
    @commands.command(hidden=True)
    @is_gm()
    async def chaosspawnbeta(self, ctx, boss_hp: int):
        """Starts a cosmic-horror raid with a 50% chat-listening chance, HP clamp, and turn fix."""
        try:
            channels = [
                self.bot.get_channel(1313482408242184213)
            ]
            if not channels[0]:
                await ctx.send("Raid channel not found.")
                return

            raid = Raid(boss_hp=boss_hp, channels=channels)
            self.active_raids[ctx.channel.id] = raid

            lore_thread = await self.create_lore_thread(raid)
            if lore_thread:
                raid.channels.append(lore_thread)

            # Join Button
            async def join_raid_callback(interaction: discord.Interaction):
                try:
                    if not raid.join_phase:
                        await interaction.response.send_message(
                            "```diff\n- The horror has manifested. You cannot join now.\n```",
                            ephemeral=True
                        )
                        return

                    is_drakath = await self.is_drakath_follower(interaction.user)
                    if not is_drakath:
                        await interaction.response.send_message(
                            "```diff\n- The void rejects you. Only Drakath's chosen may enter.\n```",
                            ephemeral=True
                        )
                        return

                    if interaction.user in raid.participants:
                        await interaction.response.send_message(
                            "```diff\n- You have already joined...\n```",
                            ephemeral=True
                        )
                        return

                    consumables = ["Lunarium Talisman", "Arcane Ward", "Bloodthistle Potion", "Ethereal Lantern"]
                    chosen = random.choice(consumables)

                    raid.participants[interaction.user] = {
                        "hp": 250,
                        "sanity": 100,
                        "corrupted": False,
                        "cursed": False,
                        "void_touched": False,
                        "madness_points": 0,
                        "last_curse_time": None,
                        "consumable": chosen,
                        "betray_charges": 1,
                        "guard_active": False,
                        "dark_gift_cooldown": 0
                    }
                    raid.aggro_table[interaction.user] = 0

                    em = discord.Embed(
                        description=(
                            f"{interaction.user.mention} drifts into the collapsing void...\n"
                            f"**Starting Consumable**: {chosen}"
                        ),
                        color=0x000000
                    )
                    await interaction.response.send_message(
                        "```diff\n- The cosmic rift widens at your approach...\n```",
                        ephemeral=True
                    )
                    await self.send_to_channels(embed=em, raid=raid)
                except Exception as e:
                    print(f"Join raid error: {e}")
                    await interaction.response.send_message(
                        f"Error: {e}", ephemeral=True
                    )

            join_button = HorrorButton(
                style=ButtonStyle.danger,
                label="JOIN THE HORROR",
                callback=join_raid_callback
            )
            raid.view.add_item(join_button)

            intro_em = discord.Embed(
                title="🌑 ECLIPSE BREACH 🌑",
                description=(
                    "A dimensional rift tears open the sky. **Eclipse** stirs beyond...\n\n"
                    "**15 minutes** remain until full manifestation."
                ),
                color=0x000000
            )
            await self.send_to_channels(embed=intro_em, view=raid.view, raid=raid)

            total_time = 2
            prev = total_time
            milestones = [2, 1]

            for m in milestones:
                diff = (prev - m) * 60
                if diff > 0:
                    await asyncio.sleep(diff)
                await self.update_countdown_message(raid, m)
                prev = m

            final_sleep = prev * 60
            if final_sleep > 0:
                await asyncio.sleep(final_sleep)

            for sec in [30, 10]:
                await asyncio.sleep(60 - sec)
                c_em = discord.Embed(
                    title="⚠️ REALITY BREACH IMMINENT ⚠️",
                    description=f"```diff\n- TIME LEFT: {sec} SECONDS\n```",
                    color=0xFF0000
                )
                await self.send_to_channels(embed=c_em, raid=raid)

            await asyncio.sleep(10)

            raid.join_phase = False
            if len(raid.participants) < 1:
                em = discord.Embed(
                    title="NO CHALLENGERS",
                    description="Eclipse senses no prey and recedes into the void.",
                    color=0x000000
                )
                await self.send_to_channels(embed=em, raid=raid)
                del self.active_raids[ctx.channel.id]
                return

            raid.current_objective = random.choice(raid.mid_raid_objectives)
            raid.objective_deadline = datetime.datetime.utcnow() + datetime.timedelta(minutes=5)

            start_em = discord.Embed(
                title="🕯️ THE HORROR ARRIVES 🕯️",
                description=(
                    "Reality distorts as **Eclipse** fully manifests.\n"
                    "The air crackles with unspeakable dread..."
                ),
                color=0xFF0000
            )
            await self.send_to_channels(embed=start_em, raid=raid)
            await self.announce_objective(raid)

            raid.raid_started = True
            await asyncio.sleep(2)
            self.build_turn_order(raid)
            await self.start_next_turn(raid)

        except Exception as e:
            tb = traceback.format_exc()
            await ctx.send(f"Error: {e}\n```py\n{tb}\n```")
            print(tb)

    def build_turn_order(self, raid: Raid):
        plist = list(raid.participants.keys())
        random.shuffle(plist)
        raid.turn_order = plist
        raid.current_turn = None

    async def update_countdown_message(self, raid: Raid, minutes_left: int):
        em = discord.Embed(
            title="⏳ ECLIPSE APPROACHES ⏳",
            description=f"```diff\n- {minutes_left} MINUTES UNTIL FULL MANIFESTATION\n```",
            color=0xFFA500
        )
        await self.send_to_channels(embed=em, raid=raid)

    # ----------------------------------------------------------
    #                   TURN LOGIC
    # ----------------------------------------------------------
    async def start_next_turn(self, raid: Raid):
        """Alternate Player turn → Boss turn → Next Player turn → etc."""
        if not raid.participants:
            # If no participants remain, handle defeat
            await self.handle_defeat(raid)
            return

        if raid.is_boss_turn:
            raid.is_boss_turn = False
            await self._advance_player_turn(raid)
        else:
            if raid.raid_started:
                raid.is_boss_turn = True
                await self.boss_turn(raid)

    async def _advance_player_turn(self, raid: Raid):
        valid_ids = set(raid.participants.keys())
        if not raid.turn_order or not set(raid.turn_order).issubset(valid_ids):
            self.build_turn_order(raid)

        if raid.current_turn in raid.participants:
            idx = raid.turn_order.index(raid.current_turn)
            nxt_idx = (idx + 1) % len(raid.turn_order)
            raid.current_turn = raid.turn_order[nxt_idx]
        else:
            raid.current_turn = raid.turn_order[0]

        await self.check_evolving_forms(raid)
        await self.check_synergy_event(raid)
        await self.check_group_sanity(raid)
        await self.check_objective_deadline(raid)

        if raid.current_turn not in raid.participants:
            await self.handle_defeat(raid)
            return

        await self.show_player_action_menu(raid)

    async def show_player_action_menu(self, raid: Raid):
        raid.is_choice_phase = True
        player = raid.current_turn

        desc = (
            f"**{player.mention}, it's your turn!**\n\n"
            f"**Boss HP**: {raid.boss_hp}\n"
            f"**Group Sanity**: {raid.group_sanity}\n"
            f"**Active**: {len(raid.participants)} | **Fallen**: {len(raid.defeated_players)} "
            f"| **Insane**: {len(raid.insane_players)}\n\n"
            "*Select an action below (30s). Eclipse waits for no one.*"
        )
        em = discord.Embed(
            title="PLAYER TURN",
            description=desc,
            color=0x87CEFA
        )
        view = View(timeout=30)

        # Buttons
        btn_attack = Button(style=ButtonStyle.danger, label="Attack")
        btn_guard = Button(style=ButtonStyle.primary, label="Guard")
        btn_focus = Button(style=ButtonStyle.success, label="Focus")
        btn_betray = Button(style=ButtonStyle.danger, label="Betray")
        btn_consumable = Button(style=ButtonStyle.secondary, label="Use Consumable")
        btn_stats = Button(style=ButtonStyle.blurple, label="Check Stats")

        if raid.participants[player].get("void_touched"):
            btn_darkgift = Button(style=ButtonStyle.danger, label="Dark Gift")
            view.add_item(btn_darkgift)

        if raid.current_objective and not raid.current_objective["completed"]:
            btn_objective = Button(style=ButtonStyle.success, label="Complete Objective")
            view.add_item(btn_objective)

        async def disable_view_and_call(interaction: discord.Interaction, func):
            """Disable all buttons, end choice phase, call the action function."""
            for item in view.children:
                item.disabled = True
            await interaction.message.edit(view=view)
            raid.is_choice_phase = False
            await interaction.response.defer()
            await func(interaction)

        # Action callbacks
        async def attack_cb(interaction: discord.Interaction):
            if interaction.user != player:
                await interaction.response.send_message("Not your turn!", ephemeral=True)
                return
            await disable_view_and_call(interaction, lambda i: self.player_attack(player, raid))

        async def guard_cb(interaction: discord.Interaction):
            if interaction.user != player:
                await interaction.response.send_message("Not your turn!", ephemeral=True)
                return
            await disable_view_and_call(interaction, lambda i: self.player_guard(player, raid))

        async def focus_cb(interaction: discord.Interaction):
            if interaction.user != player:
                await interaction.response.send_message("Not your turn!", ephemeral=True)
                return
            await disable_view_and_call(interaction, lambda i: self.player_focus(player, raid))

        async def betray_cb(interaction: discord.Interaction):
            if interaction.user != player:
                await interaction.response.send_message("Not your turn!", ephemeral=True)
                return
            await disable_view_and_call(interaction, lambda i: self.player_betray(player, raid))

        async def consumable_cb(interaction: discord.Interaction):
            if interaction.user != player:
                await interaction.response.send_message("Not your turn!", ephemeral=True)
                return
            await disable_view_and_call(interaction, lambda i: self.player_consumable(player, raid))

        async def stats_cb(interaction: discord.Interaction):
            # Checking stats doesn't end the turn or disable the menu
            await self.show_player_stats_ephemeral(interaction, raid)

        async def darkgift_cb(interaction: discord.Interaction):
            if interaction.user != player:
                await interaction.response.send_message("Not your turn!", ephemeral=True)
                return
            await disable_view_and_call(interaction, lambda i: self.player_darkgift(player, raid))

        async def objective_cb(interaction: discord.Interaction):
            if interaction.user != player:
                await interaction.response.send_message("Not your turn!", ephemeral=True)
                return
            # Note: We must also continue the turn after completing the objective!
            await disable_view_and_call(interaction, lambda i: self.player_complete_objective(player, raid))

        # Link
        btn_attack.callback = attack_cb
        btn_guard.callback = guard_cb
        btn_focus.callback = focus_cb
        btn_betray.callback = betray_cb
        btn_consumable.callback = consumable_cb
        btn_stats.callback = stats_cb

        view.add_item(btn_attack)
        view.add_item(btn_guard)
        view.add_item(btn_focus)
        view.add_item(btn_betray)
        view.add_item(btn_consumable)
        view.add_item(btn_stats)

        if raid.participants[player].get("void_touched"):
            btn_darkgift.callback = darkgift_cb

        if raid.current_objective and not raid.current_objective["completed"]:
            btn_obj = [i for i in view.children if i.label == "Complete Objective"][0]
            btn_obj.callback = objective_cb

        await self.send_to_channels(embed=em, view=view, raid=raid)
        await asyncio.sleep(30)

        if raid.is_choice_phase:
            # user did nothing
            raid.is_choice_phase = False
            for item in view.children:
                item.disabled = True
            await self.handle_timeout(player, raid)

    async def end_player_turn(self, raid: Raid):
        await self.start_next_turn(raid)

    # ----------------------------------------------------------
    #            PLAYER ACTIONS
    # ----------------------------------------------------------
    async def clamp_boss_hp(self, raid: Raid):
        if raid.boss_hp < 0:
            raid.boss_hp = 0
            # Check for immediate victory if boss HP is 0
            await self.handle_victory(raid)

    async def clamp_player_hp(self, player_data: dict):
        if player_data["hp"] < 0:
            player_data["hp"] = 0

    async def player_attack(self, player: discord.User, raid: Raid):
        data = raid.participants[player]
        if random.random() <= 0.7:
            dmg = random.randint(150, 300)
            if data["void_touched"]:
                dmg = int(dmg * 1.5)
            if data["cursed"]:
                dmg = int(dmg * 0.7)

            raid.boss_hp -= dmg
            await self.clamp_boss_hp(raid)
            raid.aggro_table[player] = raid.aggro_table.get(player, 0) + dmg

            em = discord.Embed(
                title="ATTACK SUCCESS",
                description=(
                    f"{player.mention} strikes Eclipse!\n"
                    f"```diff\n+Damage: {dmg}\n```"
                ),
                color=0x00FF00
            )
        else:
            backfire = random.randint(20, 50)
            data["hp"] -= backfire
            await self.clamp_player_hp(data)
            em = discord.Embed(
                title="ATTACK FAILED",
                description=(
                    f"{player.mention} attempts a strike, but Eclipse warps reality\n"
                    f"reflecting **{backfire}** damage back!"
                ),
                color=0xFF0000
            )

        await self.send_to_channels(embed=em, raid=raid)
        await self.check_player_state(player, raid)
        await self.end_player_turn(raid)

    async def player_guard(self, player: discord.User, raid: Raid):
        data = raid.participants[player]
        data["guard_active"] = True
        em = discord.Embed(
            title="GUARD",
            description=(
                f"{player.mention} braces for the incoming horror!\n"
                "```diff\n+ Next negative effect halved!\n```"
            ),
            color=0x00FF00
        )
        await self.send_to_channels(embed=em, raid=raid)
        await self.check_player_state(player, raid)
        await self.end_player_turn(raid)

    async def player_focus(self, player: discord.User, raid: Raid):
        data = raid.participants[player]
        if random.random() <= 0.6:
            restore = random.randint(15, 30)
            old_sanity = data["sanity"]
            data["sanity"] = min(100, data["sanity"] + restore)
            gained = data["sanity"] - old_sanity

            em = discord.Embed(
                title="FOCUS",
                description=(
                    f"{player.mention} fortifies their mind!\n"
                    f"```diff\n+Sanity Restored: {gained}\n```"
                ),
                color=0x00FF00
            )
        else:
            backlash = random.randint(10, 25)
            data["hp"] -= backlash
            await self.clamp_player_hp(data)
            raid.group_sanity -= 10
            em = discord.Embed(
                title="FOCUS FAILED",
                description=(
                    f"{player.mention} tries to repel the whispers but fails,\n"
                    f"**{backlash}** HP lost, -10 Group Sanity!"
                ),
                color=0xFF0000
            )

        await self.send_to_channels(embed=em, raid=raid)
        await self.check_player_state(player, raid)
        await self.end_player_turn(raid)

    async def player_betray(self, player: discord.User, raid: Raid):
        data = raid.participants[player]
        if data["betray_charges"] <= 0:
            em = discord.Embed(
                title="BETRAYAL FAILED",
                description="No betrayal charges remain.",
                color=0xFF0000
            )
            await self.send_to_channels(embed=em, raid=raid)
            await self.end_player_turn(raid)
            return

        valid_targets = [p for p in raid.participants if p != player]
        if not valid_targets:
            em = discord.Embed(
                title="NO TARGETS",
                description="No allies remain to betray...",
                color=0xFF0000
            )
            await self.send_to_channels(embed=em, raid=raid)
            await self.end_player_turn(raid)
            return

        victim = random.choice(valid_targets)
        vdata = raid.participants[victim]
        dmg = random.randint(30, 60)
        vdata["hp"] -= dmg
        await self.clamp_player_hp(vdata)

        data["betray_charges"] -= 1
        raid.group_sanity -= 30
        raid.aggro_table[player] = raid.aggro_table.get(player, 0) + 10

        benefit_text = ""
        if self.player_can_benefit_from_betrayal(data, raid):
            heal_amount = random.randint(15, 30)
            data["hp"] = min(data["hp"] + heal_amount, 250)
            benefit_text = f"\n+ {player.mention} recovers {heal_amount} HP!"

        em = discord.Embed(
            title="BETRAYAL",
            description=(
                f"{player.mention} betrays {victim.mention}!\n"
                "```diff\n"
                f"- {victim.display_name} loses {dmg} HP\n"
                f"- Group Sanity -30\n"
                f"{benefit_text}\n"
                "```"
            ),
            color=0xFF0000
        )
        await self.send_to_channels(embed=em, raid=raid)
        await self.check_player_state(victim, raid)
        await self.check_player_state(player, raid)
        await self.end_player_turn(raid)

    async def player_consumable(self, player: discord.User, raid: Raid):
        data = raid.participants[player]
        c = data.get("consumable")
        if not c:
            em = discord.Embed(
                title="NO CONSUMABLE",
                description="You have no item to use!",
                color=0xFF0000
            )
            await self.send_to_channels(embed=em, raid=raid)
            await self.end_player_turn(raid)
            return

        effect_desc = ""
        if c == "Lunarium Talisman":
            old_sanity = data["sanity"]
            data["sanity"] = min(100, data["sanity"] + 30)
            gained = data["sanity"] - old_sanity
            effect_desc = f"**+{gained} Sanity**"
        elif c == "Arcane Ward":
            data["guard_active"] = True
            effect_desc = "A protective aura forms around you (Guard)."
        elif c == "Bloodthistle Potion":
            raid.boss_hp -= 100
            await self.clamp_boss_hp(raid)
            raid.aggro_table[player] = raid.aggro_table.get(player, 0) + 30
            effect_desc = "**-100 Boss HP**"
        elif c == "Ethereal Lantern":
            raid.group_sanity += 50
            effect_desc = "**+50 Group Sanity**"

        data["consumable"] = None
        em = discord.Embed(
            title="CONSUMABLE USED",
            description=(
                f"{player.mention} uses **{c}**!\n"
                f"{effect_desc}"
            ),
            color=0x00FF00
        )
        await self.send_to_channels(embed=em, raid=raid)
        await self.check_player_state(player, raid)
        await self.end_player_turn(raid)

    async def player_darkgift(self, player: discord.User, raid: Raid):
        data = raid.participants[player]
        if data["dark_gift_cooldown"] > 0:
            em = discord.Embed(
                title="DARK GIFT UNAVAILABLE",
                description="Your void power is still recovering.",
                color=0xFF0000
            )
            await self.send_to_channels(embed=em, raid=raid)
            await self.end_player_turn(raid)
            return

        if random.random() <= 0.5:
            dmg = random.randint(300, 600)
            raid.boss_hp -= dmg
            await self.clamp_boss_hp(raid)
            recoil = random.randint(10, 30)
            data["sanity"] = max(0, data["sanity"] - recoil)
            raid.aggro_table[player] = raid.aggro_table.get(player, 0) + dmg

            em = discord.Embed(
                title="DARK GIFT",
                description=(
                    f"{player.mention} channels a torrent of void energy!\n"
                    f"```diff\n+{dmg} damage to Eclipse\n-{recoil} sanity\n```"
                ),
                color=0x800080
            )
            data["dark_gift_cooldown"] = 2
        else:
            backlash = random.randint(20, 50)
            data["hp"] -= backlash
            await self.clamp_player_hp(data)
            data["madness_points"] += 2

            em = discord.Embed(
                title="DARK GIFT BACKFIRES",
                description=(
                    f"{player.mention} fails to harness the void,\n"
                    f"suffering **{backlash}** HP damage and +2 Madness."
                ),
                color=0xFF0000
            )
            data["dark_gift_cooldown"] = 1

        await self.send_to_channels(embed=em, raid=raid)
        await self.check_player_state(player, raid)
        await self.end_player_turn(raid)

    def player_can_benefit_from_betrayal(self, data: dict, raid: Raid) -> bool:
        if data["cursed"] or data["sanity"] < 50 or raid.group_sanity < 300:
            return True
        return False

    # ----------------------------------------------------------
    #                   BOSS TURN
    # ----------------------------------------------------------
    async def boss_turn(self, raid: Raid):
        raid.is_boss_turn = True
        await self.check_evolving_forms(raid)

        if not raid.participants:
            await self.handle_defeat(raid)
            return

        # pick target via aggro
        target = None
        if raid.aggro_table:
            target = max(raid.aggro_table, key=raid.aggro_table.get)
        if not target or target not in raid.participants:
            target = random.choice(list(raid.participants.keys()))

        em = discord.Embed(
            title="ECLIPSE'S TURN",
            description=(
                "Eclipse swells with malice...\n"
                f"**Target**: {target.mention}"
            ),
            color=0x8B0000
        )
        await self.send_to_channels(embed=em, raid=raid)

        form = raid.current_form
        roll = random.random()

        if form == 1:
            await self.boss_basic_attack(raid, target)
        elif form == 2:
            if roll < 0.5:
                await self.boss_basic_attack(raid, target)
            else:
                await self.boss_void_spike(raid, target)
        elif form == 3:
            if roll < 0.3:
                await self.boss_void_spike(raid, target)
            elif roll < 0.6:
                await self.boss_mind_break(raid, target)
            else:
                await self.boss_basic_attack(raid, target)
        elif form == 4:
            if roll < 0.25:
                await self.boss_void_spike(raid, target)
            elif roll < 0.5:
                await self.boss_mind_break(raid, target)
            else:
                await self.boss_reality_rend(raid, target)
        elif form == 5:
            if roll < 0.2:
                await self.boss_void_spike(raid, target)
            elif roll < 0.4:
                await self.boss_mind_break(raid, target)
            elif roll < 0.6:
                await self.boss_reality_rend(raid, target)
            else:
                await self.boss_aoe_havoc(raid)
        else:
            if roll < 0.2:
                await self.boss_void_spike(raid, target)
            elif roll < 0.4:
                await self.boss_mind_break(raid, target)
            elif roll < 0.6:
                await self.boss_reality_rend(raid, target)
            else:
                await self.boss_aoe_havoc(raid)

        raid.is_boss_turn = False
        await asyncio.sleep(3)
        await self._advance_player_turn(raid)

    # Boss abilities
    async def boss_basic_attack(self, raid: Raid, target: discord.User):
        data = raid.participants[target]
        guard_active = data.pop("guard_active", False)
        dmg = random.randint(20, 50)
        if guard_active:
            dmg //= 2
        data["hp"] -= dmg
        await self.clamp_player_hp(data)

        em = discord.Embed(
            title="ECLIPSE STRIKES",
            description=(
                f"Eclipse's tendril slams {target.mention} for {dmg} HP!"
            ),
            color=0x8B0000
        )
        await self.send_to_channels(embed=em, raid=raid)
        await self.check_player_state(target, raid)

    async def boss_void_spike(self, raid: Raid, target: discord.User):
        data = raid.participants[target]
        guard_active = data.pop("guard_active", False)
        dmg = random.randint(40, 80)
        if guard_active:
            dmg //= 2
        data["hp"] -= dmg
        await self.clamp_player_hp(data)
        raid.group_sanity -= 20

        em = discord.Embed(
            title="VOID SPIKE",
            description=(
                f"Eclipse coalesces void matter into a spike, impaling {target.mention}!\n"
                f"**-{dmg} HP**, -20 Group Sanity"
            ),
            color=0x4B0082
        )
        await self.send_to_channels(embed=em, raid=raid)
        await self.check_player_state(target, raid)

    async def boss_mind_break(self, raid: Raid, target: discord.User):
        data = raid.participants[target]
        guard_active = data.pop("guard_active", False)
        s_loss = random.randint(20, 40)
        if guard_active:
            s_loss //= 2
        data["sanity"] = max(0, data["sanity"] - s_loss)

        em = discord.Embed(
            title="MIND BREAK",
            description=(
                f"Eclipse saturates {target.mention}'s mind with horrific visions!\n"
                f"**-{s_loss} Sanity**"
            ),
            color=0x800080
        )
        await self.send_to_channels(embed=em, raid=raid)
        await self.check_player_state(target, raid)

    async def boss_reality_rend(self, raid: Raid, target: discord.User):
        data = raid.participants[target]
        guard_active = data.pop("guard_active", False)
        dmg = random.randint(60, 100)
        if guard_active:
            dmg //= 2
        data["hp"] -= dmg
        await self.clamp_player_hp(data)

        s_loss = random.randint(5, 15)
        data["sanity"] = max(0, data["sanity"] - s_loss)

        em = discord.Embed(
            title="REALITY REND",
            description=(
                f"Eclipse distorts space around {target.mention}!\n"
                f"```diff\n-HP {dmg}, -Sanity {s_loss}\n```"
            ),
            color=0xFF1493
        )
        await self.send_to_channels(embed=em, raid=raid)
        await self.check_player_state(target, raid)

    async def boss_aoe_havoc(self, raid: Raid):
        em = discord.Embed(
            title="AEONIC HAVOC",
            description=(
                "Eclipse conjures a howling rift, assailing **all** who remain!"
            ),
            color=0x8B0000
        )
        await self.send_to_channels(embed=em, raid=raid)

        for player, data in list(raid.participants.items()):
            guard_active = data.pop("guard_active", False)
            dmg = random.randint(20, 40)
            s_loss = random.randint(5, 10)
            if guard_active:
                dmg //= 2
                s_loss //= 2
            data["hp"] -= dmg
            await self.clamp_player_hp(data)
            data["sanity"] = max(0, data["sanity"] - s_loss)
            await self.check_player_state(player, raid)

    # ----------------------------------------------------------
    #       EVOLVING FORMS (percentage-based)
    # ----------------------------------------------------------
    async def check_evolving_forms(self, raid: Raid):
        if raid.initial_boss_hp <= 0:
            return
        hp_ratio = raid.boss_hp / raid.initial_boss_hp

        form_upgrades = [
            (2, 0.8),
            (3, 0.6),
            (4, 0.4),
            (5, 0.2),
            (6, 0.1)
        ]
        for new_form, threshold in form_upgrades:
            if raid.current_form < new_form and hp_ratio < threshold:
                raid.current_form = new_form
                await self.evolve_boss(raid, new_form)

    async def evolve_boss(self, raid: Raid, new_form: int):
        penalty = new_form * 25
        raid.group_sanity -= penalty
        em = discord.Embed(
            title=f"ECLIPSE EVOLVES - FORM {new_form}",
            description=(
                "A churning metamorphosis reshapes Eclipse,\n"
                f"**Group Sanity** -{penalty}\n"
                "Its presence intensifies beyond mortal comprehension..."
            ),
            color=0x9400D3
        )
        await self.send_to_channels(embed=em, raid=raid)

    # ----------------------------------------------------------
    #       SYNERGY, GROUP SANITY, OBJECTIVE
    # ----------------------------------------------------------
    async def check_synergy_event(self, raid: Raid):
        if raid.synergy_triggered:
            return
        vt_players = [p for p, d in raid.participants.items() if d["void_touched"]]
        if len(vt_players) >= 2:
            raid.synergy_triggered = True
            synergy_em = discord.Embed(
                title="VOID SYNERGY",
                description=(
                    "Multiple void-touched souls resonate in a twisted union!\n"
                    "Eclipse reels from the surging energies..."
                ),
                color=0x8B00FF
            )
            await self.send_to_channels(embed=synergy_em, raid=raid)

            synergy_damage = random.randint(500, 1000)
            raid.boss_hp -= synergy_damage
            await self.clamp_boss_hp(raid)
            for p, d in raid.participants.items():
                d["madness_points"] += 2

            synergy_em2 = discord.Embed(
                title="RIFT SURGE",
                description=(
                    f"**{synergy_damage}** damage dealt to Eclipse!\n"
                    "All participants gain **+2** Madness Points.\n"
                    "Will this chaotic power be your salvation or doom?"
                ),
                color=0x8B00FF
            )
            await self.send_to_channels(embed=synergy_em2, raid=raid)

    async def check_group_sanity(self, raid: Raid):
        if raid.group_sanity <= 300:
            illusions_em = discord.Embed(
                title="MASS ILLUSIONS",
                description=(
                    "Eclipse's dread presence shatters the group's collective resolve.\n"
                    "Nightmarish visions swirl around you..."
                ),
                color=0x660066
            )
            await self.send_to_channels(embed=illusions_em, raid=raid)

            for player, data in list(raid.participants.items()):
                guard_active = data.pop("guard_active", False)
                s_loss = random.randint(5, 10)
                hp_loss = random.randint(0, 5)
                if guard_active:
                    s_loss //= 2
                    hp_loss //= 2
                data["sanity"] -= s_loss
                data["hp"] -= hp_loss
                await self.clamp_player_hp(data)
                data["madness_points"] += 1
                await self.check_player_state(player, raid)

    async def announce_objective(self, raid: Raid):
        if not raid.current_objective:
            return
        obj = raid.current_objective
        em = discord.Embed(
            title=f"MID-RAID OBJECTIVE: {obj['name']}",
            description=(
                f"{obj['description']}\n\n"
                "You have **5 minutes** to address this threat!"
            ),
            color=0xFFFF00
        )
        await self.send_to_channels(embed=em, raid=raid)

    async def check_objective_deadline(self, raid: Raid):
        if not raid.current_objective or raid.current_objective["completed"]:
            return
        now = datetime.datetime.utcnow()
        if now >= raid.objective_deadline:
            penalty = random.randint(300, 500)
            raid.boss_hp += penalty
            await self.clamp_boss_hp(raid)
            raid.current_objective["completed"] = True

            em = discord.Embed(
                title=f"OBJECTIVE FAILED: {raid.current_objective['name']}",
                description=(
                    "Time has lapsed, and Eclipse draws strength from your inaction...\n"
                ),
                color=0xFF0000
            )
            em.add_field(name="Penalty", value=f"Eclipse regains {penalty} HP!")
            await self.send_to_channels(embed=em, raid=raid)

    # --------------------------------------------
    #  CRITICAL FIX: Next turn after completing objective
    # --------------------------------------------
    async def player_complete_objective(self, player: discord.User, raid: Raid):
        """
        We call this from the objective callback. Then we continue to the next turn.
        """
        await self.complete_objective(player, raid)
        # After completing the objective, we must check player state & move on:
        await self.check_player_state(player, raid)
        await self.end_player_turn(raid)

    async def complete_objective(self, player: discord.User, raid: Raid):
        """Actually finish the objective. Then rely on the code above to do end turn."""
        if not raid.current_objective or raid.current_objective["completed"]:
            return
        raid.current_objective["completed"] = True
        reward = raid.current_objective["reward"]
        raid.boss_hp = max(0, raid.boss_hp - reward)

        em = discord.Embed(
            title="OBJECTIVE COMPLETED",
            description=(
                f"{player.mention} neutralizes the threat!\n"
                f"Eclipse howls in fury, losing **{reward}** HP..."
            ),
            color=0x00FF00
        )
        await self.send_to_channels(embed=em, raid=raid)
        raid.current_objective = None

    # ----------------------------------------------------------
    #         TIMEOUT, INSANITY, DEATH, DEFEAT, VICTORY
    # ----------------------------------------------------------
    async def handle_timeout(self, player: discord.User, raid: Raid):
        data = raid.participants[player]
        guard_active = data.pop("guard_active", False)
        s_loss = 20
        hp_loss = 30
        if guard_active:
            s_loss //= 2
            hp_loss //= 2

        data["sanity"] -= s_loss
        data["hp"] -= hp_loss
        await self.clamp_player_hp(data)
        data["madness_points"] += random.randint(2, 4)

        boss_gain = random.randint(150, 400)
        raid.boss_hp += boss_gain
        await self.clamp_boss_hp(raid)
        raid.group_sanity -= 30

        em = discord.Embed(
            title="PARALYZED BY TERROR",
            description=(
                f"{player.mention} hesitates and suffers grave consequences!\n"
                "```diff\n"
                f"- HP -{hp_loss}\n"
                f"- Sanity -{s_loss}\n"
                f"- Group Sanity -30\n"
                f"+ Eclipse regains {boss_gain} HP\n"
                "```"
            ),
            color=0xFF0000
        )
        await self.send_to_channels(embed=em, raid=raid)
        await self.check_player_state(player, raid)
        await self.end_player_turn(raid)

    async def check_player_state(self, player: discord.User, raid: Raid):
        if player not in raid.participants:
            return
        data = raid.participants[player]

        # Check for insanity
        if data["sanity"] <= 0:
            await self.handle_insanity(player, raid)
            return

        # Check for death
        if data["hp"] <= 0:
            await self.handle_death(player, raid)
            return

        # Check for madness transform
        if data["madness_points"] >= 10 and not data["void_touched"]:
            data["void_touched"] = True
            data["madness_points"] = 0
            data["sanity"] = max(0, data["sanity"] - 20)
            em = discord.Embed(
                title="A SOUL EMBRACES THE VOID",
                description=(
                    f"{player.mention} is consumed by cosmic whispers, becoming **Void-Touched**!\n"
                    "```diff\n- Sanity -20\n```"
                ),
                color=0x9400D3
            )
            await self.send_to_channels(embed=em, raid=raid)

        # Random curse triggers if cursed
        if data.get("cursed") and random.random() < 0.3:
            curse_effect = random.choice([
                ("Your veins writhe with black poison...", -20, "hp"),
                ("Voices claw at your psyche...", -15, "sanity"),
                ("The curse deepens ever more...", 2, "madness_points")
            ])
            if curse_effect[2] == "madness_points":
                data["madness_points"] += curse_effect[1]
            else:
                data[curse_effect[2]] = max(0, data[curse_effect[2]] + curse_effect[1])

            c_em = discord.Embed(
                title="CURSE MANIFESTATION",
                description=(
                    f"{player.mention}'s curse ignites anew!\n"
                    f"{curse_effect[0]}"
                ),
                color=0x800000
            )
            await self.send_to_channels(embed=c_em, raid=raid)

            if data["hp"] <= 0:
                await self.handle_death(player, raid)
                return
            if data["sanity"] <= 0:
                await self.handle_insanity(player, raid)
                return

        # If void_touched, random sanity drain
        if data.get("void_touched") and random.random() < 0.2:
            guard_active = data.pop("guard_active", False)
            drain = random.randint(5, 15)
            if guard_active:
                drain //= 2
            data["sanity"] = max(0, data["sanity"] - drain)
            if data["sanity"] <= 0:
                await self.handle_insanity(player, raid)
            else:
                v_em = discord.Embed(
                    title="VOID CORRUPTION",
                    description=(
                        f"{player.mention} feels the swirling darkness nibbling at their mind...\n"
                        f"```diff\n- Sanity -{drain}\n```"
                    ),
                    color=0x2F4F4F
                )
                await self.send_to_channels(embed=v_em, raid=raid)

    async def handle_insanity(self, player: discord.User, raid: Raid):
        raid.insane_players.add(player)
        raid.remove_player(player)



        gain = random.randint(200, 500)
        raid.boss_hp += gain
        await self.clamp_boss_hp(raid)
        raid.group_sanity -= 50

        em = discord.Embed(
            title="INSANITY CLAIMS A SOUL",
            description=(
                f"{player.mention} collapses into mind-shredding terror!\n"
                "```diff\n"
                f"+ Eclipse feasts: HP +{gain}\n"
                f"- Group Sanity -50\n"
                "```"
            ),
            color=0x000000
        )
        await self.send_to_channels(embed=em, raid=raid)

        # If that was the last player, we declare defeat
        if not raid.participants:
            await self.handle_defeat(raid)

    async def handle_death(self, player: discord.User, raid: Raid):
        raid.defeated_players.add(player)
        raid.remove_player(player)

        gain = random.randint(300, 600)
        raid.boss_hp += gain
        await self.clamp_boss_hp(raid)
        raid.group_sanity -= 80

        em = discord.Embed(
            title="CONSUMED BY THE VOID",
            description=(
                f"Eclipse devours the remnants of {player.mention}!\n"
                "```diff\n"
                f"+ Eclipse HP +{gain}\n"
                f"- Group Sanity -80\n"
                "```"
            ),
            color=0x8B0000
        )
        await self.send_to_channels(embed=em, raid=raid)

        # If no participants remain, defeat
        if not raid.participants:
            await self.handle_defeat(raid)

    async def handle_defeat(self, raid: Raid):
        """No participants remain or all insane. End the raid as a defeat."""
        if raid.channels[0].id not in self.active_raids:
            return

        em = discord.Embed(
            title="TOTAL PARTY KILL",
            description=(
                "Eclipse stands triumphant among the fragments of mortal minds...\n"
                "```diff\n- The void embraces all...\n```"
            ),
            color=0xFF0000
        )
        await self.send_to_channels(embed=em, raid=raid)

        await self.post_raid_summary(raid, victory=False)

        if raid.channels[0].id in self.active_raids:
            del self.active_raids[raid.channels[0].id]

    async def handle_victory(self, raid: Raid):
        """If boss HP <= 0, call this from e.g. clamp or after an action check."""
        if raid.channels[0].id not in self.active_raids:
            return

        survivors = [p for p, d in raid.participants.items() if d["hp"] > 0]
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
            reward_type = random.choices(
                list(cursed_rewards.keys()),
                weights=[r['chance'] for r in cursed_rewards.values()],
                k=1
            )[0]
            reward_data = cursed_rewards[reward_type]

            async with self.bot.pool.acquire() as conn:
                await conn.execute(
                    f'UPDATE profile SET "crates_{reward_type}"="crates_{reward_type}"+1 WHERE "user"=$1;',
                    winner.id
                )

            em = discord.Embed(
                title="ECLIPSE FALLS",
                description=(
                    f"{winner.mention} delivers the final blow...\n"
                    f"**Reward**: {reward_type}\n*{reward_data['effect']}*\n\n"
                    "```diff\n- The void shall remember your transgression...\n```"
                ),
                color=0x000000
            )
            await self.send_to_channels(embed=em, raid=raid)
            await self.post_raid_summary(raid, victory=True)
        else:
            await self.handle_defeat(raid)

        if raid.channels[0].id in self.active_raids:
            del self.active_raids[raid.channels[0].id]

    async def post_raid_summary(self, raid: Raid, victory: bool):
        if raid.final_summary_posted:
            return
        raid.final_summary_posted = True

        survivors = [p for p in raid.participants if p not in raid.defeated_players and p not in raid.insane_players]
        insane = list(raid.insane_players)
        defeated = list(raid.defeated_players)

        desc = (
            f"**Survivors ({len(survivors)})**: {', '.join([x.mention for x in survivors])}\n"
            f"**Insane ({len(insane)})**: {', '.join([x.mention for x in insane])}\n"
            f"**Fallen ({len(defeated)})**: {', '.join([x.mention for x in defeated])}\n"
        )
        result_title = "VICTORY SUMMARY" if victory else "DEFEAT SUMMARY"
        color = 0x00FF00 if victory else 0xFF0000
        em = discord.Embed(
            title=result_title,
            description=desc,
            color=color
        )
        await self.send_to_channels(embed=em, raid=raid)

    # --------------------------------------------
    #  HELPER: SHOW STATS (EPHEMERAL)
    # --------------------------------------------
    async def show_player_stats_ephemeral(self, interaction: discord.Interaction, raid: Raid):
        user = interaction.user
        if user not in raid.participants:
            await interaction.response.send_message("You are not in this raid!", ephemeral=True)
            return

        data = raid.participants[user]
        hp = data["hp"]
        sanity = data["sanity"]
        madness = data["madness_points"]
        consumed = data.get("consumable", "None")
        betray_left = data.get("betray_charges", 0)
        effects = self.get_status_effects(data)
        aggro = raid.aggro_table.get(user, 0)

        desc = (
            f"**HP**: {hp}\n"
            f"**Sanity**: {sanity}\n"
            f"**Madness**: {madness}\n"
            f"**Effects**: {effects}\n"
            f"**Consumable**: {consumed}\n"
            f"**Betray Charges**: {betray_left}\n"
            f"**Aggro**: {aggro}\n"
        )
        em = discord.Embed(
            title=f"{user.display_name}'s Status",
            description=desc,
            color=0x666666
        )
        await interaction.response.send_message(embed=em, ephemeral=True)

    @staticmethod
    def get_status_effects(p_data: dict) -> str:
        effects = []
        if p_data.get("cursed"):
            effects.append("CURSED")
        if p_data.get("void_touched"):
            effects.append("VOID-TOUCHED")
        if p_data["sanity"] < 30:
            effects.append("DERANGED")
        if p_data["sanity"] < 15:
            effects.append("BREAKING")
        if p_data["hp"] < 50:
            effects.append("DYING")
        if p_data["hp"] < 25:
            effects.append("CRITICAL")
        if p_data["madness_points"] >= 8:
            effects.append("UNSTABLE")

        return " | ".join(effects) if effects else "NORMAL"


async def setup(bot):
    await bot.add_cog(HorrorRaid(bot))
