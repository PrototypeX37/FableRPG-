



from datetime import datetime, timezone
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
import datetime as dt
import math
import random
from dataclasses import dataclass, field
from typing import Dict, Tuple, Optional, List, Iterable

import discord
from discord.ext import commands
from discord import ButtonStyle
from discord.ui import View, Button

# =============================
# ======= CONFIG / META =======
# =============================

ALL_CLASSES = {"Mage","Paragon","Raider","Ranger","Ritualist","Thief","Warrior","Paladin","Reaper","SantasHelper","Tank"}
TANK_CLASSES = {"Tank", "Paladin"}
RANGER_CLASSES = {"Ranger"}
MAGE_CLASSES = {"Mage"}
RAIDER_SURVIVE_CLASSES = {"Raider"}
ALLOWED_RARITIES = {"magic","legendary","materials","rare","uncommon","common","mystery","fortune","divine"}

RARITY_FALLBACK_EMOTES = {
    "magic": "🔮",
    "legendary": "🟨",
    "rare": "🔵",
    "uncommon": "🟢",
    "common": "⚪",
    "mystery": "❔",
    "fortune": "💰",
    "divine": "✨",
}

# Art
CHARYBDIS_START_IMG = "https://i.imgur.com/qxdKLKx.png"
CHARYBDIS_ATTACK_IMG = "https://i.imgur.com/OM8iU70.png"
ECHIDNA_START_IMG   = "https://i.imgur.com/3H9hzUl.png"
ECHIDNA_ATTACK_IMG  = "https://i.imgur.com/ClADfsE.png"
DEATH_START_IMG     = "https://i.imgur.com/bL4uD86.png"
DEATH_ATTACK_IMG    = "https://i.imgur.com/bL4uD86.png"
IMG_RAIDERS_ATTACK  = "https://i.imgur.com/7AsK58N.png"

# Summary channel
RAIDS_SUMMARY_CHANNEL_ID = 1404885965813842021
DEATH_RAID_BLOOMSHARDS_MIN = 10
DEATH_RAID_BLOOMSHARDS_MAX = 55

# Decorators (fallbacks for local testing)
try:
    from cogs.utils.checks import is_gm, raid_channel  # adjust path if needed
except Exception:
    def is_gm():
        def deco(f):
            return f
        return deco
    def raid_channel():
        def deco(f):
            return f
        return deco

# =============================
# ====== DATA STRUCTURES ======
# =============================

@dataclass
class RaiderState:
    hp: int
    armor: int
    dps: int
    classes: Tuple[str, ...] = field(default_factory=tuple)
    race: Optional[str] = None
    alive: bool = True
    total_damage_taken: int = 0           # post-mitigation
    total_damage_pressure: int = 0        # pre-mitigation (raw)
    total_damage_dealt: int = 0
    dmg_to_boss: int = 0
    dmg_to_adds: int = 0

@dataclass
class Add:
    name: str
    hp: int
    min_dmg: int
    max_dmg: int
    armor: int = 0

ECHIDNA_BABIES: List[Tuple[str, int, int]] = [
    ("Cerberus Pup", 350, 700),
    ("Hydra Spawn", 300, 750),
    ("Chimera Cub", 380, 800),
    ("Nemean Whelp", 420, 820),
    ("Sphinxling", 340, 720),
]

# =============================
# ======= JOIN VIEW ===========
# =============================

class JoinView(View):
    def __init__(self, *, timeout: int = 60 * 15):
        super().__init__(timeout=timeout)
        self.joined: List[discord.User] = []
        self.add_item(JoinButton(self))

class JoinButton(Button):
    def __init__(self, view: JoinView):
        super().__init__(style=ButtonStyle.primary, label="Join the raid!")
        self.view_ref = view

    async def callback(self, interaction: discord.Interaction):
        user = interaction.user
        if user not in self.view_ref.joined:
            self.view_ref.joined.append(user)
            await interaction.response.send_message("You joined the raid.", ephemeral=True)
        else:
            await interaction.response.send_message("Already joined.", ephemeral=True)

# =============================
# ======= BOSS BEHAVIORS ======
# =============================

class BossBehavior:
    name: str = "Boss"
    min_dmg: int = 250
    max_dmg: int = 900
    start_img: str = CHARYBDIS_ATTACK_IMG
    attack_img: str = CHARYBDIS_ATTACK_IMG

    async def on_tick(self, engine: "RaidEngine"):
        pass

    async def boss_attack(self, engine: "RaidEngine") -> List[discord.Embed]:
        alive = [(k, v) for k, v in engine.raiders.items() if v.alive]
        if not alive:
            return []
        target, rst = random.choice(alive)
        raw = random.randint(self.min_dmg, self.max_dmg)
        em = discord.Embed(title=f"{self.name} attacked!", colour=0xFFB900)
        em.set_thumbnail(url=self.attack_img)
        _, death_em, _ = engine.apply_damage(target, rst, raw, source=self.name, container_embed=em)
        em.add_field(name="Boss HP", value=engine.boss_hp_bar(), inline=False)
        return [e for e in (em, death_em) if e]

