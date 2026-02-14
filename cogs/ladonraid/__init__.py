



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
from dataclasses import dataclass
from typing import Dict, List, Iterable, Optional, Tuple

import discord
from discord.ext import commands
from discord import ButtonStyle
from discord.ui import View, Button

# =============================
# ======= CONFIG / META =======
# =============================

# Guild bits
RAIDER_ROLE_ID = 1405224571447148624
BOOSTER_ROLE_ID = 1405906986507178066
RAIDS_SUMMARY_CHANNEL_ID = 1404885965813842021
PATRON_TIER_THRESHOLD = 1  # profile.tier >= this auto-joins

# Auctions
AUCTION_INITIAL_WINDOW_SECONDS = 120  # 2 minutes to place first bid
AUCTION_SILENT_CLOSE_SECONDS = 60     # 60s of silence to close after last bid

# Allowed crates
ALLOWED_CRATE_RARITIES = {"magic","legendary","materials","rare","uncommon","common","mystery","fortune","divine"}

# Elements (+ Ice)
ELEMENTS = ["Fire","Water","Nature","Wind","Light","Corrupted","Dark","Electric","Ice"]

# Primary & Secondary advantages (attacker -> defenders it beats)
PRIMARY_ADV = {
    "Light":     ["Corrupted"],
    "Dark":      ["Light"],
    "Corrupted": ["Dark"],
    "Nature":    ["Electric"],
    "Electric":  ["Water"],
    "Water":     ["Fire"],
    "Fire":      ["Nature"],
    "Wind":      ["Electric"],
    "Ice":       ["Nature"],
}
SECONDARY_ADV = {
    "Wind":      ["Fire"],       # wind redirects flames
    "Electric":  ["Dark"],       # lightning pierces shadows
    "Water":     ["Corrupted"],  # cleansing waters
    "Nature":    ["Water"],      # overgrowth binds currents
    "Ice":       ["Wind"],       # chill slows gusts
    "Light":     ["Dark"],       # softer angle than Dark>Light
    "Corrupted": ["Nature"],     # blight withers growth
}
ADV_MULT_PRIMARY = 1.50
ADV_MULT_SECONDARY = 1.25
RESIST_MULT_PRIMARY = 0.55
RESIST_MULT_SECONDARY = 0.80

# Ladon art
LADON_START_IMG    = "https://i.imgur.com/5ywXuDM.png"
LADON_DEFEATED_IMG = "https://i.imgur.com/QfkS4pH.png"
LADON_ELEMENT_IMG = {
    "Fire":      "https://i.imgur.com/49XzNAC.png",
    "Water":     "https://i.imgur.com/pjrGu1x.png",
    "Nature":    "https://i.imgur.com/rFCCFbX.png",
    "Wind":      "https://i.imgur.com/vmpqYun.png",
    "Light":     "https://i.imgur.com/6ZSWMsH.png",
    "Corrupted": "https://i.imgur.com/Zj1iLax.png",
    "Dark":      "https://i.imgur.com/sXatnKp.png",
    "Electric":  "https://i.imgur.com/HbIsUph.png",
    "Ice":       "https://i.imgur.com/xEa7apG.png",
}

# Talos art
TALOS_START_IMG  = "https://i.imgur.com/cUdEAQM.png"
TALOS_ATTACK_IMG = "https://i.imgur.com/1PNDD6w.png"

# Crate emotes fallback
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

# =============================
# ====== UTIL / DATAMODEL =====
# =============================

def rarity_emote(bot: commands.Bot, rarity: str) -> str:
    try:
        crates = bot.cogs.get('Crates')
        if crates:
            emotes = getattr(crates, 'emotes', None)
            if emotes and hasattr(emotes, rarity):
                return getattr(emotes, rarity)
    except Exception:
        pass
    return RARITY_FALLBACK_EMOTES.get(rarity, "🎁")

def element_multiplier(attacker: str, defender: str) -> float:
    mult = 1.0
    if defender in PRIMARY_ADV.get(attacker, ()):
        mult *= ADV_MULT_PRIMARY
    elif defender in SECONDARY_ADV.get(attacker, ()):
        mult *= ADV_MULT_SECONDARY
    if attacker in PRIMARY_ADV.get(defender, ()):
        mult *= RESIST_MULT_PRIMARY
    elif attacker in SECONDARY_ADV.get(defender, ()):
        mult *= RESIST_MULT_SECONDARY
    return max(0.50, min(1.50, mult))

@dataclass
class PetState:
    owner: discord.Member
    name: str
    element: str
    hp: int
    atk: int
    deff: int
    url: Optional[str] = None
    alive: bool = True
    total_dealt: int = 0
    total_taken: int = 0

@dataclass
class RaiderState:
    hp: int
    armor: int
    dps: int
    alive: bool = True
    total_taken: int = 0
    total_dealt: int = 0

# =============================
# ========= JOIN VIEW =========
# =============================

