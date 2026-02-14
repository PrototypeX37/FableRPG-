"""
The EchoesOfOlympus Discord Bot
Copyright (C) 2026 Danaelis

AGPL-3.0-or-later
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, List, Tuple, Set

import discord
from discord.ext import commands


# ============================================================
# Roles (Greekified Lykaion)
# ============================================================

class LKRole(Enum):
    POLITES = 1      # Villager
    LYKAON = 2       # Werewolf
    MANTIS = 3       # Seer (sees exact role)
    IATROS = 4       # Healer (protect)
    KYNEGOS = 5      # Hunter (death shot)

    MEDEIA = 6       # Witch-like (1 heal + 1 poison)
    KERUX = 7        # Herald (1 anonymous public proclamation)
    ARIADNE = 8      # Threadweaver (night 1 choose 2; if both wolves -> she turns wolf)


ROLE_DESCRIPTIONS: Dict[LKRole, str] = {
    LKRole.POLITES: "You are a **Polites** (Citizen). Find and execute the Lykaones.",
    LKRole.LYKAON: "You are a **Lykaon** (Wolf). Each night, wolves choose one victim.",
    LKRole.MANTIS: "You are a **Mantis**. Each night, you may divine a player’s **exact role**.",
    LKRole.IATROS: "You are an **Iatros**. Each night, protect one player (can’t protect the same twice in a row).",
    LKRole.KYNEGOS: "You are a **Kynēgos**. When you die, you may shoot one living player.",
    LKRole.MEDEIA: "You are **Medeia**. You have **1 heal** (save the night victim) and **1 poison** (kill a player) for the whole game.",
    LKRole.KERUX: "You are a **Kērux** (Herald). Once per game you may send **one anonymous public proclamation** to the town.",
    LKRole.ARIADNE: (
        "You are **Ariadnē**. On **Night 1**, choose **two players** to tie with your Thread.\n"
        "If **either** tied player dies, the **other loses their vote** the next day.\n"
        "If you accidentally tie **two wolves**, the Thread backfires and **you become a Lykaon**."
    ),
}

SIDE_WOLVES = "Wolves"
SIDE_VILLAGE = "Village"


def role_side(role: LKRole) -> str:
    return SIDE_WOLVES if role == LKRole.LYKAON else SIDE_VILLAGE


# ============================================================
# Small helpers
# ============================================================

async def ensure_dm(user: discord.Member) -> discord.DMChannel:
    if user.dm_channel is None:
        await user.create_dm()
    return user.dm_channel


async def _safe_send(channel: discord.abc.Messageable, content: str):
    try:
        await channel.send(content)
    except discord.HTTPException:
        pass


# ============================================================
# Button UI
# ============================================================

class YesNoView(discord.ui.View):
    def __init__(self, user_id: int, timeout: int, yes_label: str = "Yes", no_label: str = "No"):
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.choice: Optional[bool] = None

        self.yes.label = yes_label
        self.no.label = no_label

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    @discord.ui.button(label="Yes", style=discord.ButtonStyle.success)
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.choice = True
        for c in self.children:
            c.disabled = True
        await interaction.response.edit_message(content=f"✅ Selected: **{button.label}**", view=self)
        self.stop()

    @discord.ui.button(label="No", style=discord.ButtonStyle.danger)
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.choice = False
        for c in self.children:
            c.disabled = True
        await interaction.response.edit_message(content=f"✅ Selected: **{button.label}**", view=self)
        self.stop()


class ChoiceView(discord.ui.View):
    """
    Button chooser for up to 25 options per page (Discord limit).
    Supports paging.
    """
    def __init__(
        self,
        user_id: int,
        options: List[Tuple[str, str]],  # (label, value)
        timeout: int,
        page: int = 0,
        per_page: int = 20
    ):
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.options = options
        self.page = page
        self.per_page = per_page
        self.choice_value: Optional[str] = None
        self._build()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    def _build(self):
        self.clear_items()
        start = self.page * self.per_page
        end = start + self.per_page
        page_items = self.options[start:end]

        for label, value in page_items:
            self.add_item(_ChoiceButton(label=label, value=value))

        max_page = max(0, (len(self.options) - 1) // self.per_page)
        if max_page > 0:
            self.add_item(_PageButton("◀ Prev", -1, disabled=(self.page == 0)))
            self.add_item(_PageButton("Next ▶", +1, disabled=(self.page >= max_page)))

        self.add_item(_CancelButton())

    async def turn_page(self, interaction: discord.Interaction, delta: int):
        max_page = max(0, (len(self.options) - 1) // self.per_page)
        self.page = min(max(self.page + delta, 0), max_page)
        self._build()
        await interaction.response.edit_message(view=self)

    async def choose(self, interaction: discord.Interaction, value: str, label: str):
        self.choice_value = value
        for c in self.children:
            c.disabled = True
        await interaction.response.edit_message(content=f"✅ Chosen: **{label}**", view=self)
        self.stop()

    async def cancel(self, interaction: discord.Interaction):
        self.choice_value = None
        for c in self.children:
            c.disabled = True
        await interaction.response.edit_message(content="❌ Cancelled.", view=self)
        self.stop()


class _ChoiceButton(discord.ui.Button):
    def __init__(self, label: str, value: str):
        super().__init__(style=discord.ButtonStyle.primary, label=label)
        self.value = value

    async def callback(self, interaction: discord.Interaction):
        view: ChoiceView = self.view  # type: ignore
        await view.choose(interaction, self.value, self.label)


class _PageButton(discord.ui.Button):
    def __init__(self, label: str, delta: int, disabled: bool):
        super().__init__(style=discord.ButtonStyle.secondary, label=label, disabled=disabled)
        self.delta = delta

    async def callback(self, interaction: discord.Interaction):
        view: ChoiceView = self.view  # type: ignore
        await view.turn_page(interaction, self.delta)


class _CancelButton(discord.ui.Button):
    def __init__(self):
        super().__init__(style=discord.ButtonStyle.danger, label="Cancel")

    async def callback(self, interaction: discord.Interaction):
        view: ChoiceView = self.view  # type: ignore
        await view.cancel(interaction)


# ============================================================
# Game State
# ============================================================

@dataclass
class LKPlayer:
    member: discord.Member
    role: LKRole
    alive: bool = True

    # Iatros
    last_heal_target_id: Optional[int] = None

    # Medeia
    medeia_heal_available: bool = False
    medeia_poison_available: bool = False

    # Hermes (anonymous DM)
    hermes_available: bool = False

    # Kerux (anonymous public proclamation)
    kerux_available: bool = False

    # Ariadne thread targets
    ariadne_done: bool = False

    # vote lock next day (from Ariadne)
    vote_locked_today: bool = False


@dataclass
class LKConfig:
    timers: Dict[str, int] = field(default_factory=lambda: {
        "extended": 90,
        "normal": 60,
        "fast": 45,
        "blitz": 30,
    })
    join_seconds: int = 120
    wolf_chat_seconds: int = 60


# ============================================================
# Lobby (Join/Leave/Begin)
# ============================================================

class LobbyView(discord.ui.View):
    def __init__(self, cog: "Lykaion", channel_id: int, host_id: int, timeout: int):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.channel_id = channel_id
        self.host_id = host_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return True

    @discord.ui.button(label="Join", style=discord.ButtonStyle.success)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        lobby = self.cog.lobbies.get(self.channel_id)
        if not lobby:
            await interaction.response.send_message("No lobby found.", ephemeral=True)
            return
        if interaction.user in lobby["players"]:
            await interaction.response.send_message("You’re already in.", ephemeral=True)
            return
        lobby["players"].append(interaction.user)
        await interaction.response.edit_message(embed=self.cog._lobby_embed(lobby), view=self)

    @discord.ui.button(label="Leave", style=discord.ButtonStyle.secondary)
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        lobby = self.cog.lobbies.get(self.channel_id)
        if not lobby:
            await interaction.response.send_message("No lobby found.", ephemeral=True)
            return
        if interaction.user not in lobby["players"]:
            await interaction.response.send_message("You’re not in the lobby.", ephemeral=True)
            return
        lobby["players"].remove(interaction.user)
        await interaction.response.edit_message(embed=self.cog._lobby_embed(lobby), view=self)

    @discord.ui.button(label="Begin Now", style=discord.ButtonStyle.primary)
    async def begin(self, interaction: discord.Interaction, button: discord.ui.Button):
        lobby = self.cog.lobbies.get(self.channel_id)
        if not lobby:
            await interaction.response.send_message("No lobby found.", ephemeral=True)
            return

        is_admin = interaction.user.guild_permissions.manage_guild or interaction.user.guild_permissions.administrator
        if interaction.user.id not in (self.host_id,) and not is_admin:
            await interaction.response.send_message("Only the host/GM can begin now.", ephemeral=True)
            return

        lobby["force_begin"].set()
        await interaction.response.send_message("⏩ Beginning now…", ephemeral=True)


# ============================================================
# Lykaion Game
# ============================================================

class LykaionGame:
    def __init__(self, bot: commands.Bot, channel: discord.TextChannel, players: List[discord.Member], speed: str):
        self.bot = bot
        self.channel = channel
        self.speed = speed.lower()
        self.cfg = LKConfig()
        self.timer = self.cfg.timers.get(self.speed, 60)

        self.players: List[LKPlayer] = []
        self.day_no = 1
        self.night_no = 1

        self.ended: bool = False
        self._wolf_relay_task: Optional[asyncio.Task] = None

        # Ares: random living double vote; if dies, re-roll immediately
        self.ares_favored_id: Optional[int] = None

        # Hermes: one-time anonymous DM message
        self.hermes_holder_id: Optional[int] = None

        # Ariadne: two thread targets (ids)
        self.ariadne_thread: Optional[Tuple[int, int]] = None

        self._assign_roles(players)

    # ---------------- helpers ----------------

    def alive_players(self) -> List[LKPlayer]:
        return [p for p in self.players if p.alive]

    def get_p(self, member_id: int) -> Optional[LKPlayer]:
        for p in self.players:
            if p.member.id == member_id:
                return p
        return None

    def wolves(self) -> List[LKPlayer]:
        return [p for p in self.alive_players() if p.role == LKRole.LYKAON]

    def villagers(self) -> List[LKPlayer]:
        return [p for p in self.alive_players() if p.role != LKRole.LYKAON]

    def choose_new_ares_favored(self):
        living = self.alive_players()
        self.ares_favored_id = random.choice(living).member.id if living else None

    def winner(self) -> Optional[str]:
        alive = self.alive_players()
        if len(alive) <= 1:
            if alive:
                return f"{alive[0].member.display_name} (last alive)"
            return "No one"

        wolves_alive = any(p.role == LKRole.LYKAON for p in alive)
        vill_alive = any(p.role != LKRole.LYKAON for p in alive)

        if wolves_alive and not vill_alive:
            return "Lykaones (Wolves)"
        if vill_alive and not wolves_alive:
            return "Village"
        return None

    # ---------------- role assignment ----------------

    def _assign_roles(self, members: List[discord.Member]):
        n = len(members)
        roles: List[LKRole] = []

        # Simple scaling:
        # 3-5: 1 wolf, mantis
        # 6-8: 2 wolves, mantis, iatros
        # 9-10: 3 wolves, mantis, iatros, kynegos
        # 11+: add medeia, kerux, ariadne (one each) as possible
        if n <= 5:
            roles = [LKRole.LYKAON, LKRole.MANTIS] + [LKRole.POLITES] * (n - 2)
        elif n <= 8:
            roles = [LKRole.LYKAON, LKRole.LYKAON, LKRole.MANTIS, LKRole.IATROS] + [LKRole.POLITES] * (n - 4)
        elif n <= 10:
            roles = [LKRole.LYKAON, LKRole.LYKAON, LKRole.LYKAON, LKRole.MANTIS, LKRole.IATROS, LKRole.KYNEGOS] + \
                    [LKRole.POLITES] * (n - 6)
        else:
            roles = [
                LKRole.LYKAON, LKRole.LYKAON, LKRole.LYKAON,
                LKRole.MANTIS, LKRole.IATROS, LKRole.KYNEGOS,
                LKRole.MEDEIA, LKRole.KERUX, LKRole.ARIADNE
            ]
            if len(roles) > n:
                roles = roles[:n]
            roles += [LKRole.POLITES] * (n - len(roles))

        random.shuffle(roles)
        self.players = [LKPlayer(member=m, role=r) for m, r in zip(members, roles)]

        # Ares favor chosen randomly (double vote) + persists until death
        self.choose_new_ares_favored()

        # Hermes: assign to random villager-side (or anyone if only wolves)
        candidates = [p for p in self.alive_players() if p.role != LKRole.LYKAON] or self.alive_players()
        hermes = random.choice(candidates)
        self.hermes_holder_id = hermes.member.id
        hermes.hermes_available = True

        # Medeia resources + Kerux
        for p in self.players:
            if p.role == LKRole.MEDEIA:
                p.medeia_heal_available = True
                p.medeia_poison_available = True
            if p.role == LKRole.KERUX:
                p.kerux_available = True

    # ---------------- announcements / DMs ----------------

    async def dm_roles(self):
        for p in self.players:
            dm = await ensure_dm(p.member)
            await dm.send(
                f"🏛️ **Lykaion**\n"
                f"You are **{p.role.name.title().replace('_',' ')}**.\n\n"
                f"{ROLE_DESCRIPTIONS[p.role]}\n"
                f"Game channel: {self.channel.mention}"
            )

        wolves = self.wolves()
        if wolves:
            names = ", ".join(w.member.display_name for w in wolves)
            for w in wolves:
                dm = await ensure_dm(w.member)
                await dm.send(f"🐺 Your fellow Lykaones are: **{names}**")

    async def announce_ares(self):
        if not self.ares_favored_id:
            return
        favored = self.get_p(self.ares_favored_id)
        if favored and favored.alive:
            await self.channel.send(f"⚔️ **The Gods favored {favored.member.mention} — their vote counts double today.**")

    # ============================================================
    # Hermes (anonymous DM) & Kerux (anonymous public)
    # ============================================================

    async def hermes_phase(self):
        if not self.hermes_holder_id:
            return
        holder = self.get_p(self.hermes_holder_id)
        if not holder or not holder.alive or not holder.hermes_available:
            return

        dm = await ensure_dm(holder.member)
        view = YesNoView(holder.member.id, timeout=self.timer, yes_label="Send now", no_label="Not now")
        msg = await dm.send("📨 **Hermes’ Messenger** — Send your **anonymous DM** now?", view=view)
        await view.wait()
        try:
            await msg.edit(view=None)
        except discord.HTTPException:
            pass
        if view.choice is not True:
            return

        targets = [p for p in self.alive_players() if p.member.id != holder.member.id]
        if not targets:
            await dm.send("No valid targets.")
            return

        chooser = ChoiceView(holder.member.id, [(t.member.display_name, str(t.member.id)) for t in targets], timeout=self.timer)
        m2 = await dm.send("Choose who receives the message:", view=chooser)
        await chooser.wait()
        try:
            await m2.edit(view=None)
        except discord.HTTPException:
            pass
        if chooser.choice_value is None:
            return

        target_id = int(chooser.choice_value)
        target = self.get_p(target_id)
        if not target or not target.alive:
            await dm.send("That target is no longer valid.")
            return

        await dm.send("Type your message now (max 200 chars).")

        def check(m: discord.Message) -> bool:
            return m.author.id == holder.member.id and isinstance(m.channel, discord.DMChannel)

        try:
            reply = await self.bot.wait_for("message", check=check, timeout=self.timer)
        except asyncio.TimeoutError:
            await dm.send("⏰ Timed out. Hermes remains unused.")
            return

        text = (reply.content or "").strip()
        if not text:
            await dm.send("Empty message cancelled. Hermes remains unused.")
            return
        text = text[:200]

        tdm = await ensure_dm(target.member)
        await tdm.send(f"📨 **A message from Hermes:**\n{text}")

        holder.hermes_available = False
        await dm.send("✅ Delivered anonymously. Hermes’ Messenger is now spent.")

    async def kerux_phase(self):
        kerux = next((p for p in self.alive_players() if p.role == LKRole.KERUX and p.kerux_available), None)
        if not kerux:
            return

        dm = await ensure_dm(kerux.member)
        view = YesNoView(kerux.member.id, timeout=self.timer, yes_label="Proclaim", no_label="Hold")
        msg = await dm.send("📢 **Kērux** — Send an **anonymous public proclamation** to the town now?", view=view)
        await view.wait()
        try:
            await msg.edit(view=None)
        except discord.HTTPException:
            pass
        if view.choice is not True:
            return

        await dm.send("Type your proclamation (max 200 chars). It will be posted anonymously in the game channel.")

        def check(m: discord.Message) -> bool:
            return m.author.id == kerux.member.id and isinstance(m.channel, discord.DMChannel)

        try:
            reply = await self.bot.wait_for("message", check=check, timeout=self.timer)
        except asyncio.TimeoutError:
            await dm.send("⏰ Timed out. Kērux remains unused.")
            return

        text = (reply.content or "").strip()
        if not text:
            await dm.send("Empty proclamation cancelled. Kērux remains unused.")
            return
        text = text[:200]

        await self.channel.send(f"📢 **An anonymous proclamation echoes through Lykaion:**\n{text}")
        kerux.kerux_available = False
        await dm.send("✅ Proclamation delivered. Kērux is now spent.")

    # ============================================================
    # Ariadne (Night 1 thread)
    # ============================================================

    async def ariadne_night1_phase(self):
        ariadne = next((p for p in self.alive_players() if p.role == LKRole.ARIADNE and not p.ariadne_done), None)
        if not ariadne:
            return
        ariadne.ariadne_done = True

        dm = await ensure_dm(ariadne.member)

        # Constraint: <=10 players => Ariadne loses vote daily (nerf)
        if len(self.players) <= 10:
            await dm.send("🧵 **Ariadnē** — Because this game has **10 players or fewer**, you will **lose your vote** each day.")
        else:
            await dm.send("🧵 **Ariadnē** — Choose **two players** to tie with your Thread (Night 1 only).")

        targets = [p for p in self.alive_players() if p.member.id != ariadne.member.id]
        if len(targets) < 2:
            return

        chooser1 = ChoiceView(ariadne.member.id, [(t.member.display_name, str(t.member.id)) for t in targets], timeout=self.timer)
        m1 = await dm.send("Pick the **first** thread target:", view=chooser1)
        await chooser1.wait()
        try:
            await m1.edit(view=None)
        except discord.HTTPException:
            pass
        if chooser1.choice_value is None:
            await dm.send("⏰ No thread set. (AFK / timed out)")
            return
        t1 = int(chooser1.choice_value)

        targets2 = [p for p in targets if p.member.id != t1]
        chooser2 = ChoiceView(ariadne.member.id, [(t.member.display_name, str(t.member.id)) for t in targets2], timeout=self.timer)
        m2 = await dm.send("Pick the **second** thread target:", view=chooser2)
        await chooser2.wait()
        try:
            await m2.edit(view=None)
        except discord.HTTPException:
            pass
        if chooser2.choice_value is None:
            await dm.send("⏰ Second pick cancelled. No thread set.")
            return
        t2 = int(chooser2.choice_value)

        self.ariadne_thread = (t1, t2)

        p1 = self.get_p(t1)
        p2 = self.get_p(t2)
        if not p1 or not p2:
            return

        # Backfire rule: if she picked two wolves, Ariadne becomes a wolf
        if p1.role == LKRole.LYKAON and p2.role == LKRole.LYKAON:
            ariadne.role = LKRole.LYKAON
            await dm.send("🩸 The Thread snaps in the Labyrinth… You tied **two Lykaones**. The curse turns **you** into a **Lykaon**.")
            wolves = self.wolves()
            names = ", ".join(w.member.display_name for w in wolves)
            for w in wolves:
                wdm = await ensure_dm(w.member)
                await wdm.send(f"🐺 The pack grows. Fellow Lykaones: **{names}**")
        else:
            await dm.send(
                f"🧵 Your Thread is tied.\n"
                f"If **{p1.member.display_name}** dies, **{p2.member.display_name}** will lose their vote the next day.\n"
                f"If **{p2.member.display_name}** dies, **{p1.member.display_name}** will lose their vote the next day."
            )

    def _apply_ariadne_vote_lock_from_death(self, dead_id: int):
        if not self.ariadne_thread:
            return
        a, b = self.ariadne_thread
        if dead_id == a:
            other = self.get_p(b)
            if other and other.alive:
                other.vote_locked_today = True
        elif dead_id == b:
            other = self.get_p(a)
            if other and other.alive:
                other.vote_locked_today = True

    # ============================================================
    # Night actions
    # ============================================================

    async def night_phase(self) -> Optional[int]:
        await self.channel.send("🌘 **Night falls over Lykaion...**")

        # Public progress, without leaking targets
        did_mantis = False
        did_iatros = False
        did_wolves = False
        did_medeia_heal = False
        did_medeia_poison = False

        # Night 1: Ariadne thread
        if self.night_no == 1:
            await _safe_send(self.channel, "🧵 **Ariadnē weaves the Thread...**")
            await self.ariadne_night1_phase()
            await _safe_send(self.channel, "🧵 **The Thread is set.**")

        # Mantis
        mantis = next((p for p in self.alive_players() if p.role == LKRole.MANTIS), None)
        if mantis:
            await _safe_send(self.channel, "👁️ **Mantis is divining...**")
            did_mantis = await self._mantis_action(mantis)
            await _safe_send(self.channel, "👁️ **Mantis has finished.**")

        # Iatros
        healer = next((p for p in self.alive_players() if p.role == LKRole.IATROS), None)
        healed_id: Optional[int] = None
        if healer:
            await _safe_send(self.channel, "🧪 **Iatros prepares a protection...**")
            healed_id = await self._iatros_action(healer)
            did_iatros = healed_id is not None
            await _safe_send(self.channel, "🧪 **Iatros has finished.**")

        # Wolf council: relay + vote
        await _safe_send(self.channel, "🐺 **Lykaones convene in the dark...**")
        victim_id = await self._wolves_council_and_kill()
        did_wolves = True

        # Medeia reacts after wolves pick (heal victim / poison someone)
        if any(p.role == LKRole.MEDEIA for p in self.alive_players()):
            await _safe_send(self.channel, "⚗️ **Medeia considers fate and venom...**")
        victim_id, extra_dead, used_heal, used_poison = await self._medeia_actions(victim_id)
        did_medeia_heal = used_heal
        did_medeia_poison = used_poison

        # Apply Iatros protection
        if victim_id is not None and healed_id is not None and victim_id == healed_id:
            await self.channel.send("🧪 **A protection held in the dark. No one died to the wolves.**")
            victim_id = None

        # Execute poison kill immediately (if any)
        if extra_dead is not None:
            await self.kill_player(extra_dead, reason="was undone by Medeia’s poison")

        # Public night summary (no targets)
        summary_bits: List[str] = []
        if did_mantis:
            summary_bits.append("👁️ Mantis acted")
        if did_iatros:
            summary_bits.append("🧪 Iatros acted")
        if did_wolves:
            summary_bits.append("🐺 Lykaones acted")
        if did_medeia_heal:
            summary_bits.append("⚗️ Medeia healed")
        if did_medeia_poison:
            summary_bits.append("☠️ Medeia poisoned")
        if summary_bits:
            await _safe_send(self.channel, "🌙 **Night actions resolved:** " + " • ".join(summary_bits))

        return victim_id

    async def _mantis_action(self, mantis: LKPlayer) -> bool:
        dm = await ensure_dm(mantis.member)
        targets = [p for p in self.alive_players() if p.member.id != mantis.member.id]
        if not targets:
            return False

        chooser = ChoiceView(mantis.member.id, [(t.member.display_name, str(t.member.id)) for t in targets], timeout=self.timer)
        msg = await dm.send("👁️ **Mantis** — Choose someone to divine:", view=chooser)
        await chooser.wait()
        try:
            await msg.edit(view=None)
        except discord.HTTPException:
            pass

        if chooser.choice_value is None:
            await dm.send("⏰ No choice made. (AFK / timed out)")
            return False

        tid = int(chooser.choice_value)
        t = self.get_p(tid)
        if not t or not t.alive:
            await dm.send("Target invalid.")
            return False

        await dm.send(f"🔎 **{t.member.display_name}** is: **{t.role.name.title().replace('_',' ')}**")
        return True

    async def _iatros_action(self, healer: LKPlayer) -> Optional[int]:
        dm = await ensure_dm(healer.member)
        targets = [p for p in self.alive_players() if p.member.id != healer.last_heal_target_id]
        if not targets:
            return None

        chooser = ChoiceView(healer.member.id, [(t.member.display_name, str(t.member.id)) for t in targets], timeout=self.timer)
        msg = await dm.send("🧪 **Iatros** — Choose someone to protect tonight:", view=chooser)
        await chooser.wait()
        try:
            await msg.edit(view=None)
        except discord.HTTPException:
            pass

        if chooser.choice_value is None:
            await dm.send("⏰ No protection chosen. (AFK / timed out)")
            return None

        tid = int(chooser.choice_value)
        t = self.get_p(tid)
        if not t or not t.alive:
            await dm.send("Target invalid.")
            return None

        healer.last_heal_target_id = tid

        # Keep it only once (no extra spam elsewhere)
        await dm.send(f"✅ You protect **{t.member.display_name}** tonight.")
        return tid

    # ---------------- Wolves council: relay + vote ----------------

    async def _wolves_relay(self, wolves: List[LKPlayer], seconds: int):
        """
        For `seconds`, relay DM messages between wolves (like a temporary pack-chat).
        """
        wolf_ids = {w.member.id for w in wolves}

        def check(m: discord.Message) -> bool:
            return (
                m.author.id in wolf_ids
                and isinstance(m.channel, discord.DMChannel)
                and (m.content is not None)
                and (not m.author.bot)
            )

        end_at = asyncio.get_running_loop().time() + seconds
        while asyncio.get_running_loop().time() < end_at:
            timeout = max(0.0, end_at - asyncio.get_running_loop().time())
            try:
                msg = await self.bot.wait_for("message", check=check, timeout=timeout)
            except asyncio.TimeoutError:
                break

            for w in wolves:
                if w.member.id == msg.author.id:
                    continue
                try:
                    dm = await ensure_dm(w.member)
                    await dm.send(f"🐺 **{msg.author.display_name}**: {msg.content[:500]}")
                except discord.HTTPException:
                    pass

    async def _wolves_council_and_kill(self) -> Optional[int]:
        wolves = self.wolves()
        if not wolves:
            return None

        names = ", ".join(w.member.display_name for w in wolves)
        for w in wolves:
            dm = await ensure_dm(w.member)
            await dm.send(
                f"🐺 **Wolf Council**\n"
                f"Fellow Lykaones: **{names}**\n"
                f"You have **{self.cfg.wolf_chat_seconds}s** to discuss. Any DM you send me now will be relayed to the pack."
            )

        # Relay phase
        try:
            self._wolf_relay_task = asyncio.create_task(self._wolves_relay(wolves, self.cfg.wolf_chat_seconds))
            await asyncio.sleep(self.cfg.wolf_chat_seconds)
        finally:
            if self._wolf_relay_task:
                self._wolf_relay_task.cancel()
                self._wolf_relay_task = None

        # Vote
        targets = [p for p in self.alive_players() if p.role != LKRole.LYKAON]
        if not targets:
            return None

        votes: Dict[int, int] = {}
        afk_wolves: List[str] = []

        async def wolf_vote(w: LKPlayer):
            dm = await ensure_dm(w.member)
            chooser = ChoiceView(w.member.id, [(t.member.display_name, str(t.member.id)) for t in targets], timeout=self.timer)
            msg = await dm.send("🐺 **Time to choose the prey.** Vote for tonight’s victim:", view=chooser)
            await chooser.wait()
            try:
                await msg.edit(view=None)
            except discord.HTTPException:
                pass

            if chooser.choice_value is None:
                afk_wolves.append(w.member.display_name)
                await dm.send("⏰ No vote cast. (AFK / timed out)")
                return

            tid = int(chooser.choice_value)
            votes[tid] = votes.get(tid, 0) + 1

        await asyncio.gather(*(wolf_vote(w) for w in wolves))

        if afk_wolves:
            for w in wolves:
                try:
                    dm = await ensure_dm(w.member)
                    await dm.send("😴 **AFK / no vote from:** " + ", ".join(afk_wolves))
                except discord.HTTPException:
                    pass

        if not votes:
            return None

        sorted_targets = sorted(votes.items(), key=lambda kv: kv[1], reverse=True)
        top_id, top_votes = sorted_targets[0]
        if len(sorted_targets) > 1 and sorted_targets[1][1] == top_votes:
            for w in wolves:
                dm = await ensure_dm(w.member)
                await dm.send("⚖️ The pack is split. No victim chosen.")
            return None

        victim = self.get_p(top_id)
        if victim and victim.alive:
            for w in wolves:
                dm = await ensure_dm(w.member)
                await dm.send(f"🩸 The pack chose **{victim.member.display_name}**.")
        return top_id

    # ---------------- Medeia actions ----------------

    async def _medeia_actions(self, victim_id: Optional[int]) -> Tuple[Optional[int], Optional[int], bool, bool]:
        """
        Returns (final_wolf_victim_id, extra_poison_dead_id, used_heal, used_poison)
        """
        medeia = next((p for p in self.alive_players() if p.role == LKRole.MEDEIA), None)
        if not medeia:
            return victim_id, None, False, False

        dm = await ensure_dm(medeia.member)
        used_heal = False
        used_poison = False

        # Heal option first
        if victim_id is not None and medeia.medeia_heal_available:
            victim = self.get_p(victim_id)
            if victim and victim.alive:
                view = YesNoView(medeia.member.id, timeout=self.timer, yes_label="Heal", no_label="Do not heal")
                msg = await dm.send(
                    f"🧪 **Medeia** — Wolves targeted **{victim.member.display_name}**. Use your **one-time heal**?",
                    view=view
                )
                await view.wait()
                try:
                    await msg.edit(view=None)
                except discord.HTTPException:
                    pass
                if view.choice is True:
                    medeia.medeia_heal_available = False
                    used_heal = True
                    await dm.send("✅ You used your heal. The victim will survive the wolf attack.")
                    victim_id = None

        poison_dead: Optional[int] = None
        if medeia.medeia_poison_available:
            view = YesNoView(medeia.member.id, timeout=self.timer, yes_label="Poison", no_label="Save it")
            msg = await dm.send("☠️ **Medeia** — Do you want to use your **one-time poison** tonight?", view=view)
            await view.wait()
            try:
                await msg.edit(view=None)
            except discord.HTTPException:
                pass
            if view.choice is True:
                targets = [p for p in self.alive_players() if p.member.id != medeia.member.id]
                chooser = ChoiceView(medeia.member.id, [(t.member.display_name, str(t.member.id)) for t in targets], timeout=self.timer)
                m2 = await dm.send("Choose who to poison:", view=chooser)
                await chooser.wait()
                try:
                    await m2.edit(view=None)
                except discord.HTTPException:
                    pass
                if chooser.choice_value is not None:
                    poison_dead = int(chooser.choice_value)
                    medeia.medeia_poison_available = False
                    used_poison = True
                    t = self.get_p(poison_dead)
                    if t:
                        await dm.send(f"☠️ You poisoned **{t.member.display_name}**.")
                else:
                    await dm.send("Poison cancelled. You keep it for later.")

        return victim_id, poison_dead, used_heal, used_poison

    # ============================================================
    # Day phase: PUBLIC vote by messages (mention-only + ack)
    # ============================================================

    async def day_phase(self, night_victim_id: Optional[int]):
        await self.channel.send(f"🌤️ **Day {self.day_no}**")

        # Ariadne nerf (<=10) applied each day
        ariadne = next((p for p in self.alive_players() if p.role == LKRole.ARIADNE), None)
        if ariadne and len(self.players) <= 10:
            ariadne.vote_locked_today = True

        # Resolve night kill
        if night_victim_id is not None:
            await self.kill_player(night_victim_id, reason="was slain in the night")
        else:
            await self.channel.send("🌅 Dawn breaks… **No bodies are found.**")

        if self.winner() or self.ended:
            return

        # First morning: tell wolves again who wolves are
        if self.day_no == 1 and self.wolves():
            wolves = self.wolves()
            names = ", ".join(w.member.display_name for w in wolves)
            for w in wolves:
                dm = await ensure_dm(w.member)
                await dm.send(f"🐺 **First Morning Reminder** — Fellow Lykaones: **{names}**")

        # Hermes + Kerux prompts before public vote
        await self.hermes_phase()
        await self.kerux_phase()

        # Ares announce
        await self.announce_ares()

        # Public vote instructions
        await self.channel.send(
            "🗳️ **Morning Vote**\n"
            f"Alive players: {', '.join(p.member.mention for p in self.alive_players())}\n\n"
            "**To vote:** send a message that **mentions (@)** the person you suspect.\n"
            "Type **skip** to abstain.\n"
            f"⏳ You have **{self.timer}s**.\n\n"
            "(Only your **first valid** vote counts.)"
        )

        lynch_id = await self.public_day_vote()
        if lynch_id is not None:
            await self.kill_player(lynch_id, reason="was executed by the Polis")
        else:
            await self.channel.send("⚖️ The Polis couldn’t decide. No one is executed today.")

        # clear “vote_locked_today” flags after day ends
        for p in self.players:
            p.vote_locked_today = False

        self.day_no += 1

    async def public_day_vote(self) -> Optional[int]:
        alive = self.alive_players()
        if len(alive) < 2:
            return None

        alive_ids: Set[int] = {p.member.id for p in alive}

        votes: Dict[int, int] = {}  # target_id -> weighted votes
        voters_seen: Set[int] = set()

        async def collect():
            def check(m: discord.Message) -> bool:
                if m.channel.id != self.channel.id:
                    return False
                if m.author.bot:
                    return False
                if m.author.id not in alive_ids:
                    return False
                if m.author.id in voters_seen:
                    return False

                c = (m.content or "").strip().lower()
                # STRICT: only accept either `skip` or a message containing at least one mention
                return c == "skip" or bool(m.mentions)

            end_at = asyncio.get_running_loop().time() + self.timer
            while asyncio.get_running_loop().time() < end_at:
                timeout = max(0.0, end_at - asyncio.get_running_loop().time())
                try:
                    msg = await self.bot.wait_for("message", check=check, timeout=timeout)
                except asyncio.TimeoutError:
                    break

                voter_id = msg.author.id
                voter_p = self.get_p(voter_id)

                content = (msg.content or "").strip().lower()

                # STRICT: any `skip` consumes the vote immediately (no retry)
                if content == "skip":
                    voters_seen.add(voter_id)
                    await self.channel.send(f"🕊️ {msg.author.mention} abstains.")
                    continue

                # Vote locked by Ariadne effect (also consumes their attempt in strict mode)
                if voter_p and voter_p.vote_locked_today:
                    voters_seen.add(voter_id)
                    await self.channel.send(f"🧵 {msg.author.mention} tries to vote… but the Thread steals their voice today.")
                    continue

                # STRICT: first message with a mention consumes the vote (no retries).
                voters_seen.add(voter_id)

                if not msg.mentions:
                    # Shouldn't happen because check() requires mention or skip, but keep it safe.
                    await self.channel.send(f"❓ {msg.author.mention} your vote must include a **mention (@player)** or be `skip`.")
                    continue

                target = msg.mentions[0]
                if target.id not in alive_ids:
                    await self.channel.send(
                        f"❌ {msg.author.mention} voted for an invalid target. (You must mention a **living** player.)"
                    )
                    continue

                # Ares weight
                weight = 2 if self.ares_favored_id == voter_id else 1
                votes[target.id] = votes.get(target.id, 0) + weight

                # Acknowledgement so everyone sees the vote happened
                await self.channel.send(f"🗳️ {msg.author.mention} votes for <@{target.id}>.")

        await collect()

        # AFK list: anyone alive who didn't vote and wasn't vote-locked
        nonvoters = [p.member.mention for p in alive if p.member.id not in voters_seen and not p.vote_locked_today]
        if nonvoters:
            await self.channel.send("😴 **AFK / no vote received from:** " + ", ".join(nonvoters))

        if not votes:
            return None

        sorted_targets = sorted(votes.items(), key=lambda kv: kv[1], reverse=True)
        top_id, top_votes = sorted_targets[0]
        if len(sorted_targets) > 1 and sorted_targets[1][1] == top_votes:
            await self.channel.send("⚖️ **Tie vote.** No execution.")
            return None

        target_p = self.get_p(top_id)
        if not target_p or not target_p.alive:
            return None

        await self.channel.send(f"🪓 The Polis condemns **{target_p.member.display_name}** with **{top_votes}** vote(s).")
        return top_id


    # ============================================================
    # Death handling
    # ============================================================

    async def kill_player(self, member_id: int, reason: str):
        p = self.get_p(member_id)
        if not p or not p.alive:
            return

        p.alive = False

        # Ariadne vote-lock effect from death
        self._apply_ariadne_vote_lock_from_death(member_id)

        await self.channel.send(f"💀 **{p.member.display_name}** {reason}. They were **{p.role.name.title().replace('_',' ')}**.")

        # Ares: if favored dies, shift immediately
        if self.ares_favored_id == member_id:
            self.choose_new_ares_favored()
            if self.ares_favored_id:
                newfav = self.get_p(self.ares_favored_id)
                if newfav and newfav.alive:
                    await self.channel.send(
                        f"⚔️ **The Gods’ favor shifts to {newfav.member.mention}.** (double vote now belongs to them)"
                    )

        # Hermes: if holder dies, Hermes is lost
        if self.hermes_holder_id == member_id:
            self.hermes_holder_id = None

        # Hunter shot
        if p.role == LKRole.KYNEGOS and self.winner() is None and not self.ended:
            await self.hunter_shot(p)

    async def hunter_shot(self, hunter: LKPlayer):
        dm = await ensure_dm(hunter.member)
        targets = [p for p in self.alive_players() if p.member.id != hunter.member.id]
        if not targets:
            return

        chooser = ChoiceView(hunter.member.id, [(t.member.display_name, str(t.member.id)) for t in targets], timeout=self.timer)
        msg = await dm.send("🏹 **Kynēgos** — As you fall, choose **one** to take with you:", view=chooser)
        await chooser.wait()
        try:
            await msg.edit(view=None)
        except discord.HTTPException:
            pass

        if chooser.choice_value is None:
            await dm.send("⏰ No shot taken. (AFK / timed out)")
            return

        tid = int(chooser.choice_value)
        t = self.get_p(tid)
        if not t or not t.alive:
            await dm.send("Target invalid.")
            return

        await self.channel.send(f"🏹 **Kynēgos’ last arrow strikes {t.member.mention}!**")
        await self.kill_player(tid, reason="was slain by Kynēgos’ last arrow")

    # ============================================================
    # Run loop
    # ============================================================

    async def run(self):
        await self.channel.send(
            f"🏛️ **Lykaion begins!** Speed: **{self.speed}** (timer: {self.timer}s)\n"
            f"Players: {', '.join(p.member.mention for p in self.players)}"
        )
        await self.dm_roles()
        await self.announce_ares()

        victim = await self.night_phase()

        while (self.winner() is None) and (not self.ended):
            await self.day_phase(victim)
            if self.winner() is not None or self.ended:
                break

            # IMPORTANT: increment night counter BEFORE next night runs
            self.night_no += 1
            victim = await self.night_phase()

        win = self.winner() or "No one"
        await self.channel.send(f"🏆 **Game over! Winner: {win}**")

# ============================================================
# Cog
# ============================================================

class Lykaion(commands.Cog):
    """
    Greekified Werewolf mini-game: Lykaion
    - Join/Leave/Begin lobby with buttons (2 minutes auto-start)
    - Night actions in DMs using buttons
    - Day vote is PUBLIC in the game channel (**mention-only**, or `skip`)
      and the bot acknowledges votes in-channel.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.active_games: Dict[int, Dict[str, object]] = {}  # channel_id -> {"game": LykaionGame, "task": asyncio.Task}
        self.lobbies: Dict[int, Dict[str, object]] = {}       # channel_id -> lobby dict

    def _is_gm(self, member: discord.Member) -> bool:
        return member.guild_permissions.manage_guild or member.guild_permissions.administrator

    # ---------------- embeds / help ----------------

    def _lobby_embed(self, lobby: Dict[str, object]) -> discord.Embed:
        speed = str(lobby["speed"])
        players: List[discord.Member] = lobby["players"]  # type: ignore
        remaining = int(lobby["remaining"])  # type: ignore
        host: discord.Member = lobby["host"]  # type: ignore

        e = discord.Embed(title="🏛️ Lykaion Lobby", description="Join with buttons. Auto-starts when the timer ends.")
        e.add_field(name="Host", value=host.mention, inline=True)
        e.add_field(name="Speed", value=speed, inline=True)
        e.add_field(name="Starts in", value=f"{remaining}s", inline=True)
        if players:
            e.add_field(name=f"Players ({len(players)})", value=", ".join(p.mention for p in players), inline=False)
        else:
            e.add_field(name="Players (0)", value="No one yet.", inline=False)
        e.set_footer(text="Minimum 3 players. Host/GM can Begin Now.")
        return e

    @commands.group(name="lykaion", invoke_without_command=True)
    async def lykaion(self, ctx: commands.Context):
        await self.lykaion_help(ctx)

    @lykaion.command(name="help")
    async def lykaion_help(self, ctx: commands.Context):
        p = ctx.clean_prefix
        await ctx.send(
            "**🏛️ Lykaion Help**\n\n"
            f"**Start a lobby:** `{p}lykaion start [extended|normal|fast|blitz]`\n"
            "• A 2-minute lobby appears with **Join / Leave / Begin Now** buttons.\n"
            "• Auto-starts after 2 minutes.\n\n"
            f"**Stop a game (GM):** `{p}lykaion stop`\n"
            f"**Roles list:** `{p}lkroles`\n\n"
            "**Modes / Speeds**\n"
            "• `extended` (90s timers) — slower, more talk\n"
            "• `normal` (60s) — default\n"
            "• `fast` (45s) — quick games\n"
            "• `blitz` (30s) — chaos\n\n"
            "**Greek Powers**\n"
            "⚔️ **Ares’ Favor** — a random living player gets **double vote** each day. If they die, it **shifts instantly**.\n"
            "📨 **Hermes’ Messenger** — one random non-wolf (usually) can send **one anonymous DM** to a living player.\n"
            "📢 **Kērux** — once per game, send **one anonymous public proclamation** in the channel.\n"
            "🧵 **Ariadnē** — Night 1 ties two players; if one dies, the other **loses their vote next day**. "
            "If she ties **two wolves**, she becomes a wolf.\n\n"
            "**Voting (Public)**\n"
            "Day vote happens in the **game channel**.\n"
            "✅ Valid vote = a message containing a **mention (@player)**\n"
            "🕊️ Or type `skip` to abstain.\n"
            "Only your **first** valid vote counts, and the bot will announce: `X votes for Y`."
        )

    @commands.command(name="lkroles")
    async def lkroles(self, ctx: commands.Context):
        lines = ["**🏛️ Lykaion Roles**"]
        for r in LKRole:
            lines.append(f"**{r.name.title().replace('_',' ')}** — {ROLE_DESCRIPTIONS[r]}")
        await ctx.send("\n".join(lines))

    # ---------------- start / lobby ----------------

    @lykaion.command(name="start")
    async def lykaion_start(self, ctx: commands.Context, speed: str = "normal"):
        if not isinstance(ctx.channel, discord.TextChannel):
            await ctx.send("Start Lykaion in a server text channel.")
            return
        if ctx.channel.id in self.active_games:
            await ctx.send("A Lykaion game is already running in this channel.")
            return
        if ctx.channel.id in self.lobbies:
            await ctx.send("A lobby is already open in this channel.")
            return

        speed = (speed or "normal").lower()
        cfg = LKConfig()
        if speed not in cfg.timers:
            await ctx.send("Unknown speed. Use: extended | normal | fast | blitz")
            return

        force_begin = asyncio.Event()
        lobby: Dict[str, object] = {
            "host": ctx.author,
            "speed": speed,
            "players": [],
            "force_begin": force_begin,
            "remaining": cfg.join_seconds,
            "message": None,
        }
        self.lobbies[ctx.channel.id] = lobby

        view = LobbyView(self, ctx.channel.id, ctx.author.id, timeout=cfg.join_seconds)
        msg = await ctx.send(embed=self._lobby_embed(lobby), view=view)
        lobby["message"] = msg

        # countdown loop (updates embed)
        try:
            end_at = asyncio.get_running_loop().time() + cfg.join_seconds
            while True:
                if force_begin.is_set():
                    break
                remaining = int(max(0, end_at - asyncio.get_running_loop().time()))
                lobby["remaining"] = remaining
                try:
                    await msg.edit(embed=self._lobby_embed(lobby), view=view)
                except discord.HTTPException:
                    pass
                if remaining <= 0:
                    break
                await asyncio.sleep(5)
        finally:
            for c in view.children:
                c.disabled = True
            try:
                await msg.edit(view=view)
            except discord.HTTPException:
                pass

        players: List[discord.Member] = lobby["players"]  # type: ignore
        if len(players) < 3:
            self.lobbies.pop(ctx.channel.id, None)
            await ctx.send("Lobby ended: need at least **3** players to start.")
            return

        self.lobbies.pop(ctx.channel.id, None)

        game = LykaionGame(self.bot, ctx.channel, players, speed=speed)
        task = asyncio.create_task(game.run())
        self.active_games[ctx.channel.id] = {"game": game, "task": task}

        def _done(_):
            self.active_games.pop(ctx.channel.id, None)

        task.add_done_callback(_done)

    @lykaion.command(name="stop")
    async def lykaion_stop(self, ctx: commands.Context):
        if not isinstance(ctx.channel, discord.TextChannel):
            await ctx.send("Stop Lykaion in a server text channel.")
            return

        # stop lobby
        if ctx.channel.id in self.lobbies:
            if not self._is_gm(ctx.author):
                await ctx.send("Only a GM/admin can stop the lobby.")
                return
            lobby = self.lobbies.pop(ctx.channel.id, None)
            if lobby and lobby.get("force_begin"):
                lobby["force_begin"].set()
            await ctx.send("🛑 Lobby cancelled by GM.")
            return

        # stop game
        ag = self.active_games.get(ctx.channel.id)
        if not ag:
            await ctx.send("No Lykaion game is running in this channel.")
            return
        if not self._is_gm(ctx.author):
            await ctx.send("Only a GM/admin can stop the game.")
            return

        game: LykaionGame = ag["game"]  # type: ignore
        task: asyncio.Task = ag["task"]  # type: ignore

        game.ended = True
        task.cancel()
        self.active_games.pop(ctx.channel.id, None)
        await ctx.send("🛑 Lykaion stopped by GM.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Lykaion(bot))