class CharybdisBehavior(BossBehavior):
    name = "Charybdis"
    min_dmg = 400
    max_dmg = 1100
    tank_bonus_mult = 1.8
    whirlpool_every_turns = 5
    start_img = CHARYBDIS_START_IMG
    attack_img = CHARYBDIS_ATTACK_IMG

    def _pick_target(self, engine: "RaidEngine") -> Optional[Tuple[discord.User, RaiderState]]:
        """Bias away from tanks/paladins: prefer any non-tank alive; fallback to any alive."""
        alive = [(k, v) for k, v in engine.raiders.items() if v.alive]
        if not alive:
            return None
        non_tanks = [(k, v) for k, v in alive if not any(c in TANK_CLASSES for c in v.classes)]
        pool = non_tanks if non_tanks else alive
        return random.choice(pool)

    async def boss_attack(self, engine: "RaidEngine") -> List[discord.Embed]:
        tgt = self._pick_target(engine)
        if not tgt:
            return []
        user, rst = tgt
        raw = random.randint(self.min_dmg, self.max_dmg)
        em = discord.Embed(title=f"{self.name} lashed out!", colour=0xFFB900)
        em.set_thumbnail(url=self.attack_img)
        _, death_em, _ = engine.apply_damage(user, rst, raw, source=self.name, container_embed=em)
        em.add_field(name="Boss HP", value=engine.boss_hp_bar(), inline=False)
        out = [em]
        if death_em:
            out.append(death_em)
        return out

    async def on_tick(self, engine: "RaidEngine"):
        # Whirlpool every N player phases
        if engine.player_turn_count > 0 and engine.player_turn_count % self.whirlpool_every_turns == 0:
            await engine.send_embeds(await self.whirlpool_aoe(engine))

    async def whirlpool_aoe(self, engine: "RaidEngine") -> List[discord.Embed]:
        base = random.randint(self.min_dmg, self.max_dmg)
        em = discord.Embed(title=f"{self.name} unleashes a whirlpool!", colour=0x0055FF)
        em.set_thumbnail(url=self.attack_img)
        out: List[discord.Embed] = [em]
        for user, rst in engine.raiders.items():
            if not rst.alive:
                continue
            mult = self.tank_bonus_mult if any(c in TANK_CLASSES for c in rst.classes) else 1.0
            raw = int(base * mult)
            _, death_em, _ = engine.apply_damage(user, rst, raw, source=self.name, container_embed=em)
            if death_em:
                out.append(death_em)
        em.add_field(name="Boss HP", value=engine.boss_hp_bar(), inline=False)
        return out

class EchidnaBehavior(BossBehavior):
    name = "Echidna"
    min_dmg = 260
    max_dmg = 920
    start_img = ECHIDNA_START_IMG
    attack_img = ECHIDNA_ATTACK_IMG
    add_min_hp = 900
    add_max_hp = 1800
    add_spawn_every = 3
    max_concurrent_adds = 6

    async def on_tick(self, engine: "RaidEngine"):
        if engine.tick_count % self.add_spawn_every == 0:
            await self.maybe_spawn_adds(engine)
        if engine.adds:
            await engine.adds_attack(source_boss=self.name)

    async def maybe_spawn_adds(self, engine: "RaidEngine"):
        engine.adds = [a for a in engine.adds if a.hp > 0]
        while len(engine.adds) < self.max_concurrent_adds:
            hp = random.randint(self.add_min_hp, self.add_max_hp)
            nm, mn, mx = random.choice(ECHIDNA_BABIES)
            engine.adds.append(Add(name=nm, hp=hp, min_dmg=mn, max_dmg=mx))
        if engine.adds:
            em = discord.Embed(title=f"{self.name} summons offspring!", colour=0x66AA33)
            em.description = ", ".join(f"{ad.name} (HP {ad.hp})" for ad in engine.adds)
            await engine.send_embeds([em])


class DeathBehavior(BossBehavior):
    name = "Death"
    min_dmg = 300
    max_dmg = 850
    start_img = DEATH_START_IMG
    attack_img = DEATH_ATTACK_IMG
    reap_every_turns = 4
    armor_ignore_pct = 0.35
    marked_true_damage_pct = 0.10
    marked_rounds_per_target = 4
    retarget_delay_rounds = 1

    def __init__(self):
        self.marked_target_id: Optional[int] = None
        self.marked_rounds_left: int = 0
        self.retarget_cooldown_rounds: int = 0

    def _find_marked_alive_target(
        self, engine: "RaidEngine"
    ) -> Tuple[Optional[discord.User], Optional[RaiderState]]:
        if self.marked_target_id is None:
            return None, None
        for user, rst in engine.raiders.items():
            if user.id == self.marked_target_id and rst.alive:
                return user, rst

        # Marked target no longer alive/in raid.
        self.marked_target_id = None
        self.marked_rounds_left = 0
        return None, None

    def _resolve_attack_target(
        self, engine: "RaidEngine"
    ) -> Tuple[Optional[discord.User], Optional[RaiderState], bool, bool]:
        # returns: (target, state, newly_marked, marked_active_this_round)
        alive = [(u, r) for u, r in engine.raiders.items() if r.alive]
        if not alive:
            return None, None, False, False

        marked_user, marked_rst = self._find_marked_alive_target(engine)
        if marked_user and marked_rst:
            return marked_user, marked_rst, False, True

        # Cooldown round between marks after a survivor endured all marked rounds.
        if self.retarget_cooldown_rounds > 0:
            self.retarget_cooldown_rounds -= 1
            user, rst = random.choice(alive)
            return user, rst, False, False

        # Apply a fresh mark.
        user, rst = random.choice(alive)
        self.marked_target_id = user.id
        self.marked_rounds_left = self.marked_rounds_per_target
        return user, rst, True, True

    async def boss_attack(self, engine: "RaidEngine") -> List[discord.Embed]:
        target, rst, newly_marked, marked_active = self._resolve_attack_target(engine)
        if not target or not rst:
            return []

        base_raw = random.randint(self.min_dmg, self.max_dmg)
        armor_ignored = int(rst.armor * self.armor_ignore_pct)
        adjusted_raw = base_raw + armor_ignored

        em = discord.Embed(title=f"{self.name} strikes with inevitability.", colour=0x6A0DAD)
        em.set_thumbnail(url=self.attack_img)
        _, death_em, _ = engine.apply_damage(
            target,
            rst,
            adjusted_raw,
            source=self.name,
            container_embed=em,
        )

        true_death_em = None
        true_damage = 0
        if marked_active and rst.alive:
            true_damage = max(1, int(round(base_raw * self.marked_true_damage_pct)))
            _, true_death_em, _ = engine.apply_damage(
                target,
                rst,
                true_damage,
                source=f"{self.name} (Marked by Death)",
                container_embed=em,
                ignore_armor=True,
            )

        if newly_marked:
            em.add_field(
                name="Marked by Death",
                value=(
                    f"{target.mention} is marked for **{self.marked_rounds_per_target} rounds**. "
                    "If they survive, Death releases them and marks a new target after one round."
                ),
                inline=False,
            )

        if marked_active:
            if not rst.alive:
                self.marked_target_id = None
                self.marked_rounds_left = 0
            else:
                self.marked_rounds_left = max(0, self.marked_rounds_left - 1)
                if self.marked_rounds_left <= 0:
                    self.marked_target_id = None
                    self.retarget_cooldown_rounds = self.retarget_delay_rounds
                    em.add_field(
                        name="Mark Broken",
                        value=(
                            f"{target.mention} endured the mark for "
                            f"**{self.marked_rounds_per_target} rounds**. "
                            "Death releases them and will mark a new target after 1 round."
                        ),
                        inline=False,
                    )
                else:
                    em.add_field(
                        name="Mark Duration",
                        value=f"Rounds remaining on {target.mention}: **{self.marked_rounds_left}**",
                        inline=False,
                    )
        else:
            em.add_field(
                name="Mark Cooldown",
                value="Death is choosing a new marked target next round.",
                inline=False,
            )

        em.add_field(
            name="Reaper's Edge",
            value=f"Ignored **{armor_ignored:,}** armor on {target.mention}.",
            inline=False,
        )
        em.add_field(
            name="Marked Bonus",
            value=(
                (
                    f"True damage to {target.mention}: **{true_damage:,}** "
                    f"({int(self.marked_true_damage_pct * 100)}%)."
                )
                if true_damage > 0 else (
                    f"{target.mention} was downed before true damage could trigger."
                    if marked_active
                    else "No marked true damage this round."
                )
            ),
            inline=False,
        )
        em.add_field(name="Boss HP", value=engine.boss_hp_bar(), inline=False)
        return [e for e in (em, death_em, true_death_em) if e]

    async def on_tick(self, engine: "RaidEngine"):
        if engine.player_turn_count > 0 and engine.player_turn_count % self.reap_every_turns == 0:
            await engine.send_embeds(await self.reaping_wave(engine))

    async def reaping_wave(self, engine: "RaidEngine") -> List[discord.Embed]:
        alive_targets = [(k, v) for k, v in engine.raiders.items() if v.alive]
        if not alive_targets:
            return []

        base = random.randint(self.min_dmg, self.max_dmg)
        em = discord.Embed(
            title=f"{self.name} unleashes Reaping Wave!",
            description="A cold arc sweeps through the raid line.",
            colour=0x3D0C6B,
        )
        em.set_thumbnail(url=self.attack_img)
        out: List[discord.Embed] = [em]

        for user, rst in alive_targets:
            armor_ignored = int(rst.armor * self.armor_ignore_pct)
            adjusted_raw = base + armor_ignored
            _, death_em, _ = engine.apply_damage(
                user,
                rst,
                adjusted_raw,
                source=f"{self.name} (Reaping Wave)",
                container_embed=em,
            )
            if death_em:
                out.append(death_em)

        em.add_field(
            name="Wave Effect",
            value=f"Armor penetration: **{int(self.armor_ignore_pct * 100)}%**",
            inline=False,
        )
        em.add_field(name="Boss HP", value=engine.boss_hp_bar(), inline=False)
        return out