class JoinView(View):
    """Join button that can optionally require an equipped pet (Ladon)."""
    def __init__(self, bot: commands.Bot, *, require_pet: bool, timeout: int = 60*15):
        super().__init__(timeout=timeout)
        self.bot = bot
        self.require_pet = require_pet
        self.joined: List[discord.Member] = []
        self.add_item(JoinButton(self))

class JoinButton(Button):
    def __init__(self, view: JoinView):
        super().__init__(style=ButtonStyle.primary, label="Join the raid!")
        self.view_ref = view

    async def callback(self, interaction: discord.Interaction):
        user = interaction.user
        if user in self.view_ref.joined:
            return await interaction.response.send_message("You're already in.", ephemeral=True)

        if self.view_ref.require_pet:
            async with self.view_ref.bot.pool.acquire() as conn:
                row = await conn.fetchrow(
                    'SELECT id FROM monster_pets WHERE user_id=$1 AND equipped=TRUE LIMIT 1;',
                    user.id
                )
            if not row:
                return await interaction.response.send_message(
                    "You need an **equipped pet** to join this raid. Use `/pet equip` first.",
                    ephemeral=True
                )

        self.view_ref.joined.append(user)
        await interaction.response.send_message("Joined!", ephemeral=True)

# =============================
# ========== ENGINES ==========
# =============================

class BaseEngine:
    def __init__(self, bot: commands.Bot, ctx: commands.Context, *, boss_name: str, boss_hp: int, start_img: str, attack_img: Optional[str]=None, duration_minutes: int=60):
        self.bot = bot
        self.ctx = ctx
        self.boss_name = boss_name
        self.boss_hp = boss_hp
        self.boss_hp_initial = boss_hp
        self.start_img = start_img
        self.attack_img = attack_img
        self.duration = dt.timedelta(minutes=duration_minutes)
        self.started_at = dt.datetime.now(timezone.utc)
        self._locked = False

        # Stats / summary
        self.participants_count = 0
        self.victory: Optional[bool] = None
        self.crate_line: Optional[str] = None
        self.payout_per: Optional[int] = None

    async def lock(self):
        if self._locked:
            return
        ch: discord.TextChannel = self.ctx.channel
        try:
            await ch.set_permissions(self.ctx.guild.default_role, send_messages=False)
            await ch.set_permissions(self.ctx.author, send_messages=True)
            self._locked = True
        except Exception:
            pass

    async def unlock(self):
        if not self._locked:
            return
        ch: discord.TextChannel = self.ctx.channel
        try:
            await ch.set_permissions(self.ctx.guild.default_role, send_messages=None)
            await ch.set_permissions(self.ctx.author, send_messages=None)
        except Exception:
            pass
        self._locked = False

    def boss_bar(self, width: int = 20) -> str:
        cur = max(0, int(self.boss_hp))
        mx = max(1, int(self.boss_hp_initial))
        ratio = max(0.0, min(1.0, cur / mx))
        filled = int(round(ratio * width))
        bar = "█"*filled + "░"*(width - filled)
        pct = int(ratio * 100)
        return f"`{bar}` {pct}% ({cur:,}/{mx:,})"

    async def _send_summary(self):
        chan = self.ctx.guild.get_channel(RAIDS_SUMMARY_CHANNEL_ID)
        if not chan:
            return
        status = "Defeated in" if self.victory else "Failed after"
        dur = dt.datetime.now(timezone.utc) - self.started_at
        m, s = (dur.seconds % 3600) // 60, dur.seconds % 60
        payout_line = f"Payout per participant: **${self.payout_per:,}**" if self.payout_per is not None else "Payouts: —"
        msg = (
            f"**Raid result:**\n"
            f":small_blue_diamond: Health: **{self.boss_hp_initial:,}**\n"
            f":small_blue_diamond: {status}: **{m} minutes, {s} seconds**\n"
            f":small_blue_diamond: {self.crate_line or 'No crate result.'}\n"
            f":small_blue_diamond: {payout_line}\n"
            f":small_blue_diamond: Raiders joined: **{self.participants_count}**"
        )
        await chan.send(msg)

# ----- Ladon (Pet-only, real mechanism) -----

