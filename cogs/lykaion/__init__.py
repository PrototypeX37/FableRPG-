"""
The EchoesOfOlympus Discord Bot
Copyright (C) 2026 Danaelis

AGPL-3.0-or-later
"""
from __future__ import annotations

import asyncio
import json
import random
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, List, Tuple, Set

import discord
from discord.ext import commands

from utils.checks import user_is_gm


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
    EROS = 9         # Cupid-like (night 1 links two lovers)
    KATARATOS = 10   # Cursed (if wolves target them, they turn wolf)
    APATE = 11       # Deceiver (wolf-side, appears as citizen to Mantis)
    MOIRAI = 12      # Fateweaver (day role swap, one-time)
    HOPLITES = 13    # Bodyguard (intercepts wolf attack)
    NOMOPHYLAX = 14  # Jailer (jails/protects one target nightly)
    SKIA = 15        # Wolf shadow (blocks one night action)
    MAINAS = 16      # Wolf berserker (one-time extra wolf kill)
    LIMOS = 17       # Plaguebearer (infects nightly; infect-all win)
    NEMESIS = 18     # Executioner (wins if marked target is executed)
    MOROS = 19       # Doomed (wins if executed)
    DEMARCHOS = 20   # Greek mayor; double-vote role while >3 alive


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
    LKRole.EROS: (
        "You are **Eros**. On **Night 1**, bind two hearts.\n"
        "If one of the lovers dies, the other dies of grief."
    ),
    LKRole.KATARATOS: (
        "You are **Kataratos** (Cursed). If the Lykaones target you at night, you do not die - "
        "you become a **Lykaon** and join the pack."
    ),
    LKRole.APATE: (
        "You are **Apatē** (Deceiver), aligned with the Lykaones.\n"
        "You join the wolf pack. To Mantis, you appear as a **Polites**."
    ),
    LKRole.MOIRAI: (
        "You are **Moirai**. Once per game during daytime, you may swap the roles of two players "
        "without seeing their roles."
    ),
    LKRole.HOPLITES: (
        "You are **Hoplites**. Each night, choose one player to guard. "
        "If wolves attack them, you die in their place."
    ),
    LKRole.NOMOPHYLAX: (
        "You are **Nomophylax**. Each night, jail one player. A jailed player is protected from wolf attacks "
        "and cannot use their night action. You may exchange messages with them, and on rare occasions "
        "you gain a one-time prison seal that permanently suppresses a target's role power."
    ),
    LKRole.SKIA: (
        "You are **Skia** (Shadow), aligned with the Lykaones. "
        "Each night, you may silence one non-wolf player’s night power."
    ),
    LKRole.MAINAS: (
        "You are **Mainas** (Berserker), aligned with the Lykaones. "
        "Once per game at night, unleash a frenzy for an extra wolf kill."
    ),
    LKRole.LIMOS: (
        "You are **Limos** (Plague). Each night, infect one living player. "
        "If all other living players become infected, you win."
    ),
    LKRole.NEMESIS: (
        "You are **Nemesis**. You mark one player as your fate-target. "
        "If they are executed by the Polis, you win."
    ),
    LKRole.MOROS: (
        "You are **Moros**. Your destiny is the gallows - if the Polis executes you, you win."
    ),
    LKRole.DEMARCHOS: (
        "You are the **Demarchos**. During daytime voting, your vote counts as **double** "
        "while more than 3 players are alive."
    ),
}

ROLE_DISPLAY_NAMES: Dict[LKRole, str] = {
    LKRole.POLITES: "Polites (Villager)",
    LKRole.LYKAON: "Lykaon (Werewolf)",
    LKRole.MANTIS: "Mantis (Seer)",
    LKRole.IATROS: "Iatros (Healer)",
    LKRole.KYNEGOS: "Kynēgos (Hunter)",
    LKRole.MEDEIA: "Medeia (Witch)",
    LKRole.KERUX: "Kērux (Herald)",
    LKRole.ARIADNE: "Ariadnē (Threadweaver)",
    LKRole.EROS: "Eros (Cupid)",
    LKRole.KATARATOS: "Kataratos (Cursed)",
    LKRole.APATE: "Apatē (Deceiver)",
    LKRole.MOIRAI: "Moirai (Fateweaver)",
    LKRole.HOPLITES: "Hoplites (Bodyguard)",
    LKRole.NOMOPHYLAX: "Nomophylax (Jailer)",
    LKRole.SKIA: "Skia (Shadow)",
    LKRole.MAINAS: "Mainas (Berserker)",
    LKRole.LIMOS: "Limos (Plaguebearer)",
    LKRole.NEMESIS: "Nemesis (Executioner)",
    LKRole.MOROS: "Moros (Doomed)",
    LKRole.DEMARCHOS: "Demarchos (Mayor)",
}

SIDE_WOLVES = "Wolves"
SIDE_VILLAGE = "Village"
SIDE_NEUTRAL = "Neutral"
LYKAION_STATS_FILE = Path(__file__).with_name("lykaion_stats.json")


def role_display_name(role: LKRole) -> str:
    return ROLE_DISPLAY_NAMES.get(role, role.name.title().replace("_", " "))


def role_side(role: LKRole) -> str:
    if role in {LKRole.LYKAON, LKRole.APATE, LKRole.SKIA, LKRole.MAINAS}:
        return SIDE_WOLVES
    if role in {LKRole.LIMOS, LKRole.NEMESIS, LKRole.MOROS}:
        return SIDE_NEUTRAL
    return SIDE_VILLAGE


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


class _DayVoteTargetButton(discord.ui.Button):
    def __init__(self, label: str, value: str, style: discord.ButtonStyle):
        super().__init__(style=style, label=label[:80])
        self.value = value

    async def callback(self, interaction: discord.Interaction):
        view: DayVoteView = self.view  # type: ignore
        await view.register_vote(interaction, self.value)