# =============================
# ========= ENGINE ============
# =============================

class RaidEngine:
    def __init__(self, bot: commands.Bot, ctx: commands.Context, *, boss_name: str, boss_hp: int, behavior: BossBehavior, rarity: str = "magic", duration_minutes: int = 60):
        self.bot = bot
        self.ctx = ctx
        self.behavior = behavior
        self.boss = {"name": boss_name, "hp": boss_hp, "initial_hp": boss_hp}
        self.rarity = rarity
        self.duration = dt.timedelta(minutes=duration_minutes)
        self.raiders: Dict[discord.User, RaiderState] = {}
        self.adds: List[Add] = []
        self.tick_count: int = 0
        self.player_turn_count: int = 0
        self.started_at = dt.datetime.now(timezone.utc)
        self.survival_used: set[int] = set()
        self._locked = False

        # Summary bookkeeping
        self.participants_count: int = 0
        self.payout_per: Optional[int] = None
        self.crate_line: Optional[str] = None
        self.spring_bloomshards_line: Optional[str] = None
        self.victory: Optional[bool] = None
        self.talos_auction: Optional[Tuple[int, int]] = None  # (winner_id, amount) reserved for similar flows

    # ---------- UTIL ----------
    def rarity_emote(self) -> str:
        try:
            crates = self.bot.cogs.get('Crates')
            if crates:
                emotes = getattr(crates, 'emotes', None)
                if emotes and hasattr(emotes, self.rarity):
                    return getattr(emotes, self.rarity)
        except Exception:
            pass
        return RARITY_FALLBACK_EMOTES.get(self.rarity, "🎁")

    def boss_hp_bar(self, width: int = 20) -> str:
        cur = max(0, int(self.boss["hp"]))
        mx = max(1, int(self.boss["initial_hp"]))
        ratio = max(0.0, min(1.0, cur / mx))
        filled = int(round(ratio * width))
        bar = "█" * filled + "░" * (width - filled)
        pct = int(ratio * 100)
        return f"`{bar}` {pct}% ({cur:,}/{mx:,})"

    async def send_embeds(self, embeds: Iterable[discord.Embed]):
        for em in embeds:
            await self.ctx.send(embed=em)

    async def lock_channel(self):
        if self._locked:
            return
        channel: discord.TextChannel = self.ctx.channel
        try:
            await channel.set_permissions(self.ctx.guild.default_role, send_messages=False)
            await channel.set_permissions(self.ctx.author, send_messages=True)
            self._locked = True
        except Exception:
            pass

    async def unlock_channel(self):
        if not self._locked:
            return
        channel: discord.TextChannel = self.ctx.channel
        try:
            await channel.set_permissions(self.ctx.guild.default_role, send_messages=None)
            await channel.set_permissions(self.ctx.author, send_messages=None)
        except Exception:
            pass
        self._locked = False

    # Centralized damage application (tracks pre- & post-mitigation, sends death embeds)
    def apply_damage(
        self,
        user: discord.User,
        rst: RaiderState,
        raw: int,
        *,
        source: str,
        container_embed: Optional[discord.Embed] = None,
        ignore_armor: bool = False,
    ):
        incoming = max(1, int(raw))
        eff = incoming if ignore_armor else max(1, incoming - rst.armor)
        rst.hp -= eff
        rst.total_damage_taken += eff
        rst.total_damage_pressure += incoming
        survived = False
        death_em: Optional[discord.Embed] = None

        if rst.hp > 0:
            if container_embed:
                container_embed.add_field(
                    name=f"Hit: {user.display_name}",
                    value=f"HP now **{rst.hp}** (took {eff:,}, total {rst.total_damage_taken:,})",
                    inline=False,
                )
            return eff, None, survived

        # one-time Raider survival
        if user.id not in self.survival_used and any(c in RAIDER_SURVIVE_CLASSES for c in rst.classes):
            rst.hp = 1
            self.survival_used.add(user.id)
            survived = True
            if container_embed:
                container_embed.add_field(name="💫 Raider Survival", value=f"{user.mention} clings to **1 HP**!", inline=False)
            return eff, None, survived

        # Dead — mark & craft distinct embed
        rst.alive = False
        if container_embed:
            container_embed.add_field(name="☠️ Downed", value=f"{user.mention} was killed by **{source}**.", inline=False)
        death_em = discord.Embed(title="⚠️ Raider Down", description=f"{user.mention} was slain by **{source}**.", colour=0xB00020)
        try:
            death_em.set_author(name=str(user), icon_url=user.display_avatar.url)
        except Exception:
            pass
        death_em.add_field(name="Final HP", value=str(max(0, rst.hp)))
        return eff, death_em, survived

    # --------- ENROLLMENT ---------
    async def enroll(self, users: List[discord.User]):
        async with self.bot.pool.acquire() as conn:
            for u in users:
                profile = await conn.fetchrow('SELECT * FROM profile WHERE "user"=$1;', u.id)
                if not profile:
                    continue
                dmg, deff = await self.bot.get_raidstats(
                    u,
                    atkmultiply=profile.get("atkmultiply"),
                    defmultiply=profile.get("defmultiply"),
                    classes=profile.get("class"),
                    race=profile.get("race"),
                    guild=profile.get("guild"),
                    xp=profile.get("xp"),
                    conn=conn,
                )
                level = self._xptolevel(profile.get("xp", 0))
                stathp = int(profile.get("stathp", 0)) * 50
                basehp = int(profile.get("health", 0)) + 250 + (level * 5) + stathp
                self.raiders[u] = RaiderState(
                    hp=int(basehp),
                    armor=int(deff),
                    dps=int(dmg),
                    classes=tuple(profile.get("class") or ()),
                    race=profile.get("race")
                )

    @staticmethod
    def _xptolevel(xp: int) -> int:
        return int((xp or 0) ** 0.5 // 10)

    # --------- TICK LOOP ---------
    async def run(self):
        start = dt.datetime.now(timezone.utc)
        # Opening banner
        em = discord.Embed(
            title=f"{self.boss['name']} Spawned",
            description=f"This boss has {self.boss['hp']:,} HP.",
            colour=0x3399FF
        )
        em.set_image(url=getattr(self.behavior, 'start_img', CHARYBDIS_ATTACK_IMG))
        await self.ctx.send(embed=em)

        await self.lock_channel()

        while self.boss["hp"] > 0 and any(r.alive for r in self.raiders.values()) and dt.datetime.now(timezone.utc) < start + self.duration:
            self.tick_count += 1

            # Boss action(s)
            await self.send_embeds(await self.behavior.boss_attack(self))

            # Player phase
            alive_attackers = [(u, r) for u, r in self.raiders.items() if r.alive]
            if alive_attackers:
                boss_damage = 0
                if isinstance(self.behavior, EchidnaBehavior) and self.adds:
                    # Bypassers hit boss; others hit adds
                    bypass = [(u, r) for u, r in alive_attackers if (set(r.classes) & (RANGER_CLASSES | MAGE_CLASSES))]
                    non_bypass = [(u, r) for u, r in alive_attackers if not (set(r.classes) & (RANGER_CLASSES | MAGE_CLASSES))]

                    for u, r in bypass:
                        r.total_damage_dealt += r.dps
                        r.dmg_to_boss += r.dps
                        boss_damage += r.dps

                    killed_adds: List[str] = []
                    for u, r in non_bypass:
                        remaining = r.dps
                        i = 0
                        while remaining > 0 and i < len(self.adds):
                            ad = self.adds[i]
                            if ad.hp <= 0:
                                i += 1
                                continue
                            hit = min(remaining, ad.hp)
                            ad.hp -= hit
                            r.total_damage_dealt += hit
                            r.dmg_to_adds += hit
                            remaining -= hit
                            if ad.hp <= 0:
                                killed_adds.append(ad.name)
                                i += 1
                        if remaining > 0:
                            boss_damage += remaining
                            r.total_damage_dealt += remaining
                            r.dmg_to_boss += remaining

                    self.adds = [ad for ad in self.adds if ad.hp > 0]
                    em_adds = discord.Embed(title=f"Raid engages Echidna's offspring", colour=0x66AA33)
                    em_adds.set_thumbnail(url=ECHIDNA_ATTACK_IMG)
                    if killed_adds:
                        em_adds.add_field(name="☠️ Offspring slain", value=", ".join(killed_adds), inline=False)
                    em_adds.description = ", ".join(f"{ad.name} (HP {ad.hp})" for ad in self.adds) if self.adds else "All offspring defeated!"
                    em_adds.add_field(name="Boss HP", value=self.boss_hp_bar(), inline=False)
                    await self.send_embeds([em_adds])
                else:
                    for u, r in alive_attackers:
                        boss_damage += r.dps
                        r.total_damage_dealt += r.dps
                        r.dmg_to_boss += r.dps

                self.boss["hp"] -= boss_damage
                strike = discord.Embed(title=f"Raid strikes {self.boss['name']}!", colour=0xFF5C00)
                strike.set_thumbnail(url=IMG_RAIDERS_ATTACK)
                strike.add_field(name="Damage", value=boss_damage)
                strike.add_field(name="HP left", value=self.boss["hp"] if self.boss["hp"] > 0 else "Dead!")
                strike.add_field(name="Boss HP", value=self.boss_hp_bar(), inline=False)

                # Echidna featured attacker: random among survivors each player phase
                if isinstance(self.behavior, EchidnaBehavior) and alive_attackers:
                    featured_user, fr = random.choice(alive_attackers)
                    try:
                        strike.set_author(name=str(featured_user), icon_url=featured_user.display_avatar.url)
                    except Exception:
                        pass
                    strike.add_field(
                        name="Featured hero total dmg",
                        value=f"{fr.total_damage_dealt:,} (Boss {fr.dmg_to_boss:,} / Adds {fr.dmg_to_adds:,})",
                        inline=False
                    )

                await self.send_embeds([strike])
                self.player_turn_count += 1

            await self.behavior.on_tick(self)
            await asyncio.sleep(4)

        if self.boss["hp"] <= 0:
            self.victory = True
            await self._victory()
        else:
            self.victory = False
            await self._defeat()

    # --- Echidna adds attack helper ---
    async def adds_attack(self, *, source_boss: str) -> None:
        """Each living add attacks a random living raider."""
        if not self.adds:
            return
        alive_roster = [(u, r) for u, r in self.raiders.items() if r.alive]
        if not alive_roster:
            return

        em = discord.Embed(title=f"{source_boss}'s offspring attack!", colour=0x2E7D32)
        try:
            em.set_thumbnail(url=ECHIDNA_ATTACK_IMG)
        except Exception:
            pass
        out: List[discord.Embed] = [em]

        for ad in self.adds:
            if ad.hp <= 0:
                continue
            alive_roster = [(u, r) for u, r in self.raiders.items() if r.alive]
            if not alive_roster:
                break
            target, rst = random.choice(alive_roster)
            raw = random.randint(ad.min_dmg, ad.max_dmg)
            _, death_em, _ = self.apply_damage(target, rst, raw, source=ad.name, container_embed=em)
            if death_em:
                out.append(death_em)

        em.add_field(name="Boss HP", value=self.boss_hp_bar(), inline=False)
        await self.send_embeds(out)

    # --------- ENDING / REWARDS ---------
    async def _victory(self):
        # Unlock early except when another flow needs open channel; for Charybdis we’ll reopen during auction
        await self.unlock_channel()
        duration = dt.datetime.now(timezone.utc) - self.started_at
        minutes, seconds = (duration.seconds % 3600) // 60, duration.seconds % 60
        em = discord.Embed(title=f"{self.boss['name']} defeated!", description=f"Time: {minutes}m {seconds}s", colour=0x22CC88)
        await self.send_embeds([em])

        if isinstance(self.behavior, CharybdisBehavior):
            await self._rewards_charybdis()
        elif isinstance(self.behavior, EchidnaBehavior):
            await self._rewards_echidna()
        else:
            await self._rewards_default()

        await self._send_summary()

    async def _defeat(self):
        await self.unlock_channel()
        # Flavor per boss
        if isinstance(self.behavior, CharybdisBehavior):
            text = "You couldn't slay Charybdis in time — the seas calm as the maw slips beneath the waves."
        elif isinstance(self.behavior, EchidnaBehavior):
            text = "You couldn't slay Echidna in time — her brood swallows the path as she retreats into the dark."
        else:
            text = f"You couldn't slay {self.boss['name']} in time — it escapes."
        em = discord.Embed(title="Raid failed (timeout)", description=text, colour=0xAA2222)
        em.add_field(name="HP remaining", value=f"{self.boss['hp']:,}")
        await self.send_embeds([em])
        await self._send_summary()

    async def _rewards_default(self):
        cash_pool = int(self.boss["initial_hp"] * 0.5)
        users_all = [u for u in self.raiders.keys()]
        per = max(1, cash_pool // max(1, len(users_all)))
        await self._mass_add_money(users_all, per)
        self.payout_per = per
        survivors = [u for u, r in self.raiders.items() if r.alive]
        winner = random.choice(survivors) if survivors else None
        if winner:
            await self._give_crate(winner)
            self.crate_line = f"{self.rarity.capitalize()} crate {self.rarity_emote()} awarded to: {winner.mention}"

        if isinstance(self.behavior, DeathBehavior) and survivors:
            amount_each = random.randint(DEATH_RAID_BLOOMSHARDS_MIN, DEATH_RAID_BLOOMSHARDS_MAX)
            awarded = await self._mass_add_bloomshards(survivors, amount_each)
            if awarded > 0:
                total_paid = awarded * amount_each
                self.spring_bloomshards_line = (
                    f"Bloomshards awarded: **+{amount_each}** to **{awarded}** survivors"
                )
                await self.ctx.send(
                    f"🌸 Death's defeat grants **+{amount_each} Bloomshards** to each survivor "
                    f"(**{total_paid}** total)."
                )
            else:
                self.spring_bloomshards_line = (
                    "Bloomshards payout skipped (Dreambound Spring not loaded)."
                )
                await self.ctx.send(
                    "⚠️ Could not award Bloomshards because Dreambound Spring is not loaded."
                )

    async def _auction_crate(self, survivors: List[discord.User]) -> Optional[Tuple[int, int]]:
        """Survivors-only auction for the crate.
        - Pings survivors
        - 2m to get the first bid, then 60s after each bid
        - ≥10% step (or at least +$1)
        - Refunds previous leader automatically
        - Prevents outbidding yourself
        Returns (winner_id, amount) if sold, else None.
        """
        if not survivors:
            await self.ctx.send("No survivors; auction skipped.")
            return None

        # Open channel for bidding
        await self.unlock_channel()

        mentions = ", ".join(m.mention for m in survivors)
        emote = self.rarity_emote()
        await self.ctx.send(
            f"{mentions}\n"
            f"**Auction time!** Highest valid bid wins the {emote} **{self.rarity.capitalize()} Crate**.\n"
            "• Post a whole-number bid in chat.\n"
            "• You **can't outbid yourself**.\n"
            "• First bid must arrive within **2 minutes**.\n"
            "• After each bid, there's **60s** for the next one."
        )

        highest_user_id: Optional[int] = None
        highest_amount: int = 0
        got_any_bid = False

        def check(msg: discord.Message) -> bool:
            return (
                msg.channel.id == self.ctx.channel.id
                and msg.author in survivors
                and msg.content.isdigit()
            )

        # Wait up to 2 minutes for the very first bid
        try:
            msg = await self.bot.wait_for("message", timeout=120, check=check)
        except asyncio.TimeoutError:
            await self.ctx.send("No bids placed. Auction closed.")
            return None

        # Process first bid
        bid = int(msg.content)
        if bid <= 0:
            await self.ctx.send(f"{msg.author.mention} bids must be positive.")
        else:
            async with self.bot.pool.acquire() as conn:
                bal = await conn.fetchval('SELECT money FROM profile WHERE "user"=$1;', msg.author.id)
                if bal is None or bal < bid:
                    await self.ctx.send(f"{msg.author.mention} you don't have enough money.")
                else:
                    await conn.execute('UPDATE profile SET money=money-$1 WHERE "user"=$2;', bid, msg.author.id)
                    highest_user_id, highest_amount = msg.author.id, bid
                    got_any_bid = True
                    next_min = int(math.ceil(max(bid + 1, bid * 1.10)))
                    await self.ctx.send(f"{msg.author.mention} leads at **${bid:,}**! (Next ≥ ${next_min:,})")

        # Rolling 60s windows for subsequent bids
        while True:
            try:
                msg = await self.bot.wait_for("message", timeout=60, check=check)
            except asyncio.TimeoutError:
                break

            # Block outbidding yourself
            if highest_user_id is not None and msg.author.id == highest_user_id:
                await self.ctx.send(f"{msg.author.mention} you're already leading — you can't outbid yourself.")
                continue

            bid = int(msg.content)
            min_next = 1 if highest_amount == 0 else int(math.ceil(max(highest_amount + 1, highest_amount * 1.10)))
            if bid < min_next:
                await self.ctx.send(f"{msg.author.mention} minimum next bid is **${min_next:,}**.")
                continue

            async with self.bot.pool.acquire() as conn:
                bal = await conn.fetchval('SELECT money FROM profile WHERE "user"=$1;', msg.author.id)
                if bal is None or bal < bid:
                    await self.ctx.send(f"{msg.author.mention} you don't have enough money.")
                    continue
                # refund previous leader
                if highest_user_id is not None and highest_amount > 0:
                    await conn.execute('UPDATE profile SET money=money+$1 WHERE "user"=$2;', highest_amount, highest_user_id)
                # hold new leader's bid
                await conn.execute('UPDATE profile SET money=money-$1 WHERE "user"=$2;', bid, msg.author.id)

            highest_user_id, highest_amount = msg.author.id, bid
            got_any_bid = True
            next_min = int(math.ceil(max(bid + 1, bid * 1.10)))
            await self.ctx.send(f"{msg.author.mention} leads at **${bid:,}**! (Next ≥ ${next_min:,})")

        if not got_any_bid or highest_user_id is None:
            await self.ctx.send("No valid bids. Auction closed.")
            return None

        # Winner gets the crate (their bid is already held)
        winner = self.ctx.guild.get_member(highest_user_id) or await self.bot.fetch_user(highest_user_id)
        await self._give_crate(winner)
        self.crate_line = f"{self.rarity.capitalize()} crate {self.rarity_emote()} sold to: <@{highest_user_id}> for **${highest_amount:,}**"
        return (highest_user_id, highest_amount)

    async def _rewards_charybdis(self):
        """Charybdis rewards: everyone paid; survivors get an auction; also show Top-5 by pressure."""
        # Payout to all participants (alive or dead)
        cash_pool = int(self.boss["initial_hp"] * 1.3)
        users_all = [u for u in self.raiders.keys()]
        per = max(1, cash_pool // max(1, len(users_all)))
        await self._mass_add_money(users_all, per)
        self.payout_per = per
        await self.ctx.send(f"Paid ${per:,} to {len(users_all)} participants.")

        # Leaderboard by pressure among survivors (pre-mitigation)
        alive = [(u, r) for u, r in self.raiders.items() if r.alive]
        if alive:
            top5 = sorted(alive, key=lambda t: t[1].total_damage_pressure, reverse=True)[:5]
            lb = discord.Embed(title="Top survivors by damage pressure (pre-mitigation)", colour=0x0055FF)
            lb.description = "\n".join(
                f"**{i+1}.** {u.mention} — {r.total_damage_pressure:,} pressure" for i, (u, r) in enumerate(top5)
            )
            await self.send_embeds([lb])

            # Survivors-only auction for the crate
            survivors_only = [u for u, _ in alive]
            await self._auction_crate(survivors_only)
        else:
            await self.ctx.send("No survivors; auction skipped.")

    async def _rewards_echidna(self):
        survivors = [(u, r) for u, r in self.raiders.items() if r.alive]
        users = [u for u, _ in survivors]
        participants = len(self.raiders)
        cash_pool = int(self.boss["initial_hp"] * 1.2)
        per = max(1, cash_pool // max(1, len(users) or 1))
        if users:
            await self._mass_add_money(users, per)
            self.payout_per = per
            await self.ctx.send(f"Paid ${per:,} to {len(users)} survivors on {participants} participants.")
        else:
            await self.ctx.send(f"No survivors; no payouts (participants: {participants}).")

        if survivors:
            winner_user, winner_rs = max(survivors, key=lambda t: (t[1].dmg_to_boss + t[1].dmg_to_adds))
            total = winner_rs.dmg_to_boss + winner_rs.dmg_to_adds
            await self._give_crate(winner_user)
            self.crate_line = f"{self.rarity.capitalize()} crate {self.rarity_emote()} awarded to: {winner_user.mention}"
            win = discord.Embed(title="MVP — Echidna Slayer", colour=0xCC33AA)
            win.description = (
                f"{winner_user.mention} won with **{total:,}** total damage (Boss {winner_rs.dmg_to_boss:,} / Adds {winner_rs.dmg_to_adds:,}).\n"
                f"They receive a {self.rarity_emote()} **{self.rarity.capitalize()} Crate**!"
            )
            await self.send_embeds([win])

            top5 = sorted(survivors, key=lambda t: (t[1].dmg_to_boss + t[1].dmg_to_adds), reverse=True)[:5]
            lb = discord.Embed(title="Top 5 Damage Dealers", colour=0x8844FF)
            rows = []
            for i, (u, r) in enumerate(top5, start=1):
                rows.append(f"**{i}.** {u.mention} — **{(r.dmg_to_boss + r.dmg_to_adds):,}** (Boss {r.dmg_to_boss:,} / Adds {r.dmg_to_adds:,})")
            lb.description = "\n".join(rows)
            await self.send_embeds([lb])

            # Fun extra: random egg to a survivor
            egg_user = random.choice(users)
            await self._award_random_egg_to_user(egg_user, announce_embed=True)
        else:
            await self.ctx.send("No survivors to receive crate/egg.")

    # --------- DB HELPERS ---------
    async def _mass_add_money(self, users: List[discord.User], amount_each: int):
        if not users or amount_each <= 0:
            return
        ids = [u.id for u in users]
        async with self.bot.pool.acquire() as conn:
            await conn.execute('UPDATE profile SET money=money+$1 WHERE "user"=ANY($2);', amount_each, ids)

    async def _mass_add_bloomshards(self, users: List[discord.User], amount_each: int) -> int:
        if not users or amount_each <= 0:
            return 0
        spring = self.bot.cogs.get("DreamboundSpring")
        if spring is None or not hasattr(spring, "add_bloomshards"):
            return 0

        awarded = 0
        async with self.bot.pool.acquire() as conn:
            for user in users:
                try:
                    await spring.add_bloomshards(user.id, amount_each, conn=conn)
                    awarded += 1
                except Exception:
                    continue
        return awarded

    async def _give_crate(self, user: discord.User):
        col = f"crates_{self.rarity}"
        async with self.bot.pool.acquire() as conn:
            await conn.execute(f'UPDATE profile SET "{col}"="{col}"+1 WHERE "user"=$1;', user.id)
        emote = self.rarity_emote()
        await self.ctx.send(f"{emote} {self.rarity.capitalize()} Crate awarded to {user.mention}!")

    async def _award_random_egg_to_user(self, user: discord.User, *, announce_embed: bool = False):
        """Give a random pet egg. Image is sourced ONLY from splice_combinations (image_url/url)
        or from monster_pets (url) matching the species name. No generic fallbacks."""
        result_name: str = "Mysterious Egg"
        element: Optional[str] = None
        image_url: Optional[str] = None

        ELEMENT_POOL = ["Fire","Water","Nature","Wind","Corrupted","Ice","Dark","Light","Electric"]

        async with self.bot.pool.acquire() as conn:
            # 1) Try to get a row with image from splice_combinations (supporting multiple schemas)
            row = None
            try:
                row = await conn.fetchrow(
                    'SELECT result_name, element, image_url FROM splice_combinations ORDER BY RANDOM() LIMIT 1;'
                )
            except Exception:
                try:
                    row = await conn.fetchrow(
                        'SELECT result_name, element, url FROM splice_combinations ORDER BY RANDOM() LIMIT 1;'
                    )
                except Exception:
                    try:
                        row = await conn.fetchrow(
                            'SELECT result_name, element FROM splice_combinations ORDER BY RANDOM() LIMIT 1;'
                        )
                    except Exception:
                        row = await conn.fetchrow(
                            'SELECT result_name FROM splice_combinations ORDER BY RANDOM() LIMIT 1;'
                        )

            if row:
                result_name = row["result_name"] if "result_name" in row.keys() and row["result_name"] else result_name
                if "element" in row.keys():
                    element = row["element"]
                # prefer image_url, then url, if present in schema
                if "image_url" in row.keys() and row["image_url"]:
                    image_url = row["image_url"]
                elif "url" in row.keys() and row["url"]:
                    image_url = row["url"]

            # If no element in splice_combinations, choose at random from your pool
            if not element or not str(element).strip():
                element = random.choice(ELEMENT_POOL)

            # 2) If we still don't have an image, try to borrow one from monster_pets by species name
            if not image_url or not str(image_url).strip():
                pet_img_row = await conn.fetchrow(
                    '''
                    SELECT url
                    FROM monster_pets
                    WHERE url IS NOT NULL AND btrim(url) <> ''
                    AND (
                            (default_name IS NOT NULL AND lower(default_name) = lower($1))
                            OR lower(name) = lower($1)
                        )
                    ORDER BY level DESC NULLS LAST, experience DESC NULLS LAST, id DESC
                    LIMIT 1;
                    ''',
                    result_name
                )
                if pet_img_row and pet_img_row["url"]:
                    image_url = pet_img_row["url"]

            # Roll egg stats
            hp = random.randint(200, 1200)
            attack = random.randint(120, 900)
            defense = random.randint(100, 800)
            iv = random.randint(40, 100)
            hatch_time = dt.datetime.now(timezone.utc) + dt.timedelta(days=3)

            # Persist the egg (note: url can be NULL if neither table provided one)
            await conn.fetchrow(
                '''
                INSERT INTO monster_eggs
                (user_id, egg_type, hp, attack, defense, element, url, "IV", hatched, hatch_time)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,FALSE,$9)
                RETURNING id
                ''',
                user.id, result_name, hp, attack, defense, element, image_url, iv, hatch_time
            )

        if announce_embed:
            await self.ctx.send(f"{user.mention}")
            egg = discord.Embed(title="You found a Pet Egg!", colour=0x00CC99)
            egg.add_field(name="Type", value=result_name)
            egg.add_field(name="Element", value=str(element))
            egg.add_field(name="HP", value=str(hp))
            egg.add_field(name="Attack", value=str(attack))
            egg.add_field(name="Defense", value=str(defense))
            if image_url and str(image_url).strip():
                egg.set_image(url=image_url)  # only show image if sourced from your DBs
            await self.send_embeds([egg])


    # --------- SUMMARY ---------
    def _duration_text(self) -> str:
        duration = dt.datetime.now(timezone.utc) - self.started_at
        minutes, seconds = (duration.seconds % 3600) // 60, duration.seconds % 60
        return f"{minutes} minutes, {seconds} seconds"

    async def _send_summary(self):
        chan = self.ctx.guild.get_channel(RAIDS_SUMMARY_CHANNEL_ID)
        if not chan:
            return
        survivors = sum(1 for r in self.raiders.values() if r.alive)
        boss_forces = 0  # placeholder (we're not tracking ally bots here)
        status = "Defeated in" if self.victory else "Failed after"
        crate_line = self.crate_line or "No crate result."
        payout_line = f"Payout per participant: **${self.payout_per:,}**" if self.payout_per is not None else "Payouts: —"
        bloomshards_line = self.spring_bloomshards_line
        emote = ":small_blue_diamond:"
        msg = (
            f"**Raid result:**\n"
            f"{emote} Health: **{self.boss['initial_hp']:,}**\n"
            f"{emote} {status}: **{self._duration_text()}**\n"
            f"{emote} {crate_line}\n"
            f"{emote} {payout_line}\n"
        )
        if bloomshards_line:
            msg += f"{emote} {bloomshards_line}\n"
        msg += (
            f"{emote} Survivors: **{survivors} and {boss_forces} of {self.boss['name']}'s forces**\n"
            f"{emote} Raiders joined: **{self.participants_count}**"
        )
        await chan.send(msg)

# =============================
# ========= COG ===============
# =============================

class NewRaids(commands.Cog):
    """Raid spawners and orchestration (Charybdis, Echidna)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Config: roles / thresholds
        self.patron_tier_threshold = 1              # profile.tier >= 1 auto-joins
        self.raider_role_id = 1405224571447148624   # ping role on spawn
        self.booster_role_id = 1405906986507178066  # server booster auto-join

    # ---------- helpers ----------
    async def _flavor_text(self, boss_name: str, hp: int) -> str:
        if boss_name == "Charybdis":
            lore = (
                "The sea itself yawns into a colossal maw. Sailors swear they hear the ocean breathe.\n"
                "Every few heartbeats the water wrenches downward — fight the current or be claimed."
            )
        elif boss_name == "Echidna":
            lore = (
                "Mother of Monsters coils around shattered stone, and the cave answers with snarls.\n"
                "Hack through her brood or thread arrows and spells to the heart of the beast."
            )
        elif boss_name == "Death":
            lore = (
                "The air stills. Footsteps echo where no body walks.\n"
                "Death has come - not in fury, but inevitability.\n"
                "Each heartbeat feels borrowed."
            )
        else:
            lore = "A legendary horror descends."
        return f"{lore}\n\n**{boss_name} will be vulnerable in 15 minutes.**\nHP: {hp:,}"

    async def _autojoin_patrons(self, ctx: commands.Context, view: JoinView) -> int:
        """Auto-join patrons with profile.tier >= threshold."""
        threshold = self.patron_tier_threshold
        try:
            rows = await self.bot.pool.fetch('SELECT "user" FROM profile WHERE "tier">=$1;', threshold)
        except Exception:
            return 0
        ids = {int(r[0]) for r in rows if r and r[0]}
        added = 0
        for uid in ids:
            member = ctx.guild.get_member(uid)
            if not member:
                continue
            if member not in view.joined:
                view.joined.append(member)
                added += 1
        if added:
            await ctx.send(f"✨ Auto-joined **{added}** patrons (tier ≥ {threshold}).")
        return added

    async def _autojoin_boosters(self, ctx: commands.Context, view: JoinView) -> int:
        role = ctx.guild.get_role(self.booster_role_id)
        if not role:
            return 0
        added = 0
        for member in role.members:
            if member not in view.joined:
                view.joined.append(member)
                added += 1
        if added:
            await ctx.send(f"💠 Auto-joined **{added}** server boosters.")
        return added

    async def _spawn_generic(
        self,
        ctx: commands.Context,
        *,
        boss_name: str,
        hp: int,
        rarity: str,
        behavior: BossBehavior,
        enable_autojoin: bool = True,
    ) -> None:
        # Validate rarity
        rarity = (rarity or "magic").lower()
        if rarity not in ALLOWED_RARITIES:
            await ctx.send("Invalid rarity. Valid: " + ", ".join(sorted(ALLOWED_RARITIES)))
            return

        # Delete invoking command message
        try:
            await ctx.message.delete()
        except Exception:
            pass

        engine = RaidEngine(self.bot, ctx, boss_name=boss_name, boss_hp=hp, behavior=behavior, rarity=rarity)

        # Join phase
        view = JoinView(timeout=60 * 15)
        em = discord.Embed(
            title=f"{boss_name} approaches!",
            description=await self._flavor_text(boss_name, hp),
            colour=0x3366FF,
        )
        try:
            em.set_image(url=getattr(behavior, 'start_img'))
        except Exception:
            pass
        await ctx.send(embed=em, view=view)

        # Raider ping
        role = ctx.guild.get_role(self.raider_role_id)
        mention_text = role.mention if role else f"<@&{self.raider_role_id}>"
        await ctx.send(
            f"{mention_text} **{boss_name} spawned!** Join with the button below. Vulnerable in 15 minutes.",
            allowed_mentions=discord.AllowedMentions(roles=True),
        )

        if enable_autojoin:
            await self._autojoin_patrons(ctx, view)
            await self._autojoin_boosters(ctx, view)

        # Lock channel immediately
        await engine.lock_channel()

        # Countdown announcements
        for delay, text in [
            (300, f"**{boss_name} will be vulnerable in 10 minutes**"),
            (300, f"**{boss_name} will be vulnerable in 5 minutes**"),
            (180, f"**{boss_name} will be vulnerable in 2 minutes**"),
            (60,  f"**{boss_name} will be vulnerable in 1 minute**"),
            (30,  f"**{boss_name} will be vulnerable in 30 seconds**"),
            (20,  f"**{boss_name} will be vulnerable in 20 seconds**"),
            (10,  f"**{boss_name} will be vulnerable in 10 seconds**"),
        ]:
            await asyncio.sleep(delay)
            await ctx.send(text)

        # Start
        view.stop()
        await ctx.send("**Boss is vulnerable! Fetching participants…**")
        players: List[discord.User] = list(view.joined)
        engine.participants_count = len(players)
        await ctx.send(f"**{engine.participants_count} participants** have joined the raid.")

        await engine.enroll(players)
        await engine.run()

    # ---------- spawn handlers (invoked via raid $spawn) ----------

    async def spawn_charybdis(self, ctx: commands.Context, hp: int, rarity: str = "magic"):
        await self._spawn_generic(ctx, boss_name="Charybdis", hp=hp, rarity=rarity, behavior=CharybdisBehavior())

    async def spawn_echidna(self, ctx: commands.Context, hp: int, rarity: str = "magic"):
        await self._spawn_generic(ctx, boss_name="Echidna", hp=hp, rarity=rarity, behavior=EchidnaBehavior())

    @is_gm()
    @raid_channel()
    @commands.command(name="springspawn", hidden=True)
    async def springspawn(self, ctx: commands.Context, hp: int, rarity: str = "magic"):
        """Spawn the Dreambound Spring Death raid."""
        await self._spawn_generic(
            ctx,
            boss_name="Death",
            hp=hp,
            rarity=rarity,
            behavior=DeathBehavior(),
            enable_autojoin=False,
        )


# ===== setup =====
async def setup(bot: commands.Bot):
    await bot.add_cog(NewRaids(bot))