class LadonEngine(BaseEngine):
    """Pet-only boss. One pet acts per player turn; every 5 player turns Ladon AOE's 3 pets; otherwise a single-target special."""
    def __init__(self, bot: commands.Bot, ctx: commands.Context, *, boss_element: str, boss_hp: int, crate_rarity: str):
        super().__init__(bot, ctx, boss_name=f"Ladon [{boss_element}]", boss_hp=boss_hp, start_img=LADON_START_IMG, attack_img=LADON_ELEMENT_IMG.get(boss_element, LADON_START_IMG), duration_minutes=60)
        self.boss_element = boss_element
        self.crate_rarity = crate_rarity
        self.pets: Dict[discord.Member, PetState] = {}
        self.player_turns = 0
        self.last_hitter: Optional[discord.Member] = None

    async def enroll(self, users: List[discord.Member]):
        async with self.bot.pool.acquire() as conn:
            for u in users:
                row = await conn.fetchrow(
                    '''SELECT name, hp, attack, defense, element, url
                       FROM monster_pets
                       WHERE user_id=$1 AND equipped=TRUE
                       ORDER BY id DESC
                       LIMIT 1;''',
                    u.id
                )
                if not row:
                    continue
                name  = row["name"] or "Your Pet"
                hp    = int(row["hp"] or 500)
                atk   = int(row["attack"] or 120)
                deff  = int(row["defense"] or 80)
                elem  = (row["element"] or "Neutral").title()
                url   = row["url"] or None
                if elem not in ELEMENTS:
                    elem = random.choice(ELEMENTS)
                self.pets[u] = PetState(owner=u, name=name, element=elem, hp=hp, atk=atk, deff=deff, url=url)

    async def run(self):
        # Flavor intro
        feel = (
            "A grove falls silent as scales whisper through ancient boughs. "
            "The **Hesperides’ drake** uncoils—nine heads tasting the air, "
            "each a different hue of hunger. Ladon watches your companions, "
            "measuring their mettle… and their **pets**."
        )
        em = discord.Embed(
            title=f"Ladon stirs ({self.boss_element})",
            description=(
                f"{feel}\n\n"
                f"**This is a *pet-only* raid.** Only your **equipped pet** will fight.\n"
                f"Use `/pet equip` if you need to switch.\n\n"
                f"**Ladon will be vulnerable in 15 minutes.**\n"
                f"HP: **{self.boss_hp:,}**"
            ),
            colour=0x2AA55A
        )
        em.set_image(url=self.start_img)
        await self.ctx.send(embed=em)

        await self.lock()

        start = dt.datetime.now(timezone.utc)
        while self.boss_hp > 0 and any(p.alive for p in self.pets.values()) and dt.datetime.now(timezone.utc) < start + self.duration:
            # ===== Player turn: exactly ONE pet attacks =====
            await self._one_pet_attacks()

            if self.boss_hp <= 0:
                break

            # ===== Boss turn =====
            if self.player_turns > 0 and self.player_turns % 5 == 0:
                await self._aoe_breath_3()
            else:
                # pick one of two specials
                if random.random() < 0.5:
                    await self._special_constrict()
                else:
                    await self._special_venom_spit()

            await asyncio.sleep(4)

        self.victory = self.boss_hp <= 0
        if self.victory:
            await self._victory()
        else:
            await self._defeat()

        await self._send_summary()

    # ---------- Player phase ----------
    async def _one_pet_attacks(self):
        alive = [(u, p) for u, p in self.pets.items() if p.alive]
        if not alive:
            return
        self.player_turns += 1
        u, p = random.choice(alive)

        # vary damage a bit around atk
        base = int(p.atk * random.uniform(0.85, 1.15))
        mult = element_multiplier(p.element, self.boss_element)
        dmg = max(1, int(base * mult))  # boss has no armor
        self.boss_hp -= dmg
        p.total_dealt += dmg

        em = discord.Embed(
            title=f"{u.display_name}'s {p.name} attacks Ladon!",
            description=f"{u.mention}'s **{p.name}** ({p.element}) strikes for **{dmg:,}** (×{mult:.2f}).",
            colour=0xFF7A00
        )
        # Use player's avatar as author (per your request)
        try:
            em.set_author(name=str(u), icon_url=u.display_avatar.url)
        except Exception:
            pass
        # Show pet picture if available, else show Ladon's elemental image
        thumb = p.url or self.attack_img or LADON_ELEMENT_IMG.get(self.boss_element, LADON_START_IMG)
        try:
            em.set_thumbnail(url=thumb)
        except Exception:
            pass

        em.add_field(name="Boss HP", value=self.boss_bar(), inline=False)
        await self.ctx.send(embed=em)

        if self.boss_hp <= 0:
            self.last_hitter = u

    # ---------- Boss phase ----------
    async def _aoe_breath_3(self):
        alive = [(u, p) for u, p in self.pets.items() if p.alive]
        if not alive:
            return
        random.shuffle(alive)
        targets = alive[:3]
        base = random.randint(480, 920)  # AOE breath base
        em = discord.Embed(title=f"Ladon exhales a {self.boss_element.lower()} breath!", colour=0x3399FF)
        em.set_thumbnail(url=self.attack_img)
        for u, p in targets:
            mult = element_multiplier(self.boss_element, p.element)
            eff = max(1, int(base * mult) - p.deff)
            p.hp -= eff
            p.total_taken += eff
            em.add_field(name=f"Hit {p.name}", value=f"{u.mention} took **{eff:,}** (×{mult:.2f}) — HP {max(0,p.hp)}")
            if p.hp <= 0 and p.alive:
                p.alive = False
                em.add_field(name="Downed", value=f"{u.mention}'s **{p.name}** is defeated.", inline=False)
        em.add_field(name="Boss HP", value=self.boss_bar(), inline=False)
        await self.ctx.send(embed=em)

    async def _special_constrict(self):
        """Heavy single-target hit, partially armor-piercing."""
        alive = [(u, p) for u, p in self.pets.items() if p.alive]
        if not alive:
            return
        u, p = random.choice(alive)
        base = random.randint(700, 1100)
        mult = element_multiplier(self.boss_element, p.element)
        # 25% armor pierce: only 75% of DEF counts
        eff = max(1, int(base * mult) - int(p.deff * 0.75))
        p.hp -= eff
        p.total_taken += eff
        em = discord.Embed(title="Ladon constricts!", description=f"{u.mention}'s **{p.name}** takes **{eff:,}** (×{mult:.2f})", colour=0xBB3333)
        em.set_thumbnail(url=self.attack_img)
        if p.hp <= 0 and p.alive:
            p.alive = False
            em.add_field(name="Downed", value=f"{u.mention}'s **{p.name}** is crushed.", inline=False)
        em.add_field(name="Boss HP", value=self.boss_bar(), inline=False)
        await self.ctx.send(embed=em)

    async def _special_venom_spit(self):
        """Armor-light hit + venom echo that chips extra damage."""
        alive = [(u, p) for u, p in self.pets.items() if p.alive]
        if not alive:
            return
        u, p = random.choice(alive)
        base = random.randint(520, 900)
        mult = element_multiplier(self.boss_element, p.element)
        eff = max(1, int(base * mult) - int(p.deff * 0.9))  # slightly more ignoring DEF
        echo = random.randint(80, 180)                      # venom echo, pure chip
        total = eff + echo
        p.hp -= total
        p.total_taken += total
        em = discord.Embed(
            title="Ladon spits venom!",
            description=f"{u.mention}'s **{p.name}** takes **{eff:,}** (×{mult:.2f}) + **{echo}** venom echo = **{total:,}**",
            colour=0x4E9A06
        )
        em.set_thumbnail(url=self.attack_img)
        if p.hp <= 0 and p.alive:
            p.alive = False
            em.add_field(name="Downed", value=f"{u.mention}'s **{p.name}** succumbs to the venom.", inline=False)
        em.add_field(name="Boss HP", value=self.boss_bar(), inline=False)
        await self.ctx.send(embed=em)

    # ---------- Endings ----------
    async def _victory(self):
        await self.unlock()
        dur = dt.datetime.now(timezone.utc) - self.started_at
        m, s = (dur.seconds % 3600)//60, dur.seconds % 60
        em = discord.Embed(
            title="Ladon defeated!",
            description=f"The drake collapses into the orchard’s shadow. Time: **{m}m {s}s**.",
            colour=0x22CC88
        )
        em.set_image(url=LADON_DEFEATED_IMG)
        await self.ctx.send(embed=em)

        # Crate to last hitter
        if self.last_hitter is not None:
            await self._grant_crate(self.last_hitter.id, self.crate_rarity)
            emote = rarity_emote(self.bot, self.crate_rarity)
            self.crate_line = f"{self.crate_rarity.capitalize()} crate {emote} awarded to: {self.last_hitter.mention}"
            await self.ctx.send(f"{emote} **{self.crate_rarity.capitalize()} Crate** goes to {self.last_hitter.mention} for the **final blow!**")
        else:
            await self.ctx.send("No last-hitter could be determined.")

                # --- NEW: Top 5 Pets by Total Damage (MVPs) ---
        try:
            pairs = [(u, p) for u, p in self.pets.items()]  # include all, alive or not
            top5 = sorted(pairs, key=lambda t: t[1].total_dealt, reverse=True)[:5]
            lb = discord.Embed(title="Top 5 Pets — Total Damage", colour=0xFFC107)
            if top5:
                lines = []
                for i, (u, p) in enumerate(top5, start=1):
                    lines.append(
                        f"**{i}.** {u.mention}'s **{p.name}** ({p.element}) — **{p.total_dealt:,}** dmg"
                    )
                lb.description = "\n".join(lines)
            else:
                lb.description = "No damage recorded."
            await self.ctx.send(embed=lb)
        except Exception:
            # don't let the leaderboard break the flow if anything odd happens
            pass

        # Potion auction (survivors only) — exact DB keys
        survivors = [u for u, p in self.pets.items() if p.alive]
        if survivors:
            pot_code, pot_name, pot_emoji = random.choice([
                ("pet_age_potion",            "Pet Age Potion",           ":ageup:"),
                ("pet_speed_growth_potion",   "Pet Speed Growth Potion",  ":finalpotion:"),
                ("pet_xp_potion",             "Pet XP Potion",            ":splicepotion:"),
            ])
            item_text = f"{pot_emoji} **{pot_name}**"
            await self.ctx.send("**Ladon dropped a pet potion! Bid for it!**")
            sold = await self._auction(survivors, item_text=item_text)
            if sold:
                uid, amt = sold
                async with self.bot.pool.acquire() as conn:
                    # upsert without requiring a unique constraint
                    exists = await conn.fetchval(
                        'SELECT 1 FROM user_consumables WHERE user_id=$1 AND consumable_type=$2;',
                        uid, pot_code
                    )
                    if exists:
                        await conn.execute(
                            'UPDATE user_consumables SET quantity=quantity+1 WHERE user_id=$1 AND consumable_type=$2;',
                            uid, pot_code
                        )
                    else:
                        await conn.execute(
                            'INSERT INTO user_consumables (user_id, consumable_type, quantity, metadata, created_at) '
                            'VALUES ($1,$2,1,NULL, NOW());',
                            uid, pot_code
                        )
                await self.ctx.send(f"{item_text} delivered to <@{uid}>.")
        else:
            await self.ctx.send("No survivors for the potion auction.")

    async def _defeat(self):
        await self.unlock()
        em = discord.Embed(
            title="Ladon withdraws",
            description="The dragon coils back over the garden wall; the golden apples vanish from sight.",
            colour=0xAA2222
        )
        em.add_field(name="HP remaining", value=f"{self.boss_hp:,}")
        await self.ctx.send(embed=em)

    async def _grant_crate(self, user_id: int, rarity: str):
        col = f'crates_{rarity}'
        async with self.bot.pool.acquire() as conn:
            await conn.execute(f'UPDATE profile SET "{col}"="{col}"+1 WHERE "user"=$1;', user_id)

    async def _auction(self, survivors: List[discord.Member], *, item_text: str) -> Optional[Tuple[int,int]]:
        if not survivors:
            await self.ctx.send("No survivors; auction skipped.")
            return None

        await self.unlock()
        mentions = " ".join(m.mention for m in survivors)
        await self.ctx.send(
            f"{mentions}\n"
            f"**Auction time!** Type an integer bid in chat for {item_text}.\n"
            f"You have **{AUCTION_INITIAL_WINDOW_SECONDS//60} minutes** to place the first bid, "
            f"then the auction closes **{AUCTION_SILENT_CLOSE_SECONDS} seconds** after the last valid bid."
        )

        highest_user_id: Optional[tuple[int,int]] = None
        highest_amount = 0
        open_until = dt.datetime.now(timezone.utc) + dt.timedelta(seconds=AUCTION_INITIAL_WINDOW_SECONDS)
        survivor_ids = {m.id for m in survivors}

        def check(msg: discord.Message) -> bool:
            return msg.channel.id == self.ctx.channel.id and msg.author.id in survivor_ids and msg.content.isdigit()

        while True:
            timeout = max(0, (open_until - dt.datetime.now(timezone.utc)).total_seconds())
            if timeout == 0:
                break
            try:
                msg = await self.bot.wait_for("message", timeout=timeout, check=check)
            except asyncio.TimeoutError:
                break

            bid = int(msg.content)

            # prevent self-outbidding
            if highest_user_id is not None and msg.author.id == highest_user_id:
                await self.ctx.send(
                    f"{msg.author.mention} you're already leading at **${highest_amount:,}**. "
                    f"Wait for someone else to bid."
                )
                continue

            min_next = 1 if highest_amount == 0 else int(math.ceil(max(highest_amount + 1, highest_amount * 1.10)))
            if bid < min_next:
                await self.ctx.send(f"{msg.author.mention} minimum next bid is **${min_next:,}**.")
                continue

            async with self.bot.pool.acquire() as conn:
                bal = await conn.fetchval('SELECT money FROM profile WHERE "user"=$1;', msg.author.id)
                if bal is None or bal < bid:
                    await self.ctx.send(f"{msg.author.mention} you don't have enough money.")
                    continue
                if highest_user_id is not None and highest_amount > 0:
                    await conn.execute('UPDATE profile SET money=money+$1 WHERE "user"=$2;', highest_amount, highest_user_id)
                await conn.execute('UPDATE profile SET money=money-$1 WHERE "user"=$2;', bid, msg.author.id)

            highest_user_id, highest_amount = msg.author.id, bid
            next_min = int(math.ceil(max(bid + 1, bid * 1.10)))
            await self.ctx.send(f"{msg.author.mention} leads at **${bid:,}** for {item_text}! (Next ≥ ${next_min:,})")
            open_until = dt.datetime.now(timezone.utc) + dt.timedelta(seconds=AUCTION_SILENT_CLOSE_SECONDS)

        if highest_user_id is None:
            await self.ctx.send("No bids placed. Auction closed.")
            return None

        await self.ctx.send(f"Auction closed! Winner: <@{highest_user_id}> — **${highest_amount:,}** for {item_text}.")
        return (highest_user_id, highest_amount)