class _DayVoteSelect(discord.ui.Select):
    def __init__(self, options: List[discord.SelectOption]):
        super().__init__(
            placeholder="Choose who to execute (or abstain)",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        view: DayVoteView = self.view  # type: ignore
        value = self.values[0] if self.values else "skip"
        await view.register_vote(interaction, value)


class _DayVotePageButton(discord.ui.Button):
    def __init__(self, label: str, delta: int, disabled: bool):
        super().__init__(style=discord.ButtonStyle.secondary, label=label, disabled=disabled)
        self.delta = delta

    async def callback(self, interaction: discord.Interaction):
        view: DayVoteView = self.view  # type: ignore
        await view.turn_page(interaction, self.delta)


class DayVoteView(discord.ui.View):
    def __init__(
        self,
        game: "LykaionGame",
        alive: List["LKPlayer"],
        timeout: int,
        use_dropdown: bool,
    ):
        super().__init__(timeout=timeout)
        self.game = game
        self.targets = alive
        self.use_dropdown = use_dropdown
        self.page = 0
        self.per_page = 24  # 1 extra select option slot is reserved for Abstain.
        self.alive_ids: Set[int] = {p.member.id for p in alive}
        self.eligible_voter_ids: Set[int] = {p.member.id for p in alive if not p.vote_locked_today}
        self.voter_choices: Dict[int, Optional[int]] = {}
        self.voters_attempted: Set[int] = set()
        self._thread_announced: Set[int] = set()
        self.message: Optional[discord.Message] = None
        self._build()

    def _build(self):
        self.clear_items()
        if self.use_dropdown:
            start = self.page * self.per_page
            end = start + self.per_page
            page_targets = self.targets[start:end]

            options = [discord.SelectOption(label="Abstain (skip)", value="skip", emoji="🕊️")]
            for p in page_targets:
                options.append(
                    discord.SelectOption(
                        label=(p.member.display_name or str(p.member.id))[:100],
                        value=str(p.member.id),
                    )
                )
            self.add_item(_DayVoteSelect(options))

            max_page = max(0, (len(self.targets) - 1) // self.per_page)
            if max_page > 0:
                self.add_item(_DayVotePageButton("◀ Prev", -1, disabled=(self.page == 0)))
                self.add_item(_DayVotePageButton("Next ▶", +1, disabled=(self.page >= max_page)))
            return

        self.add_item(_DayVoteTargetButton("Abstain", "skip", discord.ButtonStyle.secondary))
        for p in self.targets:
            self.add_item(
                _DayVoteTargetButton(
                    (p.member.display_name or str(p.member.id)),
                    str(p.member.id),
                    discord.ButtonStyle.primary,
                )
            )

    async def turn_page(self, interaction: discord.Interaction, delta: int):
        if not self.use_dropdown:
            await interaction.response.defer()
            return

        max_page = max(0, (len(self.targets) - 1) // self.per_page)
        self.page = min(max(self.page + delta, 0), max_page)
        self._build()
        await interaction.response.edit_message(view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id not in self.alive_ids:
            await interaction.response.send_message("Only living players can vote.", ephemeral=True)
            return False
        return True

    def _current_target_tally(self, target_id: int) -> int:
        demarchos_active = len(self.targets) > 3
        demarchos = self.game.living_demarchos() if demarchos_active else None
        demarchos_voter_id = demarchos.member.id if demarchos else None

        score = 0
        for voter_id, voted_target_id in self.voter_choices.items():
            if voted_target_id != target_id:
                continue
            score += 2 if voter_id == demarchos_voter_id else 1
        return score

    async def _maybe_finish_early(self):
        if self.is_finished():
            return
        if not self.eligible_voter_ids:
            return
        if not self.eligible_voter_ids.issubset(set(self.voter_choices.keys())):
            return

        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass
        await _safe_send(self.game.channel, "✅ All eligible votes are in. Closing vote early.")
        self.stop()

    async def register_vote(self, interaction: discord.Interaction, selected: str):
        voter_id = interaction.user.id
        voter_p = self.game.get_p(voter_id)

        if voter_p and voter_p.vote_locked_today:
            self.voters_attempted.add(voter_id)
            await interaction.response.send_message("🧵 The Thread steals your voice today.", ephemeral=True)
            if voter_id not in self._thread_announced:
                self._thread_announced.add(voter_id)
                await _safe_send(
                    self.game.channel,
                    f"🧵 {interaction.user.mention} tries to vote… but the Thread steals their voice today.",
                )
            return

        self.voters_attempted.add(voter_id)
        if voter_id in self.voter_choices:
            await interaction.response.send_message(
                "🔒 Your vote is already locked and cannot be changed.",
                ephemeral=True,
            )
            return

        if selected == "skip":
            self.voter_choices[voter_id] = None
            await _safe_send(self.game.channel, f"🕊️ {interaction.user.mention} abstains.")
            await interaction.response.send_message("✅ Vote locked: abstain.", ephemeral=True)
            await self._maybe_finish_early()
            return

        target_id = int(selected)
        target_p = self.game.get_p(target_id)
        if not target_p or not target_p.alive:
            await interaction.response.send_message("That target is no longer valid.", ephemeral=True)
            return

        self.voter_choices[voter_id] = target_id
        current_tally = self._current_target_tally(target_id)
        vote_word = "vote" if current_tally == 1 else "votes"
        await _safe_send(
            self.game.channel,
            f"🗳️ {interaction.user.mention} votes for <@{target_id}> ({current_tally} {vote_word}).",
        )
        await interaction.response.send_message(f"✅ Vote locked: <@{target_id}>.", ephemeral=True)
        await self._maybe_finish_early()

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class RolesPagerView(discord.ui.View):
    def __init__(self, pages: List[discord.Embed], timeout: int = 180):
        super().__init__(timeout=timeout)
        self.pages = pages
        self.page = 0
        self.message: Optional[discord.Message] = None
        self._sync_buttons()

    def _sync_buttons(self):
        max_page = len(self.pages) - 1
        self.prev.disabled = self.page <= 0
        self.next.disabled = self.page >= max_page

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page <= 0:
            await interaction.response.defer()
            return
        self.page -= 1
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.pages[self.page], view=self)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page >= len(self.pages) - 1:
            await interaction.response.defer()
            return
        self.page += 1
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.pages[self.page], view=self)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


# ============================================================
# Game State
# ============================================================

@dataclass
class LKPlayer:
    member: discord.Member
    role: LKRole
    initial_role: Optional[LKRole] = None
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
    eros_done: bool = False
    moirai_available: bool = False
    mainas_frenzy_available: bool = False
    nomophylax_last_jail_target_id: Optional[int] = None
    nomophylax_seal_available: bool = False
    nomophylax_seal_rolled: bool = False
    nemesis_target_id: Optional[int] = None
    power_sealed: bool = False

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
    join_seconds: int = 300
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
        is_gm_user = await user_is_gm(self.cog.bot, interaction.user)
        if interaction.user.id not in (self.host_id,) and not is_admin and not is_gm_user:
            await interaction.response.send_message("Only the host/GM can begin now.", ephemeral=True)
            return

        lobby["force_begin"].set()
        await interaction.response.send_message("⏩ Beginning now…", ephemeral=True)


# ============================================================
# Lykaion Game
# ============================================================

class LykaionGame:
    def __init__(
        self,
        bot: commands.Bot,
        cog: "Lykaion",
        channel: discord.TextChannel,
        players: List[discord.Member],
        speed: str,
        reward: int = 0,
    ):
        self.bot = bot
        self.cog = cog
        self.channel = channel
        self.speed = speed.lower()
        self.reward = max(0, int(reward))
        self.cfg = LKConfig()
        self.timer = self.cfg.timers.get(self.speed, 60)

        self.players: List[LKPlayer] = []
        self.day_no = 1
        self.night_no = 1

        self.ended: bool = False
        self._wolf_relay_task: Optional[asyncio.Task] = None

        # Hermes: one-time anonymous DM message
        self.hermes_holder_id: Optional[int] = None

        # Ariadne threads (supports duplicated Ariadne roles if they occur)
        self.ariadne_threads: Dict[int, Tuple[int, int]] = {}
        # Eros lover links (id -> linked ids)
        self.lovers_links: Dict[int, Set[int]] = {}
        # Limos infection tracking
        self.infected_ids: Set[int] = set()
        # Neutral bonus winners (Nemesis/Moros)
        self.neutral_winners: Set[int] = set()

        # Channel chat lock state/cache (night/dead speak+reaction lock)
        self._saved_overwrites: Dict[int, discord.PermissionOverwrite] = {}
        self._chat_lock_capable: Optional[bool] = None
        self._chat_lock_warned: bool = False
        self._is_night: bool = False

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
        return [p for p in self.alive_players() if role_side(p.role) == SIDE_WOLVES]

    def villagers(self) -> List[LKPlayer]:
        return [p for p in self.alive_players() if role_side(p.role) == SIDE_VILLAGE]

    def has_alive_role(self, role: LKRole, *, require_power: bool = False) -> bool:
        for p in self.alive_players():
            if p.role != role:
                continue
            if require_power and p.power_sealed:
                continue
            return True
        return False

    def living_demarchos(self) -> Optional[LKPlayer]:
        for p in self.alive_players():
            if p.role == LKRole.DEMARCHOS and not p.power_sealed:
                return p
        return None

    def _roles_in_play(self) -> List[LKRole]:
        return sorted(
            {p.initial_role or p.role for p in self.players},
            key=lambda role: role.value,
        )

    async def send_roles_in_play(self):
        roles = self._roles_in_play()
        if not roles:
            return

        description = "\n".join(f"• {role_display_name(role)}" for role in roles)
        embed = discord.Embed(
            title="🏛️ Roles In Play",
            description=description,
            color=discord.Color.orange(),
        )
        await self.channel.send(embed=embed)

    def _roll_nomophylax_seal_if_needed(self, p: LKPlayer):
        if p.role != LKRole.NOMOPHYLAX or p.nomophylax_seal_rolled:
            return
        p.nomophylax_seal_rolled = True
        p.nomophylax_seal_available = random.random() < 0.10

    def _apply_permanent_power_seal(self, p: LKPlayer) -> bool:
        if p.power_sealed:
            return False
        p.power_sealed = True
        p.hermes_available = False
        p.kerux_available = False
        p.medeia_heal_available = False
        p.medeia_poison_available = False
        p.moirai_available = False
        p.mainas_frenzy_available = False
        return True

    async def _wait_for_dm_text(
        self,
        member: discord.Member,
        prompt: str,
        timeout: int,
        *,
        max_chars: int = 200,
    ) -> Optional[str]:
        dm = await ensure_dm(member)
        await dm.send(prompt)

        def check(m: discord.Message) -> bool:
            return (
                m.author.id == member.id
                and isinstance(m.channel, discord.DMChannel)
                and m.channel.id == dm.id
                and not m.author.bot
            )

        try:
            reply = await self.bot.wait_for("message", check=check, timeout=timeout)
        except asyncio.TimeoutError:
            return None

        text = (reply.content or "").strip()
        if not text or text.lower() == "skip":
            return None
        return text[:max_chars]

    async def _run_nomophylax_cell_chat(self, jailer: LKPlayer, target: LKPlayer):
        window = min(self.timer, 25)
        jailer_dm = await ensure_dm(jailer.member)
        target_dm = await ensure_dm(target.member)

        jailer_text = await self._wait_for_dm_text(
            jailer.member,
            (
                f"💬 You may send **one** message to **{target.member.display_name}**. "
                f"Reply in the next **{window}s** or type `skip`."
            ),
            window,
        )
        if jailer_text:
            await _safe_send(target_dm, f"💬 **A message from the jailer:**\n{jailer_text}")
        else:
            await _safe_send(target_dm, "💬 The jailer chose not to send a message.")

        target_text = await self._wait_for_dm_text(
            target.member,
            f"💬 You may send **one** reply back in the next **{window}s** or type `skip`.",
            window,
        )
        if target_text:
            await _safe_send(jailer_dm, f"💬 **A reply from the jailed prisoner:**\n{target_text}")
        else:
            await _safe_send(jailer_dm, "💬 The jailed prisoner sent no reply.")

    def _limos_has_won(self) -> bool:
        limos_alive = [p for p in self.alive_players() if p.role == LKRole.LIMOS]
        if not limos_alive:
            return False

        living = self.alive_players()
        non_limos_ids = {p.member.id for p in living if p.role != LKRole.LIMOS}
        if not non_limos_ids:
            return True
        return non_limos_ids.issubset(self.infected_ids)

    def winner(self) -> Optional[str]:
        if self._limos_has_won():
            return "Limos (Plague)"

        alive = self.alive_players()
        if len(alive) <= 1:
            if alive:
                return f"{alive[0].member.display_name} (last alive)"
            return "No one"

        wolves_alive = any(role_side(p.role) == SIDE_WOLVES for p in alive)
        vill_alive = any(role_side(p.role) == SIDE_VILLAGE for p in alive)

        if wolves_alive and not vill_alive:
            return "Lykaones (Wolves)"
        if vill_alive and not wolves_alive:
            return "Village"
        if not wolves_alive and not vill_alive:
            return "Neutrals"
        return None

    def winner_side(self) -> Optional[str]:
        if self._limos_has_won():
            return SIDE_NEUTRAL

        alive = self.alive_players()
        if not alive:
            return None

        wolves_alive = any(role_side(p.role) == SIDE_WOLVES for p in alive)
        vill_alive = any(role_side(p.role) == SIDE_VILLAGE for p in alive)
        if wolves_alive and not vill_alive:
            return SIDE_WOLVES
        if vill_alive and not wolves_alive:
            return SIDE_VILLAGE
        if not wolves_alive and not vill_alive:
            return SIDE_NEUTRAL
        if len(alive) == 1:
            return role_side(alive[0].role)
        return None

    def _link_lovers(self, a_id: int, b_id: int):
        if a_id == b_id:
            return
        self.lovers_links.setdefault(a_id, set()).add(b_id)
        self.lovers_links.setdefault(b_id, set()).add(a_id)

    async def _notify_wolf_pack(self, prefix: str):
        wolves = self.wolves()
        if not wolves:
            return
        names = ", ".join(w.member.display_name for w in wolves)
        for w in wolves:
            wdm = await ensure_dm(w.member)
            await wdm.send(f"🐺 {prefix} **{names}**")

    async def _assign_nemesis_target(self, nemesis: LKPlayer):
        if nemesis.nemesis_target_id is not None:
            return
        targets = [p for p in self.alive_players() if p.member.id != nemesis.member.id]
        if not targets:
            return
        nemesis.nemesis_target_id = random.choice(targets).member.id

    async def _grant_role_defaults(self, p: LKPlayer):
        if p.role == LKRole.MEDEIA and (not p.medeia_heal_available) and (not p.medeia_poison_available):
            p.medeia_heal_available = True
            p.medeia_poison_available = True
        if p.role == LKRole.KERUX and not p.kerux_available:
            p.kerux_available = True
        if p.role == LKRole.MOIRAI and not p.moirai_available:
            p.moirai_available = True
        if p.role == LKRole.MAINAS and not p.mainas_frenzy_available:
            p.mainas_frenzy_available = True
        self._roll_nomophylax_seal_if_needed(p)
        if p.role == LKRole.NEMESIS:
            await self._assign_nemesis_target(p)

    @staticmethod
    def _clone_overwrite(overwrite: discord.PermissionOverwrite) -> discord.PermissionOverwrite:
        allow, deny = overwrite.pair()
        return discord.PermissionOverwrite.from_pair(allow, deny)

    def _remember_player_overwrite(self, player: LKPlayer):
        if player.member.id in self._saved_overwrites:
            return
        self._saved_overwrites[player.member.id] = self._clone_overwrite(
            self.channel.overwrites_for(player.member)
        )

    async def _ensure_chat_lock_capable(self) -> bool:
        if self._chat_lock_capable is not None:
            return self._chat_lock_capable

        me = self.channel.guild.me
        if me is None and self.bot.user is not None:
            me = self.channel.guild.get_member(self.bot.user.id)
        self._chat_lock_capable = bool(
            me and self.channel.permissions_for(me).manage_channels
        )

        if not self._chat_lock_capable and not self._chat_lock_warned:
            self._chat_lock_warned = True
            await _safe_send(
                self.channel,
                "⚠️ I cannot lock this channel (`Manage Channels` missing), so night/dead-player speak/reaction locking is disabled.",
            )
        return self._chat_lock_capable

    async def _set_player_send_permission(
        self,
        player: LKPlayer,
        send_messages: Optional[bool],
        add_reactions: Optional[bool],
        reason: str,
    ):
        self._remember_player_overwrite(player)
        overwrite = self.channel.overwrites_for(player.member)
        overwrite.send_messages = send_messages
        overwrite.add_reactions = add_reactions
        try:
            await self.channel.set_permissions(
                player.member, overwrite=overwrite, reason=reason
            )
        except discord.Forbidden:
            self._chat_lock_capable = False
            if not self._chat_lock_warned:
                self._chat_lock_warned = True
                await _safe_send(
                    self.channel,
                    "⚠️ I could not update channel permissions, so speak/reaction lock is disabled for this game.",
                )
        except discord.HTTPException:
            pass

    async def sync_chat_permissions(self):
        if not await self._ensure_chat_lock_capable():
            return

        for player in self.players:
            if self._is_night or not player.alive:
                desired_send = False
                desired_react = False
            else:
                self._remember_player_overwrite(player)
                original = self._saved_overwrites[player.member.id]
                desired_send = original.send_messages
                desired_react = original.add_reactions

            await self._set_player_send_permission(
                player,
                desired_send,
                desired_react,
                reason="Lykaion game chat controls",
            )

    async def restore_chat_permissions(self):
        if not self._saved_overwrites:
            return
        if not await self._ensure_chat_lock_capable():
            self._saved_overwrites.clear()
            return

        for player in self.players:
            original = self._saved_overwrites.get(player.member.id)
            if original is None:
                continue
            try:
                await self.channel.set_permissions(
                    player.member,
                    overwrite=original,
                    reason="Lykaion ended: restore original channel permissions",
                )
            except discord.HTTPException:
                pass

        self._saved_overwrites.clear()

    # ---------------- role assignment ----------------

    def _assign_roles(self, members: List[discord.Member]):
        n = len(members)
        roles: List[LKRole] = []
        max_non_polites = max(1, n - 1)  # Keep one guaranteed Polites slot.

        def add_unique(role: LKRole):
            if len(roles) >= max_non_polites or role in roles:
                return
            roles.append(role)

        def add_from_pool(pool: List[LKRole], count: int):
            if count <= 0 or len(roles) >= max_non_polites:
                return
            available = [r for r in pool if r not in roles]
            if not available:
                return
            take = min(count, len(available), max_non_polites - len(roles))
            for role in random.sample(available, k=take):
                roles.append(role)

        # Bracket tuning:
        # Shift some raw Lykaon count into wolf-special slots earlier so
        # mid-size games get more varied wolf-side play without exploding
        # total wolf-side headcount.
        if n <= 5:
            wolf_count = 1
            core_roles = [LKRole.MANTIS]
            village_extra_pool = [LKRole.IATROS, LKRole.KATARATOS]
            village_extra_count = 1 if n == 5 else 0
            wolf_special_count = 0
            neutral_count = 0
        elif n <= 7:
            wolf_count = 2
            core_roles = [LKRole.MANTIS, LKRole.IATROS]
            village_extra_pool = [LKRole.HOPLITES, LKRole.KYNEGOS, LKRole.KATARATOS, LKRole.NOMOPHYLAX]
            village_extra_count = 1
            wolf_special_count = 0
            neutral_count = 0
        elif n == 8:
            wolf_count = 1
            core_roles = [LKRole.MANTIS, LKRole.IATROS, LKRole.HOPLITES]
            village_extra_pool = [LKRole.KYNEGOS, LKRole.MEDEIA, LKRole.NOMOPHYLAX, LKRole.KATARATOS, LKRole.KERUX]
            village_extra_count = 1
            wolf_special_count = 1
            neutral_count = 0
        elif n == 9:
            wolf_count = 2
            core_roles = [LKRole.MANTIS, LKRole.IATROS, LKRole.HOPLITES]
            village_extra_pool = [LKRole.KYNEGOS, LKRole.MEDEIA, LKRole.NOMOPHYLAX, LKRole.KATARATOS, LKRole.KERUX]
            village_extra_count = 1
            wolf_special_count = 1
            neutral_count = 0
        elif n == 10:
            wolf_count = 2
            core_roles = [LKRole.MANTIS, LKRole.IATROS, LKRole.HOPLITES, LKRole.NOMOPHYLAX]
            village_extra_pool = [LKRole.KYNEGOS, LKRole.MEDEIA, LKRole.KERUX, LKRole.ARIADNE, LKRole.EROS, LKRole.KATARATOS, LKRole.MOIRAI]
            village_extra_count = 0
            wolf_special_count = 1
            neutral_count = 1
        elif n == 11:
            wolf_count = 2
            core_roles = [LKRole.MANTIS, LKRole.IATROS, LKRole.HOPLITES, LKRole.NOMOPHYLAX]
            village_extra_pool = [LKRole.KYNEGOS, LKRole.MEDEIA, LKRole.KERUX, LKRole.ARIADNE, LKRole.EROS, LKRole.KATARATOS, LKRole.MOIRAI]
            village_extra_count = 1
            wolf_special_count = 1
            neutral_count = 1
        elif n == 12:
            wolf_count = 3
            core_roles = [LKRole.MANTIS, LKRole.IATROS, LKRole.HOPLITES, LKRole.NOMOPHYLAX]
            village_extra_pool = [LKRole.KYNEGOS, LKRole.MEDEIA, LKRole.KERUX, LKRole.ARIADNE, LKRole.EROS, LKRole.KATARATOS, LKRole.MOIRAI]
            village_extra_count = 1
            wolf_special_count = 1
            neutral_count = 1
        elif n <= 14:
            wolf_count = 3
            core_roles = [LKRole.MANTIS, LKRole.IATROS, LKRole.HOPLITES, LKRole.NOMOPHYLAX]
            village_extra_pool = [LKRole.KYNEGOS, LKRole.MEDEIA, LKRole.KERUX, LKRole.ARIADNE, LKRole.EROS, LKRole.KATARATOS, LKRole.MOIRAI]
            village_extra_count = 2
            wolf_special_count = 1
            neutral_count = 1
        else:
            wolf_count = 3
            core_roles = [LKRole.MANTIS, LKRole.IATROS, LKRole.HOPLITES, LKRole.NOMOPHYLAX]
            village_extra_pool = [LKRole.KYNEGOS, LKRole.MEDEIA, LKRole.KERUX, LKRole.ARIADNE, LKRole.EROS, LKRole.KATARATOS, LKRole.MOIRAI]
            village_extra_count = 3
            wolf_special_count = 2
            neutral_count = 1

        wolf_count = min(wolf_count, max(1, n - 2))
        roles.extend([LKRole.LYKAON] * wolf_count)

        for role in core_roles:
            add_unique(role)

        if n > 3:
            add_unique(LKRole.DEMARCHOS)

        add_from_pool([LKRole.APATE, LKRole.SKIA, LKRole.MAINAS], wolf_special_count)
        add_from_pool([LKRole.LIMOS, LKRole.NEMESIS, LKRole.MOROS], neutral_count)
        add_from_pool(village_extra_pool, village_extra_count)

        # Always keep at least one plain villager in every game.
        roles += [LKRole.POLITES] * (n - len(roles))

        random.shuffle(roles)
        self.players = [LKPlayer(member=m, role=r, initial_role=r) for m, r in zip(members, roles)]

        # Hermes: assign to random villager-side (or anyone if only wolves)
        candidates = [p for p in self.alive_players() if role_side(p.role) == SIDE_VILLAGE] or self.alive_players()
        hermes = random.choice(candidates)
        self.hermes_holder_id = hermes.member.id
        hermes.hermes_available = True

        # Role resources
        for p in self.players:
            if p.role == LKRole.MEDEIA:
                p.medeia_heal_available = True
                p.medeia_poison_available = True
            if p.role == LKRole.KERUX:
                p.kerux_available = True
            if p.role == LKRole.MOIRAI:
                p.moirai_available = True
            if p.role == LKRole.MAINAS:
                p.mainas_frenzy_available = True
            self._roll_nomophylax_seal_if_needed(p)

    # ---------------- announcements / DMs ----------------

    async def dm_roles(self):
        for p in self.players:
            dm = await ensure_dm(p.member)
            role_message = (
                f"🏛️ **Lykaion**\n"
                f"You are **{role_display_name(p.role)}**.\n\n"
                f"{ROLE_DESCRIPTIONS[p.role]}\n"
                f"Game channel: {self.channel.mention}"
            )
            if p.role == LKRole.NOMOPHYLAX and p.nomophylax_seal_available:
                role_message += (
                    "\n\n🔒 **Rare Gift:** This game, you have a **one-time prison seal**. "
                    "When you jail someone, you may choose to permanently suppress their role power."
                )
            await dm.send(role_message)

        wolves = self.wolves()
        if wolves:
            names = ", ".join(w.member.display_name for w in wolves)
            for w in wolves:
                dm = await ensure_dm(w.member)
                await dm.send(f"🐺 Your fellow Lykaones are: **{names}**")

    async def nemesis_setup_phase(self):
        nemeses = [p for p in self.alive_players() if p.role == LKRole.NEMESIS]
        if not nemeses:
            return

        for nemesis in nemeses:
            dm = await ensure_dm(nemesis.member)
            targets = [p for p in self.alive_players() if p.member.id != nemesis.member.id]
            if not targets:
                continue

            chooser = ChoiceView(
                nemesis.member.id,
                [(t.member.display_name, str(t.member.id)) for t in targets],
                timeout=self.timer,
            )
            msg = await dm.send("⚖️ **Nemesis** — Choose your fate-target. If they are executed, you win.", view=chooser)
            await chooser.wait()
            try:
                await msg.edit(view=None)
            except discord.HTTPException:
                pass

            if chooser.choice_value is None:
                await self._assign_nemesis_target(nemesis)
                target = self.get_p(nemesis.nemesis_target_id) if nemesis.nemesis_target_id else None
                if target:
                    await dm.send(f"⏰ Timed out. Fate picked **{target.member.display_name}** for you.")
                continue

            nemesis.nemesis_target_id = int(chooser.choice_value)
            target = self.get_p(nemesis.nemesis_target_id)
            if target:
                await dm.send(f"✅ Your fate-target is **{target.member.display_name}**.")

    async def announce_demarchos(self):
        if len(self.alive_players()) <= 3:
            return
        demarchos = self.living_demarchos()
        if demarchos:
            await self.channel.send(
                f"🎖️ **Demarchos: {demarchos.member.mention}.** Their vote counts as **double** today."
            )
        else:
            await self.channel.send("🎖️ **No living Demarchos remains.** Double vote is inactive.")

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
        keruxes = [
            p for p in self.alive_players()
            if p.role == LKRole.KERUX and p.kerux_available and not p.power_sealed
        ]
        if not keruxes:
            return

        for kerux in keruxes:
            dm = await ensure_dm(kerux.member)
            view = YesNoView(kerux.member.id, timeout=self.timer, yes_label="Proclaim", no_label="Hold")
            msg = await dm.send("📢 **Kērux** — Send an **anonymous public proclamation** to the town now?", view=view)
            await view.wait()
            try:
                await msg.edit(view=None)
            except discord.HTTPException:
                pass
            if view.choice is not True:
                continue

            await dm.send("Type your proclamation (max 200 chars). It will be posted anonymously in the game channel.")

            def check(m: discord.Message) -> bool:
                return m.author.id == kerux.member.id and isinstance(m.channel, discord.DMChannel)

            try:
                reply = await self.bot.wait_for("message", check=check, timeout=self.timer)
            except asyncio.TimeoutError:
                await dm.send("⏰ Timed out. Kērux remains unused.")
                continue

            text = (reply.content or "").strip()
            if not text:
                await dm.send("Empty proclamation cancelled. Kērux remains unused.")
                continue
            text = text[:200]

            await self.channel.send(f"📢 **An anonymous proclamation echoes through Lykaion:**\n{text}")
            kerux.kerux_available = False
            await dm.send("✅ Proclamation delivered. Kērux is now spent.")

    # ============================================================
    # Ariadne (Night 1 thread)
    # ============================================================

    async def eros_night1_phase(self):
        eroses = [
            p for p in self.alive_players()
            if p.role == LKRole.EROS and not p.eros_done and not p.power_sealed
        ]
        if not eroses:
            return

        for eros in eroses:
            eros.eros_done = True
            dm = await ensure_dm(eros.member)
            await dm.send("💘 **Eros** — Choose **two players** to bind as Lovers (Night 1 only).")

            targets = [p for p in self.alive_players() if p.member.id != eros.member.id]
            if len(targets) < 2:
                await dm.send("Not enough valid targets to bind lovers.")
                continue

            chooser1 = ChoiceView(eros.member.id, [(t.member.display_name, str(t.member.id)) for t in targets], timeout=self.timer)
            m1 = await dm.send("Pick the **first** lover:", view=chooser1)
            await chooser1.wait()
            try:
                await m1.edit(view=None)
            except discord.HTTPException:
                pass
            if chooser1.choice_value is None:
                await dm.send("⏰ No lovers linked. (AFK / timed out)")
                continue
            t1 = int(chooser1.choice_value)

            targets2 = [p for p in targets if p.member.id != t1]
            chooser2 = ChoiceView(eros.member.id, [(t.member.display_name, str(t.member.id)) for t in targets2], timeout=self.timer)
            m2 = await dm.send("Pick the **second** lover:", view=chooser2)
            await chooser2.wait()
            try:
                await m2.edit(view=None)
            except discord.HTTPException:
                pass
            if chooser2.choice_value is None:
                await dm.send("⏰ Second pick cancelled. No lovers linked.")
                continue
            t2 = int(chooser2.choice_value)

            self._link_lovers(t1, t2)
            p1 = self.get_p(t1)
            p2 = self.get_p(t2)
            if p1 and p2:
                await dm.send(
                    f"💘 You bound **{p1.member.display_name}** and **{p2.member.display_name}**."
                )
                dm1 = await ensure_dm(p1.member)
                dm2 = await ensure_dm(p2.member)
                await dm1.send(f"💘 **Eros bound your heart to {p2.member.display_name}.** If one of you dies, so does the other.")
                await dm2.send(f"💘 **Eros bound your heart to {p1.member.display_name}.** If one of you dies, so does the other.")
                await _safe_send(self.channel, "💘 **Eros has bound two hearts.**")

    async def ariadne_night1_phase(self):
        ariadnes = [
            p for p in self.alive_players()
            if p.role == LKRole.ARIADNE and not p.ariadne_done and not p.power_sealed
        ]
        if not ariadnes:
            return

        for ariadne in ariadnes:
            ariadne.ariadne_done = True

            dm = await ensure_dm(ariadne.member)

            # Constraint: <=10 players => Ariadne loses vote daily (nerf)
            if len(self.players) <= 10:
                await dm.send("🧵 **Ariadnē** — Because this game has **10 players or fewer**, you will **lose your vote** each day.")
            else:
                await dm.send("🧵 **Ariadnē** — Choose **two players** to tie with your Thread (Night 1 only).")

            targets = [p for p in self.alive_players() if p.member.id != ariadne.member.id]
            if len(targets) < 2:
                continue

            chooser1 = ChoiceView(ariadne.member.id, [(t.member.display_name, str(t.member.id)) for t in targets], timeout=self.timer)
            m1 = await dm.send("Pick the **first** thread target:", view=chooser1)
            await chooser1.wait()
            try:
                await m1.edit(view=None)
            except discord.HTTPException:
                pass
            if chooser1.choice_value is None:
                await dm.send("⏰ No thread set. (AFK / timed out)")
                continue
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
                continue
            t2 = int(chooser2.choice_value)

            self.ariadne_threads[ariadne.member.id] = (t1, t2)

            p1 = self.get_p(t1)
            p2 = self.get_p(t2)
            if not p1 or not p2:
                continue

            # Backfire rule: if she picked two wolf-side players, Ariadne becomes a wolf
            if role_side(p1.role) == SIDE_WOLVES and role_side(p2.role) == SIDE_WOLVES:
                ariadne.role = LKRole.LYKAON
                await dm.send("🩸 The Thread snaps in the Labyrinth… You tied **two Lykaones**. The curse turns **you** into a **Lykaon**.")
                await self._notify_wolf_pack("The pack grows. Fellow Lykaones:")
            else:
                await dm.send(
                    f"🧵 Your Thread is tied.\n"
                    f"If **{p1.member.display_name}** dies, **{p2.member.display_name}** will lose their vote the next day.\n"
                    f"If **{p2.member.display_name}** dies, **{p1.member.display_name}** will lose their vote the next day."
                )

    def _apply_ariadne_vote_lock_from_death(self, dead_id: int):
        if not self.ariadne_threads:
            return
        for a, b in self.ariadne_threads.values():
            if dead_id == a:
                other = self.get_p(b)
                if other and other.alive:
                    other.vote_locked_today = True
            elif dead_id == b:
                other = self.get_p(a)
                if other and other.alive:
                    other.vote_locked_today = True

    async def moirai_day_phase(self):
        moirais = [
            p for p in self.alive_players()
            if p.role == LKRole.MOIRAI and p.moirai_available and not p.power_sealed
        ]
        if not moirais:
            return

        for moirai in moirais:
            dm = await ensure_dm(moirai.member)
            view = YesNoView(moirai.member.id, timeout=self.timer, yes_label="Weave Fate", no_label="Not now")
            msg = await dm.send("🧵 **Moirai** — Use your one-time role swap now?", view=view)
            await view.wait()
            try:
                await msg.edit(view=None)
            except discord.HTTPException:
                pass
            if view.choice is not True:
                continue

            living = self.alive_players()
            if len(living) < 2:
                await dm.send("Not enough living players to swap.")
                continue

            c1 = ChoiceView(moirai.member.id, [(p.member.display_name, str(p.member.id)) for p in living], timeout=self.timer)
            m1 = await dm.send("Choose the **first** player:", view=c1)
            await c1.wait()
            try:
                await m1.edit(view=None)
            except discord.HTTPException:
                pass
            if c1.choice_value is None:
                await dm.send("⏰ Swap cancelled.")
                continue

            first_id = int(c1.choice_value)
            second_options = [p for p in living if p.member.id != first_id]
            c2 = ChoiceView(moirai.member.id, [(p.member.display_name, str(p.member.id)) for p in second_options], timeout=self.timer)
            m2 = await dm.send("Choose the **second** player:", view=c2)
            await c2.wait()
            try:
                await m2.edit(view=None)
            except discord.HTTPException:
                pass
            if c2.choice_value is None:
                await dm.send("⏰ Second pick cancelled.")
                continue

            second_id = int(c2.choice_value)
            p1 = self.get_p(first_id)
            p2 = self.get_p(second_id)
            if not p1 or not p2:
                await dm.send("Swap failed: invalid targets.")
                continue

            p1_side_before = role_side(p1.role)
            p2_side_before = role_side(p2.role)
            p1.role, p2.role = p2.role, p1.role
            await self._grant_role_defaults(p1)
            await self._grant_role_defaults(p2)

            # Ensure neutral winners still make sense after role shuffles.
            if p1.role != LKRole.NEMESIS:
                p1.nemesis_target_id = None
            if p2.role != LKRole.NEMESIS:
                p2.nemesis_target_id = None
            if p1.role == LKRole.NEMESIS and p1.nemesis_target_id is None:
                await self._assign_nemesis_target(p1)
            if p2.role == LKRole.NEMESIS and p2.nemesis_target_id is None:
                await self._assign_nemesis_target(p2)

            if p1_side_before != role_side(p1.role) or p2_side_before != role_side(p2.role):
                await self._notify_wolf_pack("Fate has rewoven the pack. Fellow Lykaones:")

            for swapped in (p1, p2):
                sdm = await ensure_dm(swapped.member)
                await _safe_send(
                    sdm,
                    f"🧵 Fate rewove your thread. You are now **{role_display_name(swapped.role)}**.\n"
                    f"{ROLE_DESCRIPTIONS[swapped.role]}",
                )
                if swapped.role == LKRole.NOMOPHYLAX and swapped.nomophylax_seal_available:
                    await _safe_send(
                        sdm,
                        "🔒 This fate also granted you a **one-time prison seal** for the rest of the game.",
                    )
                if swapped.role == LKRole.NEMESIS and swapped.nemesis_target_id is not None:
                    target = self.get_p(swapped.nemesis_target_id)
                    if target:
                        await _safe_send(
                            sdm,
                            f"⚖️ Your fate-target is **{target.member.display_name}**.",
                        )

            moirai.moirai_available = False
            await self.channel.send(
                f"🧵 **The Moirai rewove fate between {p1.member.mention} and {p2.member.mention}.**"
            )
            await dm.send("✅ Fate was rewoven.")

    async def skia_night_phase(self) -> Set[int]:
        blocked_ids: Set[int] = set()
        skias = [p for p in self.alive_players() if p.role == LKRole.SKIA and not p.power_sealed]
        if not skias:
            return blocked_ids

        for skia in skias:
            dm = await ensure_dm(skia.member)
            targets = [
                p for p in self.alive_players()
                if p.member.id != skia.member.id
                and role_side(p.role) != SIDE_WOLVES
                and p.member.id not in blocked_ids
            ]
            if not targets:
                await dm.send("No valid targets to silence tonight.")
                continue

            chooser = ChoiceView(skia.member.id, [(t.member.display_name, str(t.member.id)) for t in targets], timeout=self.timer)
            msg = await dm.send("🌑 **Skia** — Choose one target to silence tonight:", view=chooser)
            await chooser.wait()
            try:
                await msg.edit(view=None)
            except discord.HTTPException:
                pass

            if chooser.choice_value is None:
                await dm.send("⏰ No one was silenced.")
                continue

            blocked_id = int(chooser.choice_value)
            blocked = self.get_p(blocked_id)
            if not blocked or not blocked.alive:
                await dm.send("Target invalid.")
                continue

            blocked_ids.add(blocked_id)
            bdm = await ensure_dm(blocked.member)
            await _safe_send(bdm, "🌑 A shadow bound your power. You cannot use your night action.")
            await dm.send(f"✅ You silenced **{blocked.member.display_name}**.")

        return blocked_ids

    async def nomophylax_night_phase(self, blocked_actor_ids: Set[int]) -> Set[int]:
        jailed_ids: Set[int] = set()
        jailers = [
            p for p in self.alive_players()
            if p.role == LKRole.NOMOPHYLAX and not p.power_sealed
        ]
        if not jailers:
            return jailed_ids

        for jailer in jailers:
            dm = await ensure_dm(jailer.member)
            if jailer.member.id in blocked_actor_ids:
                await dm.send("🌑 You were silenced and could not jail anyone tonight.")
                continue

            targets = [
                p for p in self.alive_players()
                if p.member.id != jailer.member.id
                and p.member.id != jailer.nomophylax_last_jail_target_id
                and p.member.id not in jailed_ids
            ]
            if not targets:
                await dm.send("No valid jail target tonight.")
                continue

            chooser = ChoiceView(jailer.member.id, [(t.member.display_name, str(t.member.id)) for t in targets], timeout=self.timer)
            msg = await dm.send("⛓️ **Nomophylax** — Choose one player to jail tonight:", view=chooser)
            await chooser.wait()
            try:
                await msg.edit(view=None)
            except discord.HTTPException:
                pass

            if chooser.choice_value is None:
                await dm.send("⏰ No jail target chosen.")
                continue

            jailed_id = int(chooser.choice_value)
            target = self.get_p(jailed_id)
            if not target or not target.alive:
                await dm.send("Target invalid.")
                continue

            jailed_ids.add(jailed_id)
            jailer.nomophylax_last_jail_target_id = jailed_id
            tdm = await ensure_dm(target.member)
            await _safe_send(
                tdm,
                "⛓️ The Nomophylax jailed you tonight. You are protected from wolf attacks but cannot act.",
            )
            if jailer.nomophylax_seal_available and not target.power_sealed:
                seal_view = YesNoView(
                    jailer.member.id,
                    timeout=self.timer,
                    yes_label="Use Seal",
                    no_label="Keep It",
                )
                seal_msg = await dm.send(
                    "🔒 **Rare Prison Seal** — Use your one-time seal on this prisoner? "
                    "If you do, their role power is permanently suppressed for the rest of the game.",
                    view=seal_view,
                )
                await seal_view.wait()
                try:
                    await seal_msg.edit(view=None)
                except discord.HTTPException:
                    pass
                if seal_view.choice is True:
                    jailer.nomophylax_seal_available = False
                    self._apply_permanent_power_seal(target)
                    await _safe_send(
                        tdm,
                        "🔒 A sacred prison seal crushes your gifts. You can no longer use your role power.",
                    )
                    await dm.send(
                        f"🔒 You sealed **{target.member.display_name}**. Their role power is gone for the rest of the game."
                    )

            await self._run_nomophylax_cell_chat(jailer, target)
            await dm.send(f"✅ You jailed **{target.member.display_name}**.")

        return jailed_ids

    async def hoplites_night_phase(self, blocked_actor_ids: Set[int], jailed_ids: Set[int]) -> Dict[int, List[int]]:
        guarded: Dict[int, List[int]] = {}
        hoplites = [p for p in self.alive_players() if p.role == LKRole.HOPLITES and not p.power_sealed]
        if not hoplites:
            return guarded

        for hoplite in hoplites:
            dm = await ensure_dm(hoplite.member)
            if hoplite.member.id in blocked_actor_ids:
                await dm.send("🌑 You were silenced and could not guard anyone tonight.")
                continue
            if hoplite.member.id in jailed_ids:
                await dm.send("⛓️ You were jailed and could not guard anyone tonight.")
                continue

            targets = [p for p in self.alive_players() if p.member.id != hoplite.member.id]
            if not targets:
                continue

            chooser = ChoiceView(hoplite.member.id, [(t.member.display_name, str(t.member.id)) for t in targets], timeout=self.timer)
            msg = await dm.send("🛡️ **Hoplites** — Choose who to guard tonight:", view=chooser)
            await chooser.wait()
            try:
                await msg.edit(view=None)
            except discord.HTTPException:
                pass
            if chooser.choice_value is None:
                await dm.send("⏰ You guarded no one.")
                continue

            target_id = int(chooser.choice_value)
            target = self.get_p(target_id)
            if not target or not target.alive:
                await dm.send("Target invalid.")
                continue

            guarded.setdefault(target_id, []).append(hoplite.member.id)
            await dm.send(f"✅ You guard **{target.member.display_name}**.")

        return guarded

    async def limos_night_phase(self, blocked_actor_ids: Set[int], jailed_ids: Set[int]) -> bool:
        acted = False
        limoses = [p for p in self.alive_players() if p.role == LKRole.LIMOS and not p.power_sealed]
        if not limoses:
            return acted

        for limos in limoses:
            dm = await ensure_dm(limos.member)
            if limos.member.id in blocked_actor_ids:
                await dm.send("🌑 You were silenced and failed to spread the plague tonight.")
                continue
            if limos.member.id in jailed_ids:
                await dm.send("⛓️ You were jailed and failed to spread the plague tonight.")
                continue

            targets = [
                p for p in self.alive_players()
                if p.member.id != limos.member.id and p.member.id not in self.infected_ids
            ]
            if not targets:
                await dm.send("Everyone alive is already infected.")
                continue

            chooser = ChoiceView(limos.member.id, [(t.member.display_name, str(t.member.id)) for t in targets], timeout=self.timer)
            msg = await dm.send("🦠 **Limos** — Choose one player to infect tonight:", view=chooser)
            await chooser.wait()
            try:
                await msg.edit(view=None)
            except discord.HTTPException:
                pass
            if chooser.choice_value is None:
                await dm.send("⏰ No infection spread tonight.")
                continue

            target_id = int(chooser.choice_value)
            target = self.get_p(target_id)
            if not target or not target.alive:
                await dm.send("Target invalid.")
                continue

            self.infected_ids.add(target_id)
            acted = True
            await dm.send(f"🦠 You infected **{target.member.display_name}**.")
            tdm = await ensure_dm(target.member)
            await _safe_send(tdm, "🤢 A fever takes hold. You have been infected.")

        return acted

    async def mainas_frenzy_phase(
        self,
        primary_target_id: Optional[int],
        blocked_actor_ids: Set[int],
        jailed_ids: Set[int],
    ) -> List[int]:
        extra_targets: List[int] = []
        mainades = [
            p for p in self.alive_players()
            if p.role == LKRole.MAINAS and p.mainas_frenzy_available and not p.power_sealed
        ]
        if not mainades:
            return extra_targets

        for mainas in mainades:
            dm = await ensure_dm(mainas.member)
            if mainas.member.id in blocked_actor_ids:
                await dm.send("🌑 You were silenced and could not unleash your frenzy.")
                continue
            if mainas.member.id in jailed_ids:
                await dm.send("⛓️ You were jailed and could not unleash your frenzy.")
                continue

            view = YesNoView(mainas.member.id, timeout=self.timer, yes_label="Frenzy", no_label="Save it")
            msg = await dm.send("🔥 **Mainas** — Use your one-time frenzy for an extra wolf kill tonight?", view=view)
            await view.wait()
            try:
                await msg.edit(view=None)
            except discord.HTTPException:
                pass
            if view.choice is not True:
                continue

            blocked_ids = {i for i in extra_targets}
            if primary_target_id is not None:
                blocked_ids.add(primary_target_id)
            targets = [
                p for p in self.alive_players()
                if role_side(p.role) != SIDE_WOLVES and p.member.id not in blocked_ids
            ]
            if not targets:
                await dm.send("No valid frenzy target.")
                continue

            chooser = ChoiceView(mainas.member.id, [(t.member.display_name, str(t.member.id)) for t in targets], timeout=self.timer)
            m2 = await dm.send("Choose your frenzy victim:", view=chooser)
            await chooser.wait()
            try:
                await m2.edit(view=None)
            except discord.HTTPException:
                pass
            if chooser.choice_value is None:
                await dm.send("Frenzy cancelled. You keep it.")
                continue

            target_id = int(chooser.choice_value)
            target = self.get_p(target_id)
            if not target or not target.alive:
                await dm.send("Target invalid.")
                continue

            mainas.mainas_frenzy_available = False
            extra_targets.append(target_id)
            await dm.send(f"🔥 Frenzy marked **{target.member.display_name}**.")

        return extra_targets

    # ============================================================
    # Night actions
    # ============================================================

    async def night_phase(self) -> List[int]:
        self._is_night = True
        lock_active = await self._ensure_chat_lock_capable()
        await self.sync_chat_permissions()
        if lock_active:
            await self.channel.send(
                "🌘 **Night falls over Lykaion...** The channel is now locked to players."
            )
        else:
            await self.channel.send("🌘 **Night falls over Lykaion...**")

        # Public progress, without leaking targets
        did_mantis = False
        did_iatros = False
        did_hoplites = False
        did_nomophylax = False
        did_skia = False
        did_mainas = False
        did_limos = False
        did_wolves = False
        did_medeia_heal = False
        did_medeia_poison = False
        did_curse_turn = False

        # Night 1: Eros + Ariadne setup
        if self.night_no == 1:
            eros_present = self.has_alive_role(LKRole.EROS, require_power=True)
            ariadne_present = self.has_alive_role(LKRole.ARIADNE, require_power=True)
            if eros_present:
                await _safe_send(self.channel, "💘 **Eros takes aim...**")
                await self.eros_night1_phase()
            if ariadne_present:
                await _safe_send(self.channel, "🧵 **Ariadnē weaves the Thread...**")
                await self.ariadne_night1_phase()
                await _safe_send(self.channel, "🧵 **The Thread is set.**")

        # Skia blocks one action
        blocked_actor_ids: Set[int] = set()
        if self.has_alive_role(LKRole.SKIA, require_power=True):
            await _safe_send(self.channel, "🌑 **Skia stalks the alleys...**")
            blocked_actor_ids = await self.skia_night_phase()
        if blocked_actor_ids:
            did_skia = True

        # Nomophylax jail
        jailed_ids: Set[int] = set()
        if self.has_alive_role(LKRole.NOMOPHYLAX, require_power=True):
            await _safe_send(self.channel, "⛓️ **Nomophylax closes the cells...**")
            jailed_ids = await self.nomophylax_night_phase(blocked_actor_ids)
        if jailed_ids:
            did_nomophylax = True

        # Mantis
        mantises = [p for p in self.alive_players() if p.role == LKRole.MANTIS and not p.power_sealed]
        if mantises:
            await _safe_send(self.channel, "👁️ **Mantis is divining...**")
            for mantis in mantises:
                if mantis.member.id in blocked_actor_ids:
                    mdm = await ensure_dm(mantis.member)
                    await _safe_send(mdm, "🌑 You were silenced and could not divine tonight.")
                    continue
                if mantis.member.id in jailed_ids:
                    mdm = await ensure_dm(mantis.member)
                    await _safe_send(mdm, "⛓️ You were jailed and could not divine tonight.")
                    continue
                if await self._mantis_action(mantis):
                    did_mantis = True
            await _safe_send(self.channel, "👁️ **Mantis has finished.**")

        # Iatros
        healers = [p for p in self.alive_players() if p.role == LKRole.IATROS and not p.power_sealed]
        healed_ids: Set[int] = set()
        if healers:
            await _safe_send(self.channel, "🧪 **Iatros prepares a protection...**")
            for healer in healers:
                if healer.member.id in blocked_actor_ids:
                    hdm = await ensure_dm(healer.member)
                    await _safe_send(hdm, "🌑 You were silenced and could not protect tonight.")
                    continue
                if healer.member.id in jailed_ids:
                    hdm = await ensure_dm(healer.member)
                    await _safe_send(hdm, "⛓️ You were jailed and could not protect tonight.")
                    continue
                healed_id = await self._iatros_action(healer)
                if healed_id is not None:
                    healed_ids.add(healed_id)
                    did_iatros = True
            await _safe_send(self.channel, "🧪 **Iatros has finished.**")

        # Hoplites
        guarded_map: Dict[int, List[int]] = {}
        if self.has_alive_role(LKRole.HOPLITES, require_power=True):
            await _safe_send(self.channel, "🛡️ **Hoplites takes position...**")
            guarded_map = await self.hoplites_night_phase(blocked_actor_ids, jailed_ids)
        if guarded_map:
            did_hoplites = True

        # Limos infection
        if self.has_alive_role(LKRole.LIMOS, require_power=True):
            did_limos = await self.limos_night_phase(blocked_actor_ids, jailed_ids)

        # Wolf council: relay + vote
        await _safe_send(self.channel, "🐺 **Lykaones convene in the dark...**")
        victim_id = await self._wolves_council_and_kill(unavailable_wolf_ids=jailed_ids)
        if victim_id is not None:
            did_wolves = True

        # Mainas frenzy (extra wolf kill)
        mainas_extra_targets = await self.mainas_frenzy_phase(victim_id, blocked_actor_ids, jailed_ids)
        if mainas_extra_targets:
            did_mainas = True

        # Medeia reacts after wolves pick (heal victim / poison someone)
        if any(p.role == LKRole.MEDEIA and not p.power_sealed for p in self.alive_players()):
            await _safe_send(self.channel, "⚗️ **Medeia considers fate and venom...**")
        victim_id, extra_dead_ids, used_heal, used_poison = await self._medeia_actions(
            victim_id, blocked_actor_ids, jailed_ids
        )
        did_medeia_heal = used_heal
        did_medeia_poison = used_poison

        # Resolve wolf-side kills for dawn (primary + frenzy).
        dawn_deaths: List[int] = []
        wolf_targets: List[int] = []
        if victim_id is not None:
            wolf_targets.append(victim_id)
        for extra_id in mainas_extra_targets:
            if extra_id not in wolf_targets:
                wolf_targets.append(extra_id)

        for target_id in wolf_targets:
            target = self.get_p(target_id)
            if not target or not target.alive:
                continue

            # Jailed target is protected from wolf-side attacks.
            if target_id in jailed_ids:
                await self.channel.send(
                    f"⛓️ **{target.member.display_name}** was jailed and survived the night."
                )
                continue

            # Iatros protection
            if target_id in healed_ids:
                await self.channel.send(
                    f"🧪 A protection held in the dark. **{target.member.display_name}** survived."
                )
                continue

            # Hoplites intercept
            guards = guarded_map.get(target_id, [])
            living_guards = [gid for gid in guards if (self.get_p(gid) and self.get_p(gid).alive)]
            if living_guards:
                guard_id = living_guards[0]
                guard = self.get_p(guard_id)
                if guard:
                    await self.channel.send(
                        f"🛡️ **{guard.member.display_name}** intercepted the attack meant for **{target.member.display_name}**!"
                    )
                    if guard_id not in dawn_deaths:
                        dawn_deaths.append(guard_id)
                    continue

            # Kataratos turns instead of dying
            if target.role == LKRole.KATARATOS and not target.power_sealed:
                target.role = LKRole.LYKAON
                did_curse_turn = True
                await self.channel.send(
                    f"🌒 **The curse awakens! {target.member.mention} has become a Lykaon.**"
                )
                await self._notify_wolf_pack("A cursed soul joined the pack. Fellow Lykaones:")
                continue

            if target_id not in dawn_deaths:
                dawn_deaths.append(target_id)

        # Execute poison kill immediately (if any)
        for extra_dead in extra_dead_ids:
            t = self.get_p(extra_dead)
            if t and t.alive:
                await self.kill_player(extra_dead, reason="was undone by Medeia’s poison")

        # Public night summary (no targets)
        summary_bits: List[str] = []
        if did_mantis:
            summary_bits.append("👁️ Mantis acted")
        if did_iatros:
            summary_bits.append("🧪 Iatros acted")
        if did_hoplites:
            summary_bits.append("🛡️ Hoplites acted")
        if did_nomophylax:
            summary_bits.append("⛓️ Nomophylax acted")
        if did_skia:
            summary_bits.append("🌑 Skia acted")
        if did_mainas:
            summary_bits.append("🔥 Mainas acted")
        if did_limos:
            summary_bits.append("🦠 Limos acted")
        if did_wolves:
            summary_bits.append("🐺 Lykaones acted")
        if did_medeia_heal:
            summary_bits.append("⚗️ Medeia healed")
        if did_medeia_poison:
            summary_bits.append("☠️ Medeia poisoned")
        if did_curse_turn:
            summary_bits.append("🌒 Kataratos turned")
        if summary_bits:
            await _safe_send(self.channel, "🌙 **Night actions resolved:** " + " • ".join(summary_bits))

        return dawn_deaths

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

        revealed_role = LKRole.POLITES if (t.role == LKRole.APATE and not t.power_sealed) else t.role
        await dm.send(f"🔎 **{t.member.display_name}** is: **{role_display_name(revealed_role)}**")
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

    async def _wolves_council_and_kill(self, unavailable_wolf_ids: Set[int]) -> Optional[int]:
        wolves = [w for w in self.wolves() if w.member.id not in unavailable_wolf_ids]
        if not wolves:
            await _safe_send(self.channel, "🐺 The pack is disrupted tonight and chooses no victim.")
            return None

        if len(wolves) > 1:
            names = ", ".join(w.member.display_name for w in wolves)
            for w in wolves:
                dm = await ensure_dm(w.member)
                await dm.send(
                    f"🐺 **Wolf Council**\n"
                    f"Fellow Lykaones: **{names}**\n"
                    f"You have **{self.cfg.wolf_chat_seconds}s** to discuss. Any DM you send me now will be relayed to the pack."
                )

            # Relay phase (pack chat)
            try:
                self._wolf_relay_task = asyncio.create_task(self._wolves_relay(wolves, self.cfg.wolf_chat_seconds))
                await asyncio.sleep(self.cfg.wolf_chat_seconds)
            finally:
                if self._wolf_relay_task:
                    self._wolf_relay_task.cancel()
                    self._wolf_relay_task = None
        else:
            lone = wolves[0]
            dm = await ensure_dm(lone.member)
            await dm.send(
                "🐺 **Lone Lykaon**\n"
                "You are the only wolf alive tonight. No council delay; choose your prey now."
            )

        # Vote
        targets = [p for p in self.alive_players() if role_side(p.role) != SIDE_WOLVES]
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

    async def _medeia_actions(
        self,
        victim_id: Optional[int],
        blocked_actor_ids: Set[int],
        jailed_ids: Set[int],
    ) -> Tuple[Optional[int], List[int], bool, bool]:
        """
        Returns (final_wolf_victim_id, extra_poison_dead_ids, used_heal, used_poison)
        """
        medeias = [p for p in self.alive_players() if p.role == LKRole.MEDEIA and not p.power_sealed]
        if not medeias:
            return victim_id, [], False, False

        poison_dead_ids: List[int] = []
        used_heal = False
        used_poison = False

        for medeia in medeias:
            dm = await ensure_dm(medeia.member)
            if medeia.member.id in blocked_actor_ids:
                await dm.send("🌑 You were silenced and could not use your craft tonight.")
                continue
            if medeia.member.id in jailed_ids:
                await dm.send("⛓️ You were jailed and could not use your craft tonight.")
                continue

            # Heal option first (if victim still exists)
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

            if medeia.medeia_poison_available:
                view = YesNoView(medeia.member.id, timeout=self.timer, yes_label="Poison", no_label="Save it")
                msg = await dm.send("☠️ **Medeia** — Do you want to use your **one-time poison** tonight?", view=view)
                await view.wait()
                try:
                    await msg.edit(view=None)
                except discord.HTTPException:
                    pass
                if view.choice is True:
                    blocked_ids = set(poison_dead_ids)
                    targets = [
                        p for p in self.alive_players()
                        if p.member.id != medeia.member.id and p.member.id not in blocked_ids
                    ]
                    if not targets:
                        await dm.send("No valid targets available for poison tonight.")
                        continue

                    chooser = ChoiceView(medeia.member.id, [(t.member.display_name, str(t.member.id)) for t in targets], timeout=self.timer)
                    m2 = await dm.send("Choose who to poison:", view=chooser)
                    await chooser.wait()
                    try:
                        await m2.edit(view=None)
                    except discord.HTTPException:
                        pass
                    if chooser.choice_value is not None:
                        poison_dead = int(chooser.choice_value)
                        poison_dead_ids.append(poison_dead)
                        medeia.medeia_poison_available = False
                        used_poison = True
                        t = self.get_p(poison_dead)
                        if t:
                            await dm.send(f"☠️ You poisoned **{t.member.display_name}**.")
                    else:
                        await dm.send("Poison cancelled. You keep it for later.")

        return victim_id, poison_dead_ids, used_heal, used_poison

    # ============================================================
    # Day phase: PUBLIC vote (buttons first, dropdown when needed)
    # ============================================================

    async def day_phase(self, night_victim_ids: List[int]):
        self._is_night = False
        await self.channel.send(f"🌤️ **Day {self.day_no}**")

        # Ariadne nerf (<=10) applied each day
        if len(self.players) <= 10:
            for ariadne in [p for p in self.alive_players() if p.role == LKRole.ARIADNE]:
                ariadne.vote_locked_today = True

        # Resolve night kill
        if night_victim_ids:
            for victim_id in night_victim_ids:
                await self.kill_player(victim_id, reason="was slain in the night")
        else:
            await self.channel.send("🌅 Dawn breaks… **No bodies are found.**")

        if self.winner() or self.ended:
            return

        # Open the channel for living players only (dead stay muted and cannot react).
        await self.sync_chat_permissions()

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
        await self.moirai_day_phase()
        if self.winner() or self.ended:
            return

        # Demarchos announce
        await self.announce_demarchos()

        # Public vote instructions
        await self.channel.send(
            "🗳️ **Morning Vote**\n"
            f"Alive players: {', '.join(p.member.mention for p in self.alive_players())}\n\n"
            "**To vote:** use the vote buttons below.\n"
            "If there are too many options, voting switches to a dropdown.\n"
            "Pick **Abstain** to skip.\n"
            f"⏳ You have **{self.timer}s**.\n\n"
            "(Your **first valid** vote is final and cannot be changed. "
            "Voting closes early when all eligible votes are in.)"
        )

        lynch_id = await self.public_day_vote()
        if lynch_id is not None:
            await self.kill_player(lynch_id, reason="was executed by the Polis")
            for nemesis in [p for p in self.players if p.role == LKRole.NEMESIS]:
                if nemesis.nemesis_target_id == lynch_id:
                    self.neutral_winners.add(nemesis.member.id)
                    await self.channel.send(
                        f"⚖️ **Nemesis fulfilled fate:** {nemesis.member.mention} has achieved their prophecy."
                    )
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

        use_dropdown = len(alive) > 24
        vote_view = DayVoteView(self, alive, timeout=self.timer, use_dropdown=use_dropdown)
        if not vote_view.eligible_voter_ids:
            await self.channel.send("🧵 No eligible voices can vote today.")
            return None
        if use_dropdown:
            prompt = (
                "Too many options for vote buttons, so dropdown mode is active. "
                "Your first valid vote is final, and voting closes when all eligible votes are in."
            )
        else:
            prompt = "Use the vote buttons below. Your first valid vote is final, and voting closes when all eligible votes are in."
        vote_message = await self.channel.send(
            prompt,
            view=vote_view,
        )
        vote_view.message = vote_message
        await vote_view.wait()
        return await self._resolve_day_vote(alive, vote_view.voters_attempted, vote_view.voter_choices)

    async def _resolve_day_vote(
        self,
        alive: List[LKPlayer],
        voters_attempted: Set[int],
        voter_choices: Dict[int, Optional[int]],
    ) -> Optional[int]:
        nonvoters = [
            p.member.mention
            for p in alive
            if p.member.id not in voters_attempted and not p.vote_locked_today
        ]
        if nonvoters:
            await self.channel.send("😴 **AFK / no vote received from:** " + ", ".join(nonvoters))

        votes: Dict[int, int] = {}
        demarchos_active = len(alive) > 3
        demarchos = self.living_demarchos() if demarchos_active else None
        demarchos_voter_id = demarchos.member.id if demarchos else None
        for voter_id, target_id in voter_choices.items():
            if target_id is None:
                continue
            weight = 2 if demarchos_voter_id == voter_id else 1
            votes[target_id] = votes.get(target_id, 0) + weight

        if not votes:
            return None

        sorted_targets = sorted(votes.items(), key=lambda kv: kv[1], reverse=True)

        tally = []
        for target_id, score in sorted_targets:
            tp = self.get_p(target_id)
            name = tp.member.display_name if tp else str(target_id)
            vote_word = "vote" if score == 1 else "votes"
            tally.append(f"{name} ({score} {vote_word})")
        if tally:
            await self.channel.send("📊 **Final tally:** " + " | ".join(tally))

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
        await self.sync_chat_permissions()

        # Ariadne vote-lock effect from death
        self._apply_ariadne_vote_lock_from_death(member_id)

        await self.channel.send(f"💀 **{p.member.display_name}** {reason}. They were **{role_display_name(p.role)}**.")

        if p.role == LKRole.MOROS and "executed by the Polis" in reason:
            if p.member.id not in self.neutral_winners:
                self.neutral_winners.add(p.member.id)
                await self.channel.send(
                    f"⚫ **Moros fulfilled destiny:** {p.member.mention} wins by execution."
                )

        # Eros lovers: if one dies, linked lovers die of grief.
        linked = list(self.lovers_links.get(member_id, set()))
        for lover_id in linked:
            lover = self.get_p(lover_id)
            if lover and lover.alive:
                await self.channel.send(
                    f"💔 **{lover.member.display_name}** cannot bear the loss of their lover."
                )
                await self.kill_player(
                    lover_id,
                    reason=f"died of grief for {p.member.display_name}",
                )

        # Hermes: if holder dies, Hermes is lost
        if self.hermes_holder_id == member_id:
            self.hermes_holder_id = None

        # Hunter shot
        if p.role == LKRole.KYNEGOS and self.winner() is None and not self.ended:
            await self.hunter_shot(p)

    async def hunter_shot(self, hunter: LKPlayer):
        dm = await ensure_dm(hunter.member)
        if hunter.power_sealed:
            await dm.send("🔒 A sacred seal binds you. Your final shot is lost.")
            return
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
        try:
            await self.channel.send(
                f"🏛️ **Lykaion begins!** Speed: **{self.speed}** (timer: {self.timer}s)\n"
                f"Players: {', '.join(p.member.mention for p in self.players)}"
            )
            await self.send_roles_in_play()
            await self.dm_roles()
            await self.nemesis_setup_phase()

            night_victims = await self.night_phase()

            while (self.winner() is None) and (not self.ended):
                await self.day_phase(night_victims)
                if self.winner() is not None or self.ended:
                    break

                # IMPORTANT: increment night counter BEFORE next night runs
                self.night_no += 1
                night_victims = await self.night_phase()

            win_side = self.winner_side()
            win = self.winner() or "No one"
            await self.channel.send(f"🏆 **Game over! Winner: {win}**")
            await self.cog.record_win(win_side)
            await self.pay_sponsored_reward()
            await self.send_game_summary(win)
        finally:
            self._is_night = False
            try:
                await asyncio.shield(self.restore_chat_permissions())
            except Exception:
                pass

    def winning_members(self) -> List[discord.Member]:
        winning_side = self.winner_side()
        alive_count = sum(1 for p in self.players if p.alive)
        limos_win = self._limos_has_won()
        winners: List[discord.Member] = []

        for p in self.players:
            won = False
            if p.member.id in self.neutral_winners:
                won = True
            elif limos_win:
                won = p.role == LKRole.LIMOS
            elif winning_side in {SIDE_WOLVES, SIDE_VILLAGE}:
                won = role_side(p.role) == winning_side
            elif winning_side == SIDE_NEUTRAL:
                won = role_side(p.role) == SIDE_NEUTRAL
            elif winning_side is None:
                won = alive_count == 1 and p.alive
            if won:
                winners.append(p.member)
        return winners

    async def pay_sponsored_reward(self):
        if self.reward <= 0:
            return
        winners = self.winning_members()
        if not winners:
            return
        split = self.reward // len(winners)
        remainder = self.reward % len(winners)
        async with self.bot.pool.acquire() as conn:
            for index, member in enumerate(winners):
                payout = split + (remainder if index == 0 else 0)
                if payout > 0:
                    await conn.execute(
                        'UPDATE profile SET "money"="money"+$1 WHERE "user"=$2;',
                        payout,
                        member.id,
                    )
        await self.channel.send(
            f"💰 Sponsored Lykaion reward paid: **${self.reward}** split between {len(winners)} winner(s)."
        )

    async def send_game_summary(self, winner_text: str):
        winning_side = self.winner_side()
        alive_count = sum(1 for p in self.players if p.alive)
        limos_win = self._limos_has_won()
        lines = [f"📜 **Game Summary**", f"Winner: **{winner_text}**", "", "**Players:**"]

        for p in self.players:
            role_name = role_display_name(p.role)
            if p.initial_role and p.initial_role != p.role:
                initial_name = role_display_name(p.initial_role)
                role_name = f"{role_name} (started as {initial_name})"

            won = False
            if p.member.id in self.neutral_winners:
                won = True
            elif limos_win:
                won = p.role == LKRole.LIMOS
            elif winning_side in {SIDE_WOLVES, SIDE_VILLAGE}:
                won = role_side(p.role) == winning_side
            elif winning_side == SIDE_NEUTRAL:
                won = role_side(p.role) == SIDE_NEUTRAL
            elif winning_side is None:
                won = alive_count == 1 and p.alive

            state = "Alive" if p.alive else "Dead"
            result = "Won" if won else "Lost"
            lines.append(
                f"• {p.member.display_name}: **{role_name}** ({state}) — {result}"
            )

        chunk = ""
        for line in lines:
            candidate = f"{chunk}\n{line}" if chunk else line
            if len(candidate) > 1900:
                await self.channel.send(chunk)
                chunk = line
            else:
                chunk = candidate
        if chunk:
            await self.channel.send(chunk)

# ============================================================
# Cog
# ============================================================

class Lykaion(commands.Cog):
    """
    Greekified Werewolf mini-game: Lykaion
    - Join/Leave/Begin lobby with buttons (2 minutes auto-start)
    - Night actions in DMs using buttons
    - Day vote is PUBLIC in the game channel (buttons first, dropdown fallback)
      and the bot acknowledges locked votes in-channel.
    - At night, player speak/reactions are locked; dead players stay locked until game end.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.active_games: Dict[int, Dict[str, object]] = {}  # channel_id -> {"game": LykaionGame, "task": asyncio.Task}
        self.lobbies: Dict[int, Dict[str, object]] = {}       # channel_id -> lobby dict
        self._stats_lock = asyncio.Lock()
        self.win_stats = self._load_win_stats()

    def _is_gm(self, member: discord.Member) -> bool:
        return member.guild_permissions.manage_guild or member.guild_permissions.administrator

    def _default_win_stats(self) -> Dict[str, int]:
        return {
            "wolves_wins": 0,
            "polis_wins": 0,
        }

    def _load_win_stats(self) -> Dict[str, int]:
        stats = self._default_win_stats()
        if not LYKAION_STATS_FILE.exists():
            return stats

        try:
            payload = json.loads(LYKAION_STATS_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return stats

        if not isinstance(payload, dict):
            return stats

        for key in stats:
            value = payload.get(key, stats[key])
            stats[key] = value if isinstance(value, int) and value >= 0 else stats[key]
        return stats

    def _save_win_stats(self):
        try:
            LYKAION_STATS_FILE.write_text(
                json.dumps(self.win_stats, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except OSError:
            pass

    async def record_win(self, winner_side: Optional[str]):
        stat_key = None
        if winner_side == SIDE_WOLVES:
            stat_key = "wolves_wins"
        elif winner_side == SIDE_VILLAGE:
            stat_key = "polis_wins"

        if stat_key is None:
            return

        async with self._stats_lock:
            self.win_stats[stat_key] = self.win_stats.get(stat_key, 0) + 1
            self._save_win_stats()

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

    @commands.group(name="lykaion", aliases=["lyk"], invoke_without_command=True)
    async def lykaion(self, ctx: commands.Context):
        await self.lykaion_help(ctx)

    @lykaion.command(name="help")
    async def lykaion_help(self, ctx: commands.Context):
        p = ctx.clean_prefix
        await ctx.send(
            "**🏛️ Lykaion Help**\n\n"
            f"**Start a lobby:** `{p}lykaion start [extended|normal|fast|blitz]` or `{p}lyk start ...`\n"
            "• A 5-minute lobby appears with **Join / Leave / Begin Now** buttons.\n"
            "• Auto-starts after 2 minutes.\n\n"
            f"**Stop a game (GM):** `{p}lykaion stop`\n"
            f"**Roles list:** `{p}lkroles` or `{p}lyk roles`\n"
            f"**Win record:** `{p}lkstats` or `{p}lyk stats`\n\n"
            "**Modes / Speeds**\n"
            "• `extended` (90s timers) — slower, more talk\n"
            "• `normal` (60s) — default\n"
            "• `fast` (45s) — quick games\n"
            "• `blitz` (30s) — chaos\n\n"
            "**Greek Roles & Powers**\n"
            "🎖️ **Demarchos (Role)** — during daytime votes, the Demarchos vote counts **double** "
            "while more than 3 players are alive.\n"
            "📨 **Hermes’ Messenger** — one random non-wolf (usually) can send **one anonymous DM** to a living player.\n"
            "📢 **Kērux** — once per game, send **one anonymous public proclamation** in the channel.\n"
            "🧵 **Ariadnē** — Night 1 ties two players; if one dies, the other **loses their vote next day**. "
            "If she ties **two wolves**, she becomes a wolf.\n"
            "💘 **Eros** — Night 1 links two lovers; if one dies, the other dies too.\n"
            "🌒 **Kataratos** — if wolves target them at night, they transform into a Lykaon.\n"
            "🎭 **Apatē** — wolf-side deceiver who appears as a Polites to Mantis.\n\n"
            "🧵 **Moirai / Hoplites / Nomophylax / Skia / Mainas / Limos / Nemesis / Moros** are also available.\n"
            f"Use `{p}lkroles` or `{p}lyk roles` for full details.\n\n"
            "**Voting (Public)**\n"
            "Day vote happens in the **game channel**.\n"
            "✅ Use the **vote buttons** to choose a target.\n"
            "🕊️ Pick **Abstain** to skip.\n"
            "If there are too many options for buttons, it switches to a dropdown menu.\n"
            "Your **first** valid vote is final and cannot be changed."
        )

    def _roles_embeds(self) -> List[discord.Embed]:
        per_page = 4
        roles = list(LKRole)
        total_pages = max(1, (len(roles) + per_page - 1) // per_page)
        pages: List[discord.Embed] = []

        for page_idx in range(total_pages):
            start = page_idx * per_page
            end = start + per_page
            page_roles = roles[start:end]

            e = discord.Embed(
                title="🏛️ Lykaion Roles",
                description="Greek role reference for the current Lykaion game.",
                color=discord.Color.gold(),
            )
            for role in page_roles:
                role_name = role_display_name(role)
                e.add_field(name=role_name, value=ROLE_DESCRIPTIONS[role], inline=False)
            e.set_footer(text=f"Page {page_idx + 1}/{total_pages}")
            pages.append(e)

        return pages

    async def _send_roles_pages(self, ctx: commands.Context):
        pages = self._roles_embeds()
        if not pages:
            await ctx.send("No Lykaion roles are configured.")
            return

        view = RolesPagerView(pages, timeout=180)
        msg = await ctx.send(embed=pages[0], view=view)
        view.message = msg

    def _win_stats_embed(self) -> discord.Embed:
        wolves_wins = self.win_stats.get("wolves_wins", 0)
        polis_wins = self.win_stats.get("polis_wins", 0)
        counted_games = wolves_wins + polis_wins

        embed = discord.Embed(
            title="🏛️ Lykaion Win Record",
            description="Completed Lykaion games counted by winning side.",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Lykaones (WW)", value=str(wolves_wins), inline=True)
        embed.add_field(name="Polis", value=str(polis_wins), inline=True)
        embed.add_field(name="Counted Games", value=str(counted_games), inline=True)
        embed.set_footer(text="Neutral / solo wins are not added to this counter.")
        return embed

    @commands.command(name="lkroles")
    async def lkroles(self, ctx: commands.Context):
        await self._send_roles_pages(ctx)

    @commands.command(name="lkstats")
    async def lkstats(self, ctx: commands.Context):
        await ctx.send(embed=self._win_stats_embed())

    @lykaion.command(name="roles")
    async def lykaion_roles(self, ctx: commands.Context):
        await self._send_roles_pages(ctx)

    @lykaion.command(name="stats")
    async def lykaion_stats(self, ctx: commands.Context):
        await ctx.send(embed=self._win_stats_embed())

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

        game = LykaionGame(
            self.bot,
            self,
            ctx.channel,
            players,
            speed=speed,
            reward=getattr(ctx, "scheduled_reward", 0),
        )
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
        await game.restore_chat_permissions()
        task.cancel()
        self.active_games.pop(ctx.channel.id, None)
        await ctx.send("🛑 Lykaion stopped by GM.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Lykaion(bot))