# ----- Talos (classic, unchanged behavior + self-outbid guard) -----

class TalosEngine(BaseEngine):
    def __init__(self, bot: commands.Bot, ctx: commands.Context, *, boss_hp: int):
        super().__init__(bot, ctx, boss_name="Talos", boss_hp=boss_hp, start_img=TALOS_START_IMG, attack_img=TALOS_ATTACK_IMG, duration_minutes=60)
        self.raiders: Dict[discord.Member, RaiderState] = {}
        self.turn = 0

    async def enroll(self, users: List[discord.Member]):
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
                    conn=conn,
                )
                xp = int(profile.get("xp", 0) or 0)
                level = int((xp ** 0.5) // 10)
                stathp = int(profile.get("stathp", 0)) * 50
                basehp = int(profile.get("health", 0)) + 250 + (level * 5) + stathp
                self.raiders[u] = RaiderState(hp=int(basehp), armor=int(deff), dps=int(dmg))

    async def run(self):
        em = discord.Embed(
            title="Talos approaches",
            description="A bronze giant tramples the horizon. **Vulnerable in 15 minutes.**",
            colour=0x996600
        )
        em.set_image(url=self.start_img)
        await self.ctx.send(embed=em)

        await self.lock()

        start = dt.datetime.now(timezone.utc)
        while self.boss_hp > 0 and any(r.alive for r in self.raiders.values()) and dt.datetime.now(timezone.utc) < start + self.duration:
            self.turn += 1
            roll = random.random()
            if roll < 0.33:
                await self._aoe_fire()
            elif roll < 0.55:
                await self._oneshot()
            else:
                await self._multi_strike()

            # Raiders attack
            total = 0
            for u, r in self.raiders.items():
                if not r.alive: continue
                self.boss_hp -= r.dps
                r.total_dealt += r.dps
                total += r.dps
            em_strike = discord.Embed(title="Raiders strike Talos!", colour=0xFF5C00)
            em_strike.add_field(name="Damage", value=f"{total:,}")
            em_strike.add_field(name="Boss HP", value=self.boss_bar(), inline=False)
            await self.ctx.send(embed=em_strike)

            await asyncio.sleep(4)

        self.victory = self.boss_hp <= 0
        if self.victory:
            await self._victory()
        else:
            await self._defeat()

        await self._send_summary()

    async def _aoe_fire(self):
        alive = [(u, r) for u, r in self.raiders.items() if r.alive]
        if not alive: return
        random.shuffle(alive)
        targets = alive[:6]
        base = random.randint(530, 1250)
        em = discord.Embed(title="Talos spews fire!", colour=0xFF3300)
        em.set_thumbnail(url=self.attack_img)
        for u, r in targets:
            eff = max(1, base - r.armor)
            r.hp -= eff; r.total_taken += eff
            if r.hp <= 0 and r.alive:
                r.alive = False
                em.add_field(name="Downed", value=f"{u.mention} is incinerated.", inline=False)
        em.add_field(name="Boss HP", value=self.boss_bar(), inline=False)
        await self.ctx.send(embed=em)

    async def _oneshot(self):
        alive = [(u, r) for u, r in self.raiders.items() if r.alive]
        if not alive: return
        u, r = random.choice(alive)
        raw = random.randint(1200, 1900)
        eff = max(1, raw - r.armor)
        r.hp -= eff; r.total_taken += eff
        em = discord.Embed(title="Talos delivers a brutal strike!", colour=0x880000)
        em.set_thumbnail(url=self.attack_img)
        if r.hp <= 0 and r.alive:
            r.alive = False
            em.add_field(name="Downed", value=f"{u.mention} is crushed under bronze.", inline=False)
        em.add_field(name="Boss HP", value=self.boss_bar(), inline=False)
        await self.ctx.send(embed=em)

    async def _multi_strike(self):
        for _ in range(3):
            await self._oneshot()

    async def _victory(self):
        await self.unlock()
        dur = dt.datetime.now(timezone.utc) - self.started_at
        m, s = (dur.seconds % 3600)//60, dur.seconds % 60
        em = discord.Embed(title="Talos defeated!", description=f"Time: **{m}m {s}s**", colour=0x22CC88)
        await self.ctx.send(embed=em)

        # Payout to all participants
        users_all = [u for u in self.raiders.keys()]
        per = 300
        await self._mass_add_money(users_all, per)
        self.payout_per = per
        await self.ctx.send(f"Gave **${per:,}** to all **{len(users_all)}** participants.")

        # Survivors auction: loot bundle (dragoncoins)
        survivors = [u for u, r in self.raiders.items() if r.alive]
        if not survivors:
            await self.ctx.send("No survivors; auction skipped.")
            return
        sold = await self._auction(survivors, item_text="a **Loot Bundle**")
        if sold:
            uid, amt = sold
            drachmas = random.randint(5, 25)
            async with self.bot.pool.acquire() as conn:
                await conn.execute('UPDATE profile SET dragoncoins=dragoncoins+$1 WHERE "user"=$2;', drachmas, uid)
            self.crate_line = f"Loot bundle sold to: <@{uid}> for **${amt:,}** (contents: {drachmas} drachmas)"

    async def _defeat(self):
        await self.unlock()
        em = discord.Embed(title="Talos withdraws", description="The bronze giant’s gears hiss into silence.", colour=0xAA2222)
        em.add_field(name="HP remaining", value=f"{self.boss_hp:,}")
        await self.ctx.send(embed=em)

    async def _mass_add_money(self, users: List[discord.Member], amount_each: int):
        if not users or amount_each <= 0: return
        ids = [u.id for u in users]
        async with self.bot.pool.acquire() as conn:
            await conn.execute('UPDATE profile SET money=money+$1 WHERE "user"=ANY($2);', amount_each, ids)

    async def _auction(self, survivors: List[discord.Member], *, item_text: str) -> Optional[Tuple[int,int]]:
        if not survivors:
            await self.ctx.send("No survivors; auction skipped.")
            return None

        await self.unlock()
        mentions = " ".join(m.mention for m in survivors)
        await self.ctx.send(
            f"{mentions}\n"
            f"**Auction time!** Type an integer bid in chat for {item_text}.\n"
            f"You have **{AUCTION_INITIAL_WINDOW_SECONDS//60} minutes** to place the first bid, "
            f"then the auction closes **{AUCTION_SILENT_CLOSE_SECONDS} seconds** after the last valid bid."
        )

        highest_user_id: Optional[int] = None
        highest_amount = 0
        open_until = dt.datetime.now(timezone.utc) + dt.timedelta(seconds=AUCTION_INITIAL_WINDOW_SECONDS)
        survivor_ids = {m.id for m in survivors}

        def check(msg: discord.Message) -> bool:
            return msg.channel.id == self.ctx.channel.id and msg.author.id in survivor_ids and msg.content.isdigit()

        while True:
            timeout = max(0, (open_until - dt.datetime.now(timezone.utc)).total_seconds())
            if timeout == 0:
                break
            try:
                msg = await self.bot.wait_for("message", timeout=timeout, check=check)
            except asyncio.TimeoutError:
                break

            bid = int(msg.content)

            if highest_user_id is not None and msg.author.id == highest_user_id:
                await self.ctx.send(
                    f"{msg.author.mention} you're already leading at **${highest_amount:,}**. "
                    f"Wait for someone else to bid."
                )
                continue

            min_next = 1 if highest_amount == 0 else int(math.ceil(max(highest_amount + 1, highest_amount * 1.10)))
            if bid < min_next:
                await self.ctx.send(f"{msg.author.mention} minimum next bid is **${min_next:,}**.")
                continue

            async with self.bot.pool.acquire() as conn:
                bal = await conn.fetchval('SELECT money FROM profile WHERE "user"=$1;', msg.author.id)
                if bal is None or bal < bid:
                    await self.ctx.send(f"{msg.author.mention} you don't have enough money.")
                    continue
                if highest_user_id is not None and highest_amount > 0:
                    await conn.execute('UPDATE profile SET money=money+$1 WHERE "user"=$2;', highest_amount, highest_user_id)
                await conn.execute('UPDATE profile SET money=money-$1 WHERE "user"=$2;', bid, msg.author.id)

            highest_user_id, highest_amount = msg.author.id, bid
            next_min = int(math.ceil(max(bid + 1, bid * 1.10)))
            await self.ctx.send(f"{msg.author.mention} is leading at **${bid:,}** for {item_text}! (Next ≥ ${next_min:,})")
            open_until = dt.datetime.now(timezone.utc) + dt.timedelta(seconds=AUCTION_SILENT_CLOSE_SECONDS)

        if highest_user_id is None:
            await self.ctx.send("No bids placed. Auction closed.")
            return None

        await self.ctx.send(f"Auction closed! Winner: <@{highest_user_id}> — **${highest_amount:,}** for {item_text}.")
        return (highest_user_id, highest_amount)

# =============================
# ============ COG ============
# =============================

try:
    from cogs.utils.checks import is_gm, raid_channel
except Exception:
    def is_gm():
        def deco(f): return f
        return deco
    def raid_channel():
        def deco(f): return f
        return deco

class LadonRaid(commands.Cog):
    """Two raids: Ladon (pet-only, elemental) and Talos."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ------- shared helpers -------

    async def _autojoin_patrons(self, ctx: commands.Context, view: JoinView) -> int:
        try:
            rows = await self.bot.pool.fetch('SELECT "user" FROM profile WHERE "tier">=$1;', PATRON_TIER_THRESHOLD)
        except Exception:
            return 0
        ids = {int(r[0]) for r in rows if r and r[0]}
        added = 0
        for uid in ids:
            member = ctx.guild.get_member(uid)
            if member and member not in view.joined:
                view.joined.append(member)
                added += 1
        if added:
            await ctx.send(f"✨ Auto-joined **{added}** patrons (tier ≥ {PATRON_TIER_THRESHOLD}).")
        return added

    async def _autojoin_boosters(self, ctx: commands.Context, view: JoinView) -> int:
        role = ctx.guild.get_role(BOOSTER_ROLE_ID)
        if not role:
            return 0
        added = 0
        for m in role.members:
            if m not in view.joined:
                view.joined.append(m)
                added += 1
        if added:
            await ctx.send(f"💠 Auto-joined **{added}** server boosters.")
        return added

    async def _countdown(self, ctx: commands.Context, boss: str):
        for delay, text in [
            (300, f"**{boss} will be vulnerable in 10 minutes**"),
            (300, f"**{boss} will be vulnerable in 5 minutes**"),
            (180, f"**{boss} will be vulnerable in 2 minutes**"),
            (60,  f"**{boss} will be vulnerable in 1 minute**"),
            (30,  f"**{boss} will be vulnerable in 30 seconds**"),
            (20,  f"**{boss} will be vulnerable in 20 seconds**"),
            (10,  f"**{boss} will be vulnerable in 10 seconds**"),
        ]:
            await asyncio.sleep(delay)
            await ctx.send(text)

    # ------- LADON -------

    @commands.command(hidden=True, brief="Start a Ladon (pet) raid")
    @is_gm()
    @raid_channel()
    async def spawn_ladon(self, ctx: commands.Context, element: str, hp: int, crate_rarity: str = "legendary"):
        """Usage: $spawn_ladon <element> <hp> [crate_rarity]"""
        element = element.title()
        if element not in ELEMENTS:
            return await ctx.send(f"Invalid element. Choose from: {', '.join(ELEMENTS)}")
        crate_rarity = crate_rarity.lower()
        if crate_rarity not in ALLOWED_CRATE_RARITIES:
            return await ctx.send("Invalid crate rarity. " + ", ".join(sorted(ALLOWED_CRATE_RARITIES)))

        try: await ctx.message.delete()
        except Exception: pass

        view = JoinView(self.bot, require_pet=True, timeout=60*15)

        warning = (
            "Only your **equipped pet** will join the battle. If you don’t have one equipped, "
            "clicking **Join** will warn you to equip first (`/pet equip`)."
        )
        em = discord.Embed(
            title=f"Ladon approaches ({element})",
            description=f"{warning}\n\n**Ladon will be vulnerable in 15 minutes.**\nHP: **{hp:,}**",
            colour=0x2AA55A
        )
        em.set_image(url=LADON_START_IMG)
        await ctx.send(embed=em, view=view)

        role = ctx.guild.get_role(RAIDER_ROLE_ID)
        mention = role.mention if role else f"<@&{RAIDER_ROLE_ID}>"
        await ctx.send(f"{mention} **Ladon ({element}) spawned!** Join with the button below. Vulnerable in 15 minutes.",
                       allowed_mentions=discord.AllowedMentions(roles=True))

        # Auto-join for Ladon
        await self._autojoin_patrons(ctx, view)
        await self._autojoin_boosters(ctx, view)

        await self._countdown(ctx, f"Ladon ({element})")

        view.stop()
        await ctx.send("**Boss is vulnerable! Fetching participants…**")
        players = list(view.joined)
        engine = LadonEngine(self.bot, ctx, boss_element=element, boss_hp=hp, crate_rarity=crate_rarity)
        engine.participants_count = len(players)
        await ctx.send(f"**{engine.participants_count} participants** have joined the raid.")
        await engine.enroll(players)
        await engine.run()

    # ------- TALOS -------

    @commands.command(hidden=True, brief="Start a Talos raid")
    @is_gm()
    @raid_channel()
    async def spawn_talos(self, ctx: commands.Context, hp: int):
        """Usage: $spawn_talos <hp>"""
        try: await ctx.message.delete()
        except Exception: pass

        view = JoinView(self.bot, require_pet=False, timeout=60*15)

        em = discord.Embed(
            title="Talos approaches",
            description="A bronze giant tramples the horizon. **Vulnerable in 15 minutes.**",
            colour=0x996600
        )
        em.set_image(url=TALOS_START_IMG)
        await ctx.send(embed=em, view=view)

        role = ctx.guild.get_role(RAIDER_ROLE_ID)
        mention = role.mention if role else f"<@&{RAIDER_ROLE_ID}>"
        await ctx.send(f"{mention} **Talos spawned!** Join with the button below. Vulnerable in 15 minutes.",
                       allowed_mentions=discord.AllowedMentions(roles=True))

        await self._countdown(ctx, "Talos")

        view.stop()
        await ctx.send("**Boss is vulnerable! Fetching participants…**")
        players = list(view.joined)
        engine = TalosEngine(self.bot, ctx, boss_hp=hp)
        engine.participants_count = len(players)
        await ctx.send(f"**{engine.participants_count} participants** have joined the raid.")
        await engine.enroll(players)
        await engine.run()


async def setup(bot: commands.Bot):
    await bot.add_cog(LadonRaid(bot))
