import datetime
import random
from typing import Optional, Literal
import textwrap
import asyncio
import json


import discord
from discord.ext import commands

from utils.checks import is_gm  # adjust path if needed

Faction = Literal["silencer", "melonite"]
LanternTarget = Literal["thanatos", "melonia"]

LANTERN_ESSENCE_COST = 66
STAFF_CHANNEL_ID = 1446892125668245686  # channel where orders get reported


# Faction buffs leaders/GM can buy with FWP
BUFFS = {
    "silencer": {
        "grave_silence": {
            "name": "Grave Silence",
            "cost": 60,
            "description": "Once per battle, heavily dampens one Melonite round (backline & magic).",
        },
        "veil_fortress": {
            "name": "Veil Fortress",
            "cost": 80,
            "description": "Passive: Silencers overall power is slightly increased and damage taken reduced.",
        },
        "midnight_requiem": {
            "name": "Midnight Requiem",
            "cost": 100,
            "description": "Final round tilt in Silencers' favor if the fight is close.",
        },
    },
    "melonite": {
        "spotlight_blind": {
            "name": "Spotlight Blind",
            "cost": 60,
            "description": "Once per battle, a wave of stage lights blinds Silencer vanguard and backline.",
        },
        "idol_allure": {
            "name": "Idol Allure",
            "cost": 80,
            "description": "Passive: Part of Silencer backline power is seduced away.",
        },
        "encore_frenzy": {
            "name": "Encore Frenzy",
            "cost": 100,
            "description": "Passive: Melonites get a strong boost to their final push.",
        },
    },
}

# Shop items using Echo Shards (winter_tokens)
# Types:
#  - "crate"    → adds to crate columns on profile
#  - "potion"   → upserts into user_consumables
#  - "virtual"  → creates an order in winter_shop_orders (handled by staff)
#  - "drachma"  → converts Echo Shards into drachmas (dragoncoins)

SHOP_ITEMS = {
    # ──────────────── CRATES ────────────────
    "crate_mystery": {
        "name": "Mystery Crate",
        "cost": 3,
        "description": "A sealed crate from distant realms. Contains random loot (existing Mystery crate rewards).",
        "type": "crate",
        "crate_field": "crates_mystery",
    },
    "crate_fortune": {
        "name": "Fortune Crate",
        "cost": 4,
        "description": "Packed with luck and rare finds. Higher value than Legendary crates.",
        "type": "crate",
        "crate_field": "crates_fortune",
    },
    "crate_legendary": {
        "name": "Legendary Crate",
        "cost": 3,
        "description": "A crate imbued with ancient power. Contains legendary-tier loot.",
        "type": "crate",
        "crate_field": "crates_legendary",
    },
    "crate_divine": {
        "name": "Divine Crate",
        "cost": 10,
        "description": "Blessed by forgotten gods. Contains high-end, divine loot.",
        "type": "crate",
        "crate_field": "crates_divine",
    },

    # ──────────────── PET POTIONS ────────────────
    # Make sure the consumable_type strings match exactly your existing ones
    "potion_pet_age": {
        "name": "Pet Age Potion",
        "cost": 3,
        "description": "Instantly matures a pet by one growth stage.",
        "type": "potion",
        "consumable_type": "pet_age_potion",
    },
    "potion_pet_xp": {
        "name": "Pet XP Potion",
        "cost": 2,
        "description": "Grants bonus experience to one pet.",
        "type": "potion",
        "consumable_type": "pet_xp_potion",  # adjust if your actual key differs
    },
    "potion_pet_growth": {
        "name": "Pet Growth Speed Potion",
        "cost": 4,
        "description": "Speeds up pet growth timers.",
        "type": "potion",
        "consumable_type": "pet_speed_growth_potion",  # adjust if needed
    },

    # ──────────────── PANDORA’S FROSTGIFT ────────────────
    # For now treated as a VIRTUAL order so staff can handle rolling
    "pandora_frostgift": {
        "name": "Pandora’s Frostgift",
        "cost": 5,
        "description": (
            "A mysterious Greek winter gift. When opened by the staff, "
            "it creates a random weapon (1H: up to 100 dmg, 2H: up to 200 dmg, "
            "or armor up to 100), with a random existing type and element."
        ),
        "type": "virtual",  # handled via fv shop fulfill + custom GM flow
    },

    # ──────────────── VIRTUAL GOODIES (keep existing) ────────────────
    "song_unlock": {
        "name": "Mono Melonía Track Unlock",
        "cost": 5,
        "description": "Spend Echo Shards to unlock one track from Mono Melonía's album. Staff will handle the actual unlock.",
        "type": "virtual",
    },
    "secret_pet": {
        "name": "Secret Idol Contract",
        "cost": 20,
        "description": "A mysterious contract with an unknown idol... redeemable at the end for a chosen Mono Melonía member pet.",
        "type": "virtual",
    },

    # ──────────────── DRACHMAS (dragoncoins) ────────────────
    # Uses the 'dragoncoins' column on profile as drachma currency
    "drachma_small": {
        "name": "Small Drachma Pouch",
        "cost": 5,
        "description": "Convert Echo Shards into 15 drachmas (used in DC shop).",
        "type": "drachma",
        "amount": 15,
    },
    "drachma_large": {
        "name": "Large Drachma Pouch",
        "cost": 10,
        "description": "Convert Echo Shards into 45 drachmas (used in DC shop).",
        "type": "drachma",
        "amount": 45,
    },
}


FROSTGIFT_PREFIXES = {
    "fire": ["Emberbrand", "Pyrestorm", "Blazewrought", "Cinderreach"],
    "water": ["Tidelock", "Frostflow", "Icewrought", "Glacial Surge"],
    "electric": ["Stormlash", "Thunderchain", "Shockspire", "Voltbreaker"],
    "nature": ["Wildroot", "Thornbite", "Verdant Cry", "Bloomrend"],
    "light": ["Dawnsworn", "Radiant Prism", "Sunshard", "Haloedge"],
    "dark": ["Nightwrought", "Umbrafang", "Dreadcaller", "Voidchant"],
    "corrupted": ["Blightpiercer", "Rotbringer", "Corruptide", "Sinweave"],
    "wind": ["Galevein", "Skyshatter", "Windcleaver", "Tempestborn"],
}

FROSTGIFT_SUFFIXES = {
    # one-handed types
    "sword": ["Blade", "Edge", "Shard"],
    "dagger": ["Fang", "Blade", "Shard"],
    "hammer": ["Hammer", "Breaker"],
    "spear": ["Spear", "Piercer"],
    "knife": ["Knife", "Shard", "Fang"],
    "axe": ["Axe", "Cutter", "Cleaver"],
    "wand": ["Wand", "Focus", "Channel"],
    "shield": ["Aegis", "Guard", "Bulwark"],

    # two-handed
    "bow": ["Warbow", "Longbow", "Recurve"],
    "scythe": ["Scythe", "Reaver", "Doomscythe"],
    "mace": ["Greatblade", "Crusher", "Maul"],
}

class FrostedVeil(commands.Cog):
    """
    The Frosted Veil Festival event cog.

    Requires:

    - profile table with a 'user' column as PK and winter_* columns added.
    - monster_pets table as you described.
    - winter_event_state table (see SQL).
    - winter_shop_orders table (for virtual orders).
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # TODO: adjust dates to your real schedule
        self.event_start = datetime.datetime(2025, 12, 2, 0, 0, 0, tzinfo=datetime.timezone.utc)
        self.event_end = datetime.datetime(2025, 12, 27, 0, 0, 0, tzinfo=datetime.timezone.utc)

        # Phase split
        self.phase2_start = datetime.datetime(2025, 12, 10, 0, 0, 0, tzinfo=datetime.timezone.utc)
        self.phase3_start = datetime.datetime(2025, 12, 20, 0, 0, 0, tzinfo=datetime.timezone.utc)

        # Manual showdown limits per faction and squad (None = no limit)
        self.vanguard_limit: dict[Faction, Optional[int]] = {
            "silencer": None,
            "melonite": None,
        }
        self.backline_limit: dict[Faction, Optional[int]] = {
            "silencer": None,
            "melonite": None,
        }

    # ─────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────

    def _now(self) -> datetime.datetime:
        return datetime.datetime.now(datetime.timezone.utc)

    def get_phase(self) -> int:
        """Return current phase (0 = not started, 4 = ended)."""
        now = self._now()
        if now < self.event_start:
            return 0
        if now >= self.event_end:
            return 4
        if now < self.phase2_start:
            return 1
        if now < self.phase3_start:
            return 2
        return 3

    async def fetch_profile(self, user_id: int) -> Optional[dict]:
        """Fetch a profile row as dict."""
        async with self.bot.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM profile WHERE \"user\" = $1", user_id)
        return dict(row) if row else None

    async def ensure_profile(self, user_id: int) -> dict:
        """Make sure a profile exists; create if needed."""
        profile = await self.fetch_profile(user_id)
        if profile:
            return profile
        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO profile (\"user\") VALUES ($1) ON CONFLICT (\"user\") DO NOTHING",
                user_id,
            )
        return await self.fetch_profile(user_id)

    async def update_profile(self, user_id: int, **fields) -> None:
        """Generic profile update helper."""
        if not fields:
            return
        cols = ", ".join(f"{k} = ${i+2}" for i, k in enumerate(fields.keys()))
        values = list(fields.values())
        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                f"UPDATE profile SET {cols} WHERE \"user\" = $1",
                user_id,
                *values,
            )

    async def add_essences(self, user_id: int, essences: int = 0, fragments: int = 0, wisps: int = 0):
        """Public helper for other cogs to award event currencies."""
        profile = await self.ensure_profile(user_id)
        new_ess = profile["winter_glacial_essences"] + essences
        new_frag = profile["winter_lantern_fragments"] + fragments
        new_wisps = profile["winter_wisps"] + wisps
        await self.update_profile(
            user_id,
            winter_glacial_essences=new_ess,
            winter_lantern_fragments=new_frag,
            winter_wisps=new_wisps,
        )

    async def add_fwp(self, user_id: int, amount: int):
        """
        Public helper to add Faction War Points.
        Final win is mainly based on lantern totals,
        FWP can be used for leaderboards / buffs / tiebreakers.
        """
        profile = await self.ensure_profile(user_id)
        new_fwp = profile["winter_fwp"] + amount
        await self.update_profile(user_id, winter_fwp=new_fwp)

        faction: Optional[str] = profile["winter_faction"]
        if faction not in ("silencer", "melonite"):
            return
        async with self.bot.pool.acquire() as conn:
            if faction == "silencer":
                await conn.execute(
                    "INSERT INTO winter_event_state (id, fwp_silencers) VALUES (1, $1) "
                    "ON CONFLICT (id) DO UPDATE SET fwp_silencers = winter_event_state.fwp_silencers + $1",
                    amount,
                )
            else:
                await conn.execute(
                    "INSERT INTO winter_event_state (id, fwp_melonites) VALUES (1, $1) "
                    "ON CONFLICT (id) DO UPDATE SET fwp_melonites = winter_event_state.fwp_melonites + $1",
                    amount,
                )

    async def get_global_state(self) -> dict:
        async with self.bot.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM winter_event_state WHERE id = 1")
        if row is None:
            return {
                "lanterns_thanatos": 0,
                "lanterns_melonia": 0,
                "fwp_silencers": 0,
                "fwp_melonites": 0,
                "silencers_buffs": [],
                "melonites_buffs": [],
            }

        d = dict(row)

        # ✅ jsonb might arrive as str depending on driver/codecs
        for k in ("silencers_buffs", "melonites_buffs"):
            v = d.get(k, [])
            if v is None:
                d[k] = []
            elif isinstance(v, str):
                try:
                    d[k] = json.loads(v)
                except Exception:
                    d[k] = []
            elif not isinstance(v, list):
                # safety fallback
                d[k] = list(v) if hasattr(v, "__iter__") else []

        return d

    async def incr_global_lanterns(self, target: LanternTarget, amount: int = 1):
        """Increase global lantern counters."""
        async with self.bot.pool.acquire() as conn:
            if target == "thanatos":
                await conn.execute(
                    "INSERT INTO winter_event_state (id, lanterns_thanatos) VALUES (1, $1) "
                    "ON CONFLICT (id) DO UPDATE SET lanterns_thanatos = winter_event_state.lanterns_thanatos + $1",
                    amount,
                )
            else:
                await conn.execute(
                    "INSERT INTO winter_event_state (id, lanterns_melonia) VALUES (1, $1) "
                    "ON CONFLICT (id) DO UPDATE SET lanterns_melonia = winter_event_state.lanterns_melonia + $1",
                    amount,
                )

    # ─────────────────────────────────────────────────────────────
    # Command group & join
    # ─────────────────────────────────────────────────────────────

    @commands.group(name="frostedveil", aliases=["fv"], invoke_without_command=True)
    async def frostedveil(self, ctx: commands.Context):
        """Main Winter Event command. Alias: fv"""
        phase = self.get_phase()
        profile = await self.ensure_profile(ctx.author.id)
        state = await self.get_global_state()

        ess = profile["winter_glacial_essences"]
        shards = profile["winter_tokens"]
        faction = profile["winter_faction"] or "None"
        joined = profile.get("winter_joined", False)

        embed = discord.Embed(
            title="❄️ The Frosted Veil Festival",
            colour=discord.Colour.blurple(),
        )
        embed.add_field(name="Phase", value=str(phase), inline=True)
        embed.add_field(name="Joined", value="Yes" if joined else "No", inline=True)
        embed.add_field(
            name="Faction",
            value=faction.capitalize() if faction != "None" else "None",
            inline=True,
        )
        embed.add_field(name="Your Glacial Essences", value=str(ess), inline=True)
        embed.add_field(name="Your Echo Shards", value=str(shards), inline=True)

        embed.add_field(
            name="Global Lanterns",
            value=(
                f"🕯️ Thanatos: {state.get('lanterns_thanatos', 0)}\n"
                f"🎤 Mono Melonía: {state.get('lanterns_melonia', 0)}"
            ),
            inline=False,
        )

        lines = [
            "1️⃣ Join the event with `fv join`.",
            "2️⃣ Fight PvE, Tower, Adventures to earn **Glacial Essences**.",
            f"3️⃣ Use `fv lantern repair` to craft **Winter Lanterns** (cost: {LANTERN_ESSENCE_COST} essences).",
            "4️⃣ Use `fv lantern donate` to support your god (auto-targets your faction), "
            "or specify `<thanatos|melonia>`.",
            "5️⃣ GMs assign factions & showdown squads. Use `fv showdown register` to join the final war.",
        ]
        embed.add_field(
            name="How to play",
            value="\n".join(lines),
            inline=False,
        )

        embed.set_footer(text="Use fv info for lore and phase details.")
        await ctx.send(embed=embed)

    @frostedveil.command(name="join")
    async def frostedveil_join(self, ctx: commands.Context):
        """Join the Frosted Veil event."""
        phase = self.get_phase()
        if phase == 0:
            return await ctx.send("The Frosted Veil Festival has not started yet.")
        if phase >= 4:
            return await ctx.send("The Frosted Veil Festival is already over.")

        profile = await self.ensure_profile(ctx.author.id)
        if profile.get("winter_joined"):
            return await ctx.send("You have already joined the Frosted Veil Festival.")

        await self.update_profile(ctx.author.id, winter_joined=True)
        await ctx.send(
            "❄️ You have joined the **Frosted Veil Festival**!\n"
            "You can now start collecting Glacial Essences and repairing lanterns."
        )

    # ─────────────────────────────────────────────────────────────
    # Info / stats / participants
    # ─────────────────────────────────────────────────────────────

    @frostedveil.command(name="info")
    async def frostedveil_info(self, ctx: commands.Context):
        """Detailed event info and phases."""
        phase = self.get_phase()
        embed = discord.Embed(
            title="❄️ The Frosted Veil Festival — Information",
            colour=discord.Colour.teal(),
            description=(
                "Winter has thinned the veil between life and death.\n"
                "Thanatos seeks silence and order, while Mono Melonía and their Kalli fans chase chaos and concerts.\n\n"
                "Collect **Glacial Essences**, repair **Winter Lanterns**, and choose which side your lanterns support."
            ),
        )
        embed.add_field(
            name="Phase 1 – Gathering & Lanterns",
            value="Fight and explore to earn Glacial Essences, then repair lanterns.",
            inline=False,
        )
        embed.add_field(
            name="Phase 2 – Factions & War Prep",
            value="GMs assign you to **Silencers** (Thanatos) or **Melonites** (Mono Melonía). Lantern donations raise each side's power.",
            inline=False,
        )
        embed.add_field(
            name="Phase 3 – Showdown",
            value="Final simulated battle between both factions using participating players and their pets.",
            inline=False,
        )
        embed.add_field(
            name="Current Phase",
            value=str(phase),
            inline=False,
        )
        await ctx.send(embed=embed)

    @frostedveil.command(name="stats")
    async def frostedveil_stats(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        """Show personal event stats."""
        target = member or ctx.author
        profile = await self.ensure_profile(target.id)

        embed = discord.Embed(
            title=f"❄️ Frosted Veil Stats — {target.display_name}",
            colour=discord.Colour.blue(),
        )
        embed.add_field(
            name="Joined event",
            value="Yes" if profile.get("winter_joined") else "No",
            inline=True,
        )
        embed.add_field(name="Faction", value=(profile["winter_faction"] or "None"), inline=True)
        embed.add_field(name="Glacial Essences", value=profile["winter_glacial_essences"], inline=True)
        embed.add_field(name="Lantern Fragments", value=profile["winter_lantern_fragments"], inline=True)
        embed.add_field(name="Lanterns Crafted", value=profile["winter_lanterns_crafted"], inline=True)
        embed.add_field(name="Donated to Thanatos", value=profile["winter_lanterns_donated_thanatos"], inline=True)
        embed.add_field(name="Donated to Melonía", value=profile["winter_lanterns_donated_melonia"], inline=True)
        embed.add_field(name="Echo Shards", value=profile["winter_tokens"], inline=True)
        embed.add_field(name="Faction War Points (personal)", value=profile["winter_fwp"], inline=True)
        embed.add_field(name="Showdown Role", value=(profile["winter_showdown_role"] or "None"), inline=True)

        await ctx.send(embed=embed)

    @frostedveil.command(name="participants")
    @is_gm()
    async def frostedveil_participants(self, ctx: commands.Context):
        """GM: list all event participants and their lantern donations."""
        async with self.bot.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT \"user\", winter_faction, "
                "winter_lanterns_donated_thanatos, winter_lanterns_donated_melonia "
                "FROM profile "
                "WHERE winter_joined = TRUE "
                "ORDER BY (winter_lanterns_donated_thanatos + winter_lanterns_donated_melonia) DESC;"
            )

        if not rows:
            return await ctx.send("No participants yet.")

        lines = []
        for row in rows:
            uid = row["user"]
            user = self.bot.get_user(uid)
            name = user.mention if user else f"User {uid}"
            fac = row["winter_faction"] or "None"
            t = row["winter_lanterns_donated_thanatos"]
            m = row["winter_lanterns_donated_melonia"]
            total = t + m
            lines.append(
                f"{name} — Faction: `{fac}` — Lanterns → Thanatos: {t}, Melonía: {m}, Total: {total}"
            )

        chunks = []
        current = ""
        for line in lines:
            if len(current) + len(line) + 1 > 1900:
                chunks.append(current)
                current = ""
            current += line + "\n"
        if current:
            chunks.append(current)

        for i, chunk in enumerate(chunks, start=1):
            embed = discord.Embed(
                title=f"❄️ Frosted Veil Participants (page {i}/{len(chunks)})",
                description=chunk,
                colour=discord.Colour.dark_blue(),
            )
            await ctx.send(embed=embed)

    # ─────────────────────────────────────────────────────────────
    # GM Rewards: give essences / shards / lanterns
    # ─────────────────────────────────────────────────────────────

    @frostedveil.command(name="giveessences")
    @is_gm()
    async def frostedveil_give_essences(
        self,
        ctx: commands.Context,
        member: discord.Member,
        amount: int,
    ):
        """
        GM: Give Glacial Essences to a player.
        Usage: fv giveessences @User 50
        """
        if amount == 0:
            return await ctx.send("Amount must be non-zero.")
        if amount < 0:
            return await ctx.send("Use a positive amount. (No removing via this command.)")

        profile = await self.ensure_profile(member.id)
        new_ess = profile["winter_glacial_essences"] + amount

        if new_ess < 0:
            new_ess = 0

        await self.update_profile(
            member.id,
            winter_glacial_essences=new_ess,
        )

        await ctx.send(
            f"✅ Gave **{amount}** Glacial Essences to {member.mention}.\n"
            f"They now have **{new_ess}** Glacial Essences."
        )

    @frostedveil.command(name="giveshards")
    @is_gm()
    async def frostedveil_give_shards(
        self,
        ctx: commands.Context,
        member: discord.Member,
        amount: int,
    ):
        """
        GM: Give Echo Shards (winter_tokens) to a player.
        Usage: fv giveshards @User 5
        """
        if amount == 0:
            return await ctx.send("Amount must be non-zero.")
        if amount < 0:
            return await ctx.send("Use a positive amount. (No removing via this command.)")

        profile = await self.ensure_profile(member.id)
        new_tokens = profile["winter_tokens"] + amount

        if new_tokens < 0:
            new_tokens = 0

        await self.update_profile(
            member.id,
            winter_tokens=new_tokens,
        )

        await ctx.send(
            f"✅ Gave **{amount}** Echo Shards to {member.mention}.\n"
            f"They now have **{new_tokens}** Echo Shards."
        )

    @frostedveil.command(name="givelanterns")
    @is_gm()
    async def frostedveil_give_lanterns(
        self,
        ctx: commands.Context,
        member: discord.Member,
        amount: int,
    ):
        """
        GM: Give repaired Winter Lanterns to a player (adds to their crafted pool).
        They still need to use `fv lantern donate` to choose a side.

        Usage: fv givelanterns @User 3
        """
        if amount == 0:
            return await ctx.send("Amount must be non-zero.")
        if amount < 0:
            return await ctx.send("Use a positive amount. (No removing via this command.)")

        profile = await self.ensure_profile(member.id)
        new_crafted = profile["winter_lanterns_crafted"] + amount

        if new_crafted < 0:
            new_crafted = 0

        await self.update_profile(
            member.id,
            winter_lanterns_crafted=new_crafted,
        )

        await ctx.send(
            f"✅ Gave **{amount}** repaired Winter Lantern(s) to {member.mention}.\n"
            f"They now have **{new_crafted}** lantern(s) ready to donate."
        )


    # ─────────────────────────────────────────────────────────────
    # Lanterns
    # ─────────────────────────────────────────────────────────────

    @frostedveil.command(name="lantern")
    async def frostedveil_lantern(self, ctx: commands.Context, action: str, target: Optional[str] = None):
        """
        Lantern subcommands:
        fv lantern repair
        fv lantern donate [thanatos|melonia]

        If no target is given when donating, your faction's god is used.
        """
        action = action.lower()
        profile = await self.ensure_profile(ctx.author.id)

        if not profile.get("winter_joined"):
            return await ctx.send(
                "You must first join the event with `fv join` before using lantern commands."
            )

        if action == "repair":
            if self.get_phase() == 0:
                return await ctx.send("The event has not started yet.")
            ess = profile["winter_glacial_essences"]
            if ess < LANTERN_ESSENCE_COST:
                return await ctx.send(
                    f"You need **{LANTERN_ESSENCE_COST}** Glacial Essences to repair a lantern. "
                    f"You currently have {ess}."
                )
            new_ess = ess - LANTERN_ESSENCE_COST
            new_crafted = profile["winter_lanterns_crafted"] + 1
            new_shards = profile["winter_tokens"] + 1  # 1 Echo Shard per lantern craft
            await self.update_profile(
                ctx.author.id,
                winter_glacial_essences=new_ess,
                winter_lanterns_crafted=new_crafted,
                winter_tokens=new_shards,
            )
            return await ctx.send(
                f"🕯️ You repaired a **Winter Lantern**! "
                f"(Crafted: {new_crafted}, Essences left: {new_ess}, Echo Shards: {new_shards})"
            )

        elif action == "donate":
            # Determine target: explicit or faction auto
            if target is None:
                fac = profile["winter_faction"]
                if fac == "silencer":
                    target_l: LanternTarget = "thanatos"
                elif fac == "melonite":
                    target_l = "melonia"
                else:
                    return await ctx.send(
                        "You have no faction yet. Ask a GM to assign you, or specify "
                        "`fv lantern donate thanatos` or `melonia` explicitly."
                    )
            else:
                if target.lower() not in ("thanatos", "melonia"):
                    return await ctx.send("Target must be `thanatos` or `melonia`.")
                target_l = target.lower()  # type: ignore

            if profile["winter_lanterns_crafted"] <= 0:
                return await ctx.send("You have no repaired lanterns to donate.")

            new_crafted = profile["winter_lanterns_crafted"] - 1
            fields = {"winter_lanterns_crafted": new_crafted}
            if target_l == "thanatos":
                fields["winter_lanterns_donated_thanatos"] = profile["winter_lanterns_donated_thanatos"] + 1
            else:
                fields["winter_lanterns_donated_melonia"] = profile["winter_lanterns_donated_melonia"] + 1
            await self.update_profile(ctx.author.id, **fields)

            await self.incr_global_lanterns(target_l, 1)
            await self.add_fwp(ctx.author.id, 1)  # optional leaderboard / buff currency

            state = await self.get_global_state()
            await ctx.send(
                f"🕯️ You donated a lantern to **{target_l.capitalize()}**!\n"
                f"Global Lanterns — Thanatos: {state.get('lanterns_thanatos', 0)}, "
                f"Melonía: {state.get('lanterns_melonia', 0)}"
            )

        else:
            await ctx.send("Usage: `fv lantern repair` or `fv lantern donate [thanatos|melonia]`")

    # ─────────────────────────────────────────────────────────────
    # Factions (GM assigns)
    # ─────────────────────────────────────────────────────────────

    @frostedveil.group(name="faction", invoke_without_command=True)
    async def frostedveil_faction(self, ctx: commands.Context):
        """Faction subcommands: set (GM), status, top, buffs, buybuff."""
        await ctx.send("Subcommands: `set` (GM), `status`, `top`, `buffs`, `buybuff`.")

    @frostedveil_faction.command(name="set")
    @is_gm()
    async def frostedveil_faction_set(
        self,
        ctx: commands.Context,
        member: discord.Member,
        faction: str,
    ):
        """
        GM: assign a player to a faction manually.
        Usage:
        fv faction set @User silencer
        fv faction set @User melonite
        """
        faction = faction.lower()
        if faction not in ("silencer", "melonite"):
            return await ctx.send("Faction must be `silencer` or `melonite`.")

        profile = await self.ensure_profile(member.id)
        if not profile.get("winter_joined"):
            return await ctx.send("That player has not joined the event yet (`fv join`).")

        await self.update_profile(member.id, winter_faction=faction)
        await ctx.send(
            f"✅ {member.mention} has been assigned to the **{faction.capitalize()}** faction."
        )

    @frostedveil_faction.command(name="status")
    async def frostedveil_faction_status(self, ctx: commands.Context):
        """Show global faction status."""
        state = await self.get_global_state()
        sil_fw = state.get("fwp_silencers", 0)
        mel_fw = state.get("fwp_melonites", 0)
        th_l = state.get("lanterns_thanatos", 0)
        mm_l = state.get("lanterns_melonia", 0)

        total_l = max(th_l + mm_l, 1)
        sil_pct = int(th_l / total_l * 10)
        mel_pct = 10 - sil_pct

        bar = "⚫" * sil_pct + "🎤" * mel_pct
        embed = discord.Embed(
            title="⚔️ Frosted Veil — Faction Status",
            colour=discord.Colour.dark_teal(),
        )
        embed.add_field(
            name="Lantern Donations",
            value=f"Thanatos: {th_l}\nMono Melonía: {mm_l}",
            inline=True,
        )
        embed.add_field(
            name="Faction War Points",
            value=f"Silencers: {sil_fw}\nMelonites: {mel_fw}",
            inline=True,
        )
        embed.add_field(
            name="Veil Balance",
            value=bar,
            inline=False,
        )
        await ctx.send(embed=embed)

    @frostedveil_faction.command(name="top")
    async def frostedveil_faction_top(self, ctx: commands.Context):
        """Show top contributors by FWP."""
        async with self.bot.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT \"user\", winter_fwp FROM profile "
                "WHERE winter_fwp > 0 "
                "ORDER BY winter_fwp DESC LIMIT 10"
            )
        if not rows:
            return await ctx.send("No faction war contributions yet.")

        lines = []
        for i, row in enumerate(rows, start=1):
            user = self.bot.get_user(row["user"])
            name = user.mention if user else f"User {row['user']}"
            lines.append(f"**{i}.** {name} — {row['winter_fwp']} FWP")

        embed = discord.Embed(
            title="🏆 Top Faction Contributors",
            description="\n".join(lines),
            colour=discord.Colour.gold(),
        )
        await ctx.send(embed=embed)

    @frostedveil_faction.command(name="buffs")
    async def frostedveil_faction_buffs(self, ctx: commands.Context):
        """Show available faction buffs and which ones are active."""
        state = await self.get_global_state()
        active_s = set(state.get("silencers_buffs", []))
        active_m = set(state.get("melonites_buffs", []))

        lines_s = []
        for buff_id, data in BUFFS["silencer"].items():
            status = "ACTIVE ✅" if buff_id in active_s else "Inactive"
            lines_s.append(
                f"**{data['name']}** (`{buff_id}`) — Cost: {data['cost']} FWP — {status}\n{data['description']}"
            )

        lines_m = []
        for buff_id, data in BUFFS["melonite"].items():
            status = "ACTIVE ✅" if buff_id in active_m else "Inactive"
            lines_m.append(
                f"**{data['name']}** (`{buff_id}`) — Cost: {data['cost']} FWP — {status}\n{data['description']}"
            )

        embed = discord.Embed(
            title="✨ Frosted Veil Faction Buffs",
            colour=discord.Colour.purple(),
        )
        embed.add_field(
            name="Thanatos — Silencers",
            value="\n\n".join(lines_s) if lines_s else "None",
            inline=False,
        )
        embed.add_field(
            name="Mono Melonía — Melonites",
            value="\n\n".join(lines_m) if lines_m else "None",
            inline=False,
        )
        await ctx.send(embed=embed)

    @frostedveil_faction.command(name="buybuff")
    @is_gm()
    async def frostedveil_faction_buybuff(self, ctx, faction: str, buff_id: str):

        faction = faction.lower()
        if faction not in ("silencer", "melonite"):
            return await ctx.send("Faction must be `silencer` or `melonite`.")

        if buff_id not in BUFFS[faction]:
            return await ctx.send(f"Unknown buff `{buff_id}` for faction `{faction}`.")

        state = await self.get_global_state()
        buff_data = BUFFS[faction][buff_id]
        cost = buff_data["cost"]

        if faction == "silencer":
            current_fwp = int(state.get("fwp_silencers", 0))
            active = set(state.get("silencers_buffs", []))
        else:
            current_fwp = int(state.get("fwp_melonites", 0))
            active = set(state.get("melonites_buffs", []))

        if buff_id in active:
            return await ctx.send("This buff is already active.")

        if current_fwp < cost:
            return await ctx.send(
                f"Not enough faction war points. `{faction}` has {current_fwp} FWP, needs {cost}."
            )

        active.add(buff_id)
        new_active = list(active)

        async with self.bot.pool.acquire() as conn:
            if faction == "silencer":
                await conn.execute(
                    """
                    UPDATE winter_event_state
                    SET fwp_silencers = $1,
                        silencers_buffs = $2::jsonb
                    WHERE id = 1
                    """,
                    current_fwp - cost,
                    new_active,
                )
            else:
                await conn.execute(
                    """
                    UPDATE winter_event_state
                    SET fwp_melonites = $1,
                        melonites_buffs = $2::jsonb
                    WHERE id = 1
                    """,
                    current_fwp - cost,
                    new_active,
                )

        await ctx.send(
            f"✅ Buff **{buff_data['name']}** (`{buff_id}`) purchased for `{faction}` "
            f"for {cost} FWP."
        )


    # ─────────────────────────────────────────────────────────────
    # Shop: Echo Shards
    # ─────────────────────────────────────────────────────────────

    @frostedveil.group(name="shop", invoke_without_command=True)
    async def frostedveil_shop(self, ctx: commands.Context):
        """Show the Frosted Veil shop. Alias: fv shop"""
        profile = await self.ensure_profile(ctx.author.id)
        tokens = profile["winter_tokens"]

        lines = [f"You have **{tokens}** Echo Shards.\n"]
        for item_id, data in SHOP_ITEMS.items():
            lines.append(
                f"`{item_id}` — **{data['cost']}** Echo Shards — **{data['name']}**\n"
                f"→ {data['description']}"
            )

        embed = discord.Embed(
            title="🛒 Frosted Veil Shop",
            description="\n\n".join(lines),
            colour=discord.Colour.dark_blue(),
        )
        embed.set_footer(text="Use fv shop buy <item_id> to purchase.")
        await ctx.send(embed=embed)

    @frostedveil_shop.command(name="buy")
    async def frostedveil_shop_buy(self, ctx: commands.Context, item_id: str):
        """Buy an item from the Frosted Veil shop: fv shop buy <item_id>"""
        item_id = item_id.lower()
        if item_id not in SHOP_ITEMS:
            return await ctx.send("Unknown item. Use `fv shop` to see available items.")

        item = SHOP_ITEMS[item_id]
        profile = await self.ensure_profile(ctx.author.id)
        tokens = profile["winter_tokens"]
        cost = item["cost"]

        if tokens < cost:
            return await ctx.send(
                f"You do not have enough Echo Shards. "
                f"Item `{item_id}` costs {cost}, you have {tokens}."
            )

        # Deduct Echo Shards
        new_tokens = tokens - cost
        await self.update_profile(ctx.author.id, winter_tokens=new_tokens)

        itype = item["type"]
        msg_effect: str = ""

        # ───────────── CRATES ─────────────
        if itype == "crate":
            crate_field = item["crate_field"]
            current = profile.get(crate_field, 0) or 0
            new_value = current + 1
            await self.update_profile(ctx.author.id, **{crate_field: new_value})
            msg_effect = f"You received **1 {item['name']}** (you now have {new_value})."

        # ───────────── POTIONS (user_consumables) ─────────────
        elif itype == "potion":
            ctype = item["consumable_type"]
            async with self.bot.pool.acquire() as conn:
                # Try update existing row
                updated = await conn.execute(
                    """
                    UPDATE user_consumables
                    SET quantity = quantity + 1
                    WHERE user_id = $1 AND consumable_type = $2;
                    """,
                    ctx.author.id,
                    ctype,
                )
                # If no row updated, insert new
                if updated == "UPDATE 0":
                    await conn.execute(
                        """
                        INSERT INTO user_consumables (user_id, consumable_type, quantity)
                        VALUES ($1, $2, 1);
                        """,
                        ctx.author.id,
                        ctype,
                    )
            msg_effect = f"You received **1x {item['name']}**."

        # ───────────── DRACHMAS (dragoncoins) ─────────────
        elif itype == "drachma":
            amount = int(item.get("amount", 0))
            current_dc = profile.get("dragoncoins", 0) or 0
            new_dc = current_dc + amount
            await self.update_profile(ctx.author.id, dragoncoins=new_dc)
            msg_effect = f"You received **{amount} drachmas** (dragoncoins)."

        # ───────────── VIRTUAL ITEMS (song unlock, secret pet, Pandora’s Frostgift) ─────────────
        else:
            # virtual: song_unlock, secret_pet, pandora_frostgift, etc.
            # Create order and ping staff channel
            async with self.bot.pool.acquire() as conn:
                order_row = await conn.fetchrow(
                    "INSERT INTO winter_shop_orders (user_id, item_id) VALUES ($1, $2) RETURNING id",
                    ctx.author.id,
                    item_id,
                )
            order_id = order_row["id"]
            staff_message = (
                f"📦 **New Frosted Veil Order**\n"
                f"Order ID: `{order_id}`\n"
                f"User: {ctx.author.mention} (ID: {ctx.author.id})\n"
                f"Item: `{item_id}` — **{item['name']}**\n\n"
                f"Use `fv shop fulfill {order_id}` once you've granted the role/song/pet/weapon."
            )
            channel = self.bot.get_channel(STAFF_CHANNEL_ID)
            if channel:
                try:
                    await channel.send(staff_message)
                except Exception:
                    pass

            msg_effect = (
                "Your order has been confirmed, the gods will send it your way soon. "
                f"(Order ID: `{order_id}`)"
            )

        await ctx.send(
            f"✅ You bought **{item['name']}** for **{cost}** Echo Shards.\n"
            f"{msg_effect}\n"
            f"You have **{new_tokens}** Echo Shards left."
        )


    @frostedveil_shop.command(name="fulfill")
    @is_gm()
    async def frostedveil_shop_fulfill(self, ctx: commands.Context, order_id: int):
        """
        GM: mark a shop order as fulfilled and notify the user via DM.

        Usage:
        fv shop fulfill <order_id>
        """
        async with self.bot.pool.acquire() as conn:
            order = await conn.fetchrow(
                "SELECT id, user_id, item_id, fulfilled "
                "FROM winter_shop_orders WHERE id = $1",
                order_id,
            )
        if not order:
            return await ctx.send(f"No order with ID `{order_id}` found.")

        if order["fulfilled"]:
            return await ctx.send(f"Order `{order_id}` is already fulfilled.")

        item_id = order["item_id"]
        item = SHOP_ITEMS.get(item_id)
        item_name = item["name"] if item else item_id

        # Mark fulfilled
        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                "UPDATE winter_shop_orders SET fulfilled = TRUE WHERE id = $1",
                order_id,
            )

        # DM user
        user_id = order["user_id"]
        user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
        try:
            await user.send(f"🎁 The gods have delivered your **{item_name}** (Order `{order_id}`).")
        except Exception:
            await ctx.send("Order fulfilled, but I couldn't DM the user (DMs closed?).")

        await ctx.send(f"✅ Order `{order_id}` marked as fulfilled and user has been notified (if possible).")

    # ─────────────────────────────────────────────────────────────
    # Pandoras Frostgift — GM tools (SUBCOMMAND OF fv)
    # ─────────────────────────────────────────────────────────────

    @frostedveil.group(name="frostgift", invoke_without_command=True)
    @is_gm()
    async def frostedveil_frostgift(self, ctx: commands.Context):
        """GM: Pandora's Frostgift tools. Usage: fv frostgift roll <order_id>"""
        await ctx.send("Subcommands: `roll <order_id>` (GM only).")


    @frostedveil_frostgift.command(name="roll")
    @is_gm()
    async def frostedveil_frostgift_roll(self, ctx: commands.Context, order_id: int):
        """
        GM: Roll a Pandora's Frostgift and output a ready-to-copy $gmitem command.
        Does NOT create the item automatically.
        """

        async with self.bot.pool.acquire() as conn:
            order = await conn.fetchrow(
                "SELECT id, user_id, item_id, fulfilled "
                "FROM winter_shop_orders WHERE id = $1",
                order_id,
            )

        if not order:
            return await ctx.send(f"No order with ID `{order_id}` found.")

        if order["item_id"] != "pandora_frostgift":
            return await ctx.send(
                f"Order `{order_id}` is for `{order['item_id']}`, not `pandora_frostgift`."
            )

        user_id = int(order["user_id"])
        user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)

        one_h_types = ["Sword", "Dagger", "Hammer", "Spear", "Knife", "Axe", "Wand"]
        two_h_types = ["Bow", "Scythe", "Mace"]
        shield_type = "Shield"
        elements = ["Dark", "Corrupted", "Water", "Fire", "Electric", "Nature", "Light", "Wind"]

        category = random.choices(["1h", "2h", "shield"], weights=[60, 30, 10], k=1)[0]

        if category == "1h":
            item_type = random.choice(one_h_types)
            stat = random.randint(10, 100)
        elif category == "2h":
            item_type = random.choice(two_h_types)
            stat = random.randint(35, 100)
        else:
            item_type = shield_type
            stat = random.randint(10, 100)

        element = random.choice(elements)

        value = min(stat * random.randint(120, 300), 100_000_000)

        prefix_pool = {
            "Dark": ["Umbral", "Voidbound", "Eclipsed"],
            "Corrupted": ["Blighted", "Riftborn", "Cursed"],
            "Water": ["Tideforged", "Deepsea", "Frostwave"],
            "Fire": ["Emberforged", "Cinder", "Infernal"],
            "Electric": ["Stormforged", "Voltbound", "Thunder"],
            "Nature": ["Wildroot", "Verdant", "Groveborn"],
            "Light": ["Radiant", "Sunforged", "Haloed"],
            "Wind": ["Galewept", "Skyforged", "Tempest"],
        }
        suffix_pool = {
            "Sword": ["Blade", "Edge", "Fang"],
            "Dagger": ["Sting", "Shard", "Needle"],
            "Hammer": ["Maul", "Breaker", "Anvil"],
            "Spear": ["Lance", "Pike", "Spine"],
            "Knife": ["Razor", "Carver", "Slice"],
            "Axe": ["Reaver", "Splitter", "Cleave"],
            "Wand": ["Scepter", "Rod", "Spire"],
            "Bow": ["Piercer", "Stormshot", "Longbow"],
            "Scythe": ["Reaper", "Harvest", "Crescent"],
            "Mace": ["Knell", "Skullcrack", "Smasher"],
            "Shield": ["Bulwark", "Aegis", "Bastion"],
        }

        name = f"{random.choice(prefix_pool[element])} {random.choice(suffix_pool[item_type])}"

        # IMPORTANT: use mention form because your gm command expects <owner> as a discord user
        gm_command = f'$gmitem {stat} <@{user_id}> {item_type} {element} {value} "{name}"'

        await ctx.send(
            "🎁 **Pandora's Frostgift Rolled**\n"
            f"Order: `{order_id}`\n"
            f"Target: {user.mention if user else f'<@{user_id}>'}\n"
            f"Type: **{item_type}** | Element: **{element}** | Stat: **{stat}** | Value: **{value}**\n"
            f"Name: **{name}**\n\n"
            "**Copy/paste:**\n"
            f"`{gm_command}`"
        )


    # ─────────────────────────────────────────────────────────────
    # Showdown: register & status
    # ─────────────────────────────────────────────────────────────

    @frostedveil.group(name="showdown", invoke_without_command=True)
    async def frostedveil_showdown(self, ctx: commands.Context):
        """Final showdown: register, status, admin, run. Alias: fv showdown"""
        await ctx.send("Subcommands: `register`, `status`, `admin`, `run`, `debug`, `testrun` (GM).")

    @frostedveil_showdown.command(name="register")
    async def frostedveil_showdown_register(self, ctx: commands.Context):
        """
        Player registers as a candidate for the final showdown.
        GM will manually assign vanguard/backline later.
        """
        phase = self.get_phase()
        if phase < 2:
            return await ctx.send("Showdown registration is not open yet.")
        profile = await self.ensure_profile(ctx.author.id)
        if not profile.get("winter_joined"):
            return await ctx.send("You must join the event first with `fv join`.")
        if not profile["winter_faction"]:
            return await ctx.send("You must have a faction assigned by a GM first.")
        if profile["winter_showdown_role"] in ("vanguard", "backline", "candidate"):
            return await ctx.send("You are already registered for the showdown.")

        await self.update_profile(ctx.author.id, winter_showdown_role="candidate")
        await ctx.send("✅ You are now registered as a **candidate** for the final showdown.")

    @frostedveil_showdown.command(name="status")
    async def frostedveil_showdown_status(self, ctx: commands.Context):
        """Show current registered candidates and assigned squads."""
        async with self.bot.pool.acquire() as conn:
            rows = await conn.fetch(
                'SELECT "user", winter_faction, winter_showdown_role '
                "FROM profile "
                "WHERE winter_showdown_role IS NOT NULL"
            )

        if not rows:
            return await ctx.send("No one is registered for the showdown yet.")

        sil_vanguard = []
        sil_backline = []
        sil_candidates = []
        mel_vanguard = []
        mel_backline = []
        mel_candidates = []

        for row in rows:
            uid = row["user"]
            faction = row["winter_faction"]
            role = row["winter_showdown_role"]
            user = self.bot.get_user(uid)
            name = user.mention if user else f"User {uid}"

            if faction == "silencer":
                if role == "vanguard":
                    sil_vanguard.append(name)
                elif role == "backline":
                    sil_backline.append(name)
                else:
                    sil_candidates.append(name)
            elif faction == "melonite":
                if role == "vanguard":
                    mel_vanguard.append(name)
                elif role == "backline":
                    mel_backline.append(name)
                else:
                    mel_candidates.append(name)

        def fmt(lst):
            return ", ".join(lst) if lst else "None"

        embed = discord.Embed(
            title="⚔️ Showdown Registration Status",
            colour=discord.Colour.dark_purple(),
        )
        embed.add_field(
            name="Silencers — Vanguard",
            value=fmt(sil_vanguard),
            inline=False,
        )
        embed.add_field(
            name="Silencers — Backline",
            value=fmt(sil_backline),
            inline=False,
        )
        embed.add_field(
            name="Silencers — Candidates",
            value=fmt(sil_candidates),
            inline=False,
        )
        embed.add_field(
            name="Melonites — Vanguard",
            value=fmt(mel_vanguard),
            inline=False,
        )
        embed.add_field(
            name="Melonites — Backline",
            value=fmt(mel_backline),
            inline=False,
        )
        embed.add_field(
            name="Melonites — Candidates",
            value=fmt(mel_candidates),
            inline=False,
        )

        await ctx.send(embed=embed)

    # ─────────────────────────────────────────────────────────────
    # Showdown admin (GM): set limits, assign squads
    # ─────────────────────────────────────────────────────────────

    @frostedveil_showdown.group(name="admin", invoke_without_command=True)
    @is_gm()
    async def frostedveil_showdown_admin(self, ctx: commands.Context):
        """GM tools for showdown (set limits, assign squads)."""
        await ctx.send("Subcommands: `setlimit`, `assign`.")

    @frostedveil_showdown_admin.command(name="setlimit")
    @is_gm()
    async def showdown_admin_setlimit(
        self,
        ctx: commands.Context,
        faction: str,
        role: str,
        limit: Optional[int],
    ):
        """
        GM: Set max number of vanguard/backline per faction.
        limit=None or -1 => no limit.
        Example: fv showdown admin setlimit silencer vanguard 5
        """
        faction = faction.lower()
        role = role.lower()
        if faction not in ("silencer", "melonite"):
            return await ctx.send("Faction must be `silencer` or `melonite`.")
        if role not in ("vanguard", "backline"):
            return await ctx.send("Role must be `vanguard` or `backline`.")

        if limit is None or limit < 0:
            val = None
        else:
            val = int(limit)

        if role == "vanguard":
            self.vanguard_limit[faction] = val
        else:
            self.backline_limit[faction] = val

        await ctx.send(
            f"✅ Limit for **{faction} {role}** set to "
            f"{'no limit' if val is None else val}."
        )

    @frostedveil_showdown_admin.command(name="assign")
    @is_gm()
    async def showdown_admin_assign(
        self,
        ctx: commands.Context,
        member: discord.Member,
        role: str,
    ):
        """
        GM: manually assign a registered player to vanguard/backline.
        Example: fv showdown admin assign @User vanguard
        """
        role = role.lower()
        if role not in ("vanguard", "backline"):
            return await ctx.send("Role must be `vanguard` or `backline`.")

        profile = await self.ensure_profile(member.id)
        faction: Optional[str] = profile["winter_faction"]
        if faction not in ("silencer", "melonite"):
            return await ctx.send("That player has not been assigned to a faction yet.")

        # Check limit
        async with self.bot.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COUNT(*) AS c FROM profile "
                "WHERE winter_faction = $1 AND winter_showdown_role = $2",
                faction,
                role,
            )
        current_count = row["c"] if row else 0

        limit = self.vanguard_limit[faction] if role == "vanguard" else self.backline_limit[faction]
        if limit is not None and current_count >= limit:
            return await ctx.send(
                f"Limit reached for **{faction} {role}** (limit {limit}). "
                f"Current: {current_count}."
            )

        await self.update_profile(member.id, winter_showdown_role=role)
        await ctx.send(
            f"✅ {member.mention} has been assigned to **{faction.capitalize()} {role.capitalize()}**."
        )

    # ─────────────────────────────────────────────────────────────
    # SHOWDOWN — GM RUN FINAL CINEMATIC FIGHT
    # ─────────────────────────────────────────────────────────────

    @frostedveil_showdown.command(name="run")
    @is_gm()
    async def frostedveil_showdown_run(self, ctx: commands.Context):
        """
        GM: Run the FINAL showdown simulation.

        - Uses showdown squads from DB (vanguard / backline only).
        - Uses real player stats, pet stats and purchased faction buffs from DB.
        - Lanterns matter: +5 effective power per lantern.
        - Runs as an HP battle over several rounds until one side hits 0 HP.
        - Sends a setup summary + round messages with short delays between them.
        """

        # ---------------- PHASE CHECK ----------------
        phase = self.get_phase()
        if phase < 3:
            return await ctx.send("The showdown cannot be run before Phase 3.")

        # ------------- CONFIG FOR FINAL BATTLE -------------
        HP_FACTOR = 25.0      # team HP = final_power * HP_FACTOR
        DMG_FACTOR = 1.5      # base per-round damage = final_power * DMG_FACTOR
        MAX_ROUNDS = 12       # safety cap; usually ends earlier
        ROUND_DELAY = 25      # seconds between rounds

        # ------------- GLOBAL EVENT STATE (REAL DATA) -------------
        state = await self.get_global_state()
        th_lanterns = state.get("lanterns_thanatos", 0)
        mm_lanterns = state.get("lanterns_melonia", 0)
        silencers_buffs = set(state.get("silencers_buffs", []))
        melonites_buffs = set(state.get("melonites_buffs", []))

        # ------------- SQUADS: VANGUARD + BACKLINE FROM DB -------------
        async with self.bot.pool.acquire() as conn:
            rows = await conn.fetch(
                'SELECT "user", winter_faction, winter_showdown_role '
                "FROM profile WHERE winter_showdown_role IN ('vanguard', 'backline')"
            )

        if not rows:
            return await ctx.send("No participants in the showdown!")

        silencers_vanguard_ids: list[int] = []
        silencers_backline_ids: list[int] = []
        melonites_vanguard_ids: list[int] = []
        melonites_backline_ids: list[int] = []

        for row in rows:
            uid = row["user"]
            fac = row["winter_faction"]
            role = row["winter_showdown_role"]

            if fac == "silencer":
                if role == "vanguard":
                    silencers_vanguard_ids.append(uid)
                else:
                    silencers_backline_ids.append(uid)
            elif fac == "melonite":
                if role == "vanguard":
                    melonites_vanguard_ids.append(uid)
                else:
                    melonites_backline_ids.append(uid)

        all_ids = list(
            set(
                silencers_vanguard_ids
                + silencers_backline_ids
                + melonites_vanguard_ids
                + melonites_backline_ids
            )
        )
        if not all_ids:
            return await ctx.send("No valid participants with squads (check factions & roles).")

        # ------------- LOAD PETS & PROFILES -------------
        async with self.bot.pool.acquire() as conn:
            monsters = await conn.fetch(
                "SELECT owner_id, hp, attack, defense, level, trust_level "
                "FROM monster_pets WHERE equipped = TRUE AND owner_id = ANY($1::bigint[])",
                all_ids,
            )
        pet_stats = {m["owner_id"]: m for m in monsters}

        async with self.bot.pool.acquire() as conn:
            profiles = await conn.fetch(
                'SELECT "user", statatk, statdef, stathp, atkmultiply, defmultiply, '
                "luck, tier, xp "
                "FROM profile WHERE \"user\" = ANY($1::bigint[])",
                all_ids,
            )
        prof_stats = {p["user"]: p for p in profiles}

        # ------------- POWER FORMULAS (SAME AS TEST, REAL BUFFS) -------------

        def detailed_power(uid: int) -> float:
            """Return combined player power for this player (pet + char)."""
            pet = pet_stats.get(uid)
            prof = prof_stats.get(uid)
            if not pet or not prof:
                return 0.0

            # Pet base stats
            hp = pet["hp"]
            atk = pet["attack"]
            defense = pet["defense"]
            level = pet["level"]
            trust = pet["trust_level"]

            # Player stats (cast numerics, handle NULL)
            stat_atk = float(prof["statatk"] or 0)
            stat_def = float(prof["statdef"] or 0)
            stat_hp = float(prof["stathp"] or 0)
            atk_mul = float(prof["atkmultiply"] or 1.0)
            def_mul = float(prof["defmultiply"] or 1.0)
            luck = float(prof["luck"] or 1.0)
            tier = float(prof["tier"] or 0)
            xp = float(prof["xp"] or 0)

            pet_power = (
                hp * 0.25
                + atk * 0.6
                + defense * 0.4
                + level * 6
                + trust * 3
            )

            char_power = (
                stat_hp * 0.3
                + stat_atk * 0.6
                + stat_def * 0.4
            )
            char_power *= (atk_mul + def_mul) / 2.0
            char_power += luck * 5.0
            char_power += tier * 10.0
            char_power += (xp ** 0.5)

            total = pet_power * 0.7 + char_power * 0.3
            return total

        def score_team(
            vanguard_ids: list[int],
            backline_ids: list[int],
            faction: Faction,
        ) -> float:
            """
            Compute team raw power BEFORE lanterns / faction buffs.

            Silencer:
              - Vanguard: +5%   (1.05)
              - Backline: +20%  (1.20)

            Melonite:
              - Vanguard: +15%  (1.15)
              - Backline: +10%  (1.10)
            """
            total = 0.0

            for uid in vanguard_ids:
                p_total = detailed_power(uid)
                if p_total <= 0:
                    continue
                if faction == "silencer":
                    mult = 1.05
                else:
                    mult = 1.15
                total += p_total * mult

            for uid in backline_ids:
                p_total = detailed_power(uid)
                if p_total <= 0:
                    continue
                if faction == "silencer":
                    mult = 1.20
                else:
                    mult = 1.10
                total += p_total * mult

            return total

        # ------------- BASE TEAM POWER (NO LANTERNS, NO BUFFS) -------------
        base_s = score_team(
            silencers_vanguard_ids,
            silencers_backline_ids,
            "silencer",
        )
        base_m = score_team(
            melonites_vanguard_ids,
            melonites_backline_ids,
            "melonite",
        )

        # ------------- LANTERN EFFECT (+5 POWER PER LANTERN) -------------
        after_lantern_s = base_s + th_lanterns * 5
        after_lantern_m = base_m + mm_lanterns * 5

        # ------------- APPLY PASSIVE BUFFS (GLOBAL MULTIPLIERS) -------------
        final_s = after_lantern_s
        final_m = after_lantern_m

        if "veil_fortress" in silencers_buffs:
            final_s *= 1.10
        if "idol_allure" in melonites_buffs:
            final_s *= 0.93
            final_m *= 1.05
        if "encore_frenzy" in melonites_buffs:
            final_m *= 1.10

        # ------------- HP POOLS & BASE DAMAGE -------------
        max_hp_s = final_s * HP_FACTOR
        max_hp_m = final_m * HP_FACTOR
        cur_hp_s = max_hp_s
        cur_hp_m = max_hp_m

        base_dmg_s = final_s * DMG_FACTOR
        base_dmg_m = final_m * DMG_FACTOR

        # ------------- DISPLAY HELPERS -------------
        def fmt_team_list(ids: list[int]) -> str:
            names = []
            for uid in ids:
                user = self.bot.get_user(uid)
                if user:
                    names.append(user.display_name)
            return ", ".join(names) if names else "no one"

        def pick_attacker(ids: list[int]) -> str:
            valid = [self.bot.get_user(i) for i in ids if self.bot.get_user(i)]
            if not valid:
                return "someone"
            u = random.choice(valid)
            return u.display_name

        def fmt_buffs_names(buffs: set[str], faction: str) -> str:
            if not buffs:
                return "None"
            return ", ".join(BUFFS[faction][b]["name"] for b in buffs)

        # ------------- INTRO / SETUP ANNOUNCEMENT -------------
        intro_lines = [
            "⚔️❄️ **FINAL SHOWDOWN — The Battle for the Frosted Veil**",
            "",
            "The snow falls in heavy, soundless sheets over the Veil.",
            "Lanterns hover between worlds, trembling between **Thanatos** and **Mono Melonía**.",
            "",
            "🕯️ **Lantern Totals**",
            f"• Thanatos (Silencers): **{th_lanterns}**",
            f"• Mono Melonía (Melonites): **{mm_lanterns}**",
            "",
            "👥 **Showdown Squads**",
            f"• Silencer Vanguard: {fmt_team_list(silencers_vanguard_ids)}",
            f"• Silencer Backline: {fmt_team_list(silencers_backline_ids)}",
            f"• Melonite Vanguard: {fmt_team_list(melonites_vanguard_ids)}",
            f"• Melonite Backline: {fmt_team_list(melonites_backline_ids)}",
            "",
            "✨ **Faction Buffs in Play**",
            f"• Silencers: {fmt_buffs_names(silencers_buffs, 'silencer')}",
            f"• Melonites: {fmt_buffs_names(melonites_buffs, 'melonite')}",
            "",
            "❤️ **Team HP Pools (derived from lanterns + stats + buffs)**",
            f"• Silencers HP ≈ **{int(max_hp_s)}**",
            f"• Melonites HP ≈ **{int(max_hp_m)}**",
            "",
            "_The Veil shivers. Thanatos lowers his scythe. Mono Melonía raises the mic._",
        ]
        await ctx.send("\n".join(intro_lines))

        # ------------- ROUND LOOP -------------
        winner: Optional[Faction] = None

        for rnd in range(1, MAX_ROUNDS + 1):
            if cur_hp_s <= 0 or cur_hp_m <= 0:
                break

            # Base randomised damage
            sil_dmg = base_dmg_s * random.uniform(0.4, 0.8)
            mel_dmg = base_dmg_m * random.uniform(0.4, 0.8)

            # Momentum: the losing side hits slightly harder
            if cur_hp_s < cur_hp_m:
                sil_dmg *= 1.08
            elif cur_hp_m < cur_hp_s:
                mel_dmg *= 1.08

            # Buff-driven per-round tweaks

            # Grave Silence: Melonite damage reduced mid-fight
            if "grave_silence" in silencers_buffs and 3 <= rnd <= 5:
                mel_dmg *= 0.75

            # Spotlight Blind: Silencer damage reduced early
            if "spotlight_blind" in melonites_buffs and rnd <= 2:
                sil_dmg *= 0.80

            # Midnight Requiem: Silencers stronger in last rounds
            if "midnight_requiem" in silencers_buffs and rnd >= MAX_ROUNDS - 2:
                sil_dmg *= 1.08

            # Encore Frenzy: Melonites stronger in last rounds
            if "encore_frenzy" in melonites_buffs and rnd >= MAX_ROUNDS - 2:
                mel_dmg *= 1.10

            # Apply damage
            cur_hp_m = max(cur_hp_m - sil_dmg, 0)
            cur_hp_s = max(cur_hp_s - mel_dmg, 0)

            # Pick "face" attackers for narration
            sil_attacker = pick_attacker(silencers_vanguard_ids + silencers_backline_ids)
            mel_attacker = pick_attacker(melonites_vanguard_ids + melonites_backline_ids)

            # Round flavor
            round_header = f"__Round {rnd} — the Veil screams__"
            lines = [round_header]

            # Silencer strike
            lines.append(
                f"• **{sil_attacker}**'s companion surges from the frost, "
                f"carving into the Melonite line for **{int(sil_dmg)}** damage. "
                f"_Melonites HP now ≈ {int(cur_hp_m)}._"
            )

            # Melonite strike
            lines.append(
                f"• **{mel_attacker}** rides the beat into the fray, "
                f"their pet detonating in neon sound for **{int(mel_dmg)}** damage. "
                f"_Silencers HP now ≈ {int(cur_hp_s)}._"
            )

            # Who "won" the round
            if sil_dmg > mel_dmg:
                lines.append("→ This round leans **towards Silencers** — the hush hits harder than the chorus.")
            elif mel_dmg > sil_dmg:
                lines.append("→ This round leans **towards Melonites** — every hit lands on the downbeat.")
            else:
                lines.append("→ Perfectly matched impacts. The Veil can’t decide which side hurts more.")

            await ctx.send("\n".join(lines))

            # KO checks
            if cur_hp_s <= 0 and cur_hp_m <= 0:
                # Double KO → lanterns first, then buffs count, then RNG
                if th_lanterns > mm_lanterns:
                    winner = "silencer"
                elif mm_lanterns > th_lanterns:
                    winner = "melonite"
                else:
                    if len(silencers_buffs) > len(melonites_buffs):
                        winner = "silencer"
                    elif len(melonites_buffs) > len(silencers_buffs):
                        winner = "melonite"
                    else:
                        winner = "silencer" if random.random() < 0.5 else "melonite"
                break
            elif cur_hp_s <= 0:
                winner = "melonite"
                break
            elif cur_hp_m <= 0:
                winner = "silencer"
                break

            if rnd != MAX_ROUNDS:
                try:
                    await asyncio.sleep(ROUND_DELAY)
                except Exception:
                    break

        # If no KO after max rounds, decide by remaining HP, then lanterns/buffs
        if winner is None:
            if cur_hp_s > cur_hp_m:
                winner = "silencer"
            elif cur_hp_m > cur_hp_s:
                winner = "melonite"
            else:
                if th_lanterns > mm_lanterns:
                    winner = "silencer"
                elif mm_lanterns > th_lanterns:
                    winner = "melonite"
                else:
                    if len(silencers_buffs) > len(melonites_buffs):
                        winner = "silencer"
                    elif len(melonites_buffs) > len(silencers_buffs):
                        winner = "melonite"
                    else:
                        winner = "silencer" if random.random() < 0.5 else "melonite"

        # ------------- FINALE TEXT -------------
        if winner == "silencer":
            finale = [
                "🏁 **The Frosted Veil falls silent.**",
                "",
                f"Final HP — Silencers: **{int(cur_hp_s)}**, Melonites: **{int(cur_hp_m)}**",
                "",
                "Lanterns steady into constellations above Thanatos, each flame a soul returned to order.",
                "Mono Melonía stand in the snow, breath steaming, instruments still humming with defiance.",
                "",
                "Thanatos does not smile. But he lowers his scythe.",
                "",
                "**Thanatos’ Silencers win the Battle for the Frosted Veil.**",
            ]
        else:
            finale = [
                "🏁 **The Veil explodes into sound.**",
                "",
                f"Final HP — Silencers: **{int(cur_hp_s)}**, Melonites: **{int(cur_hp_m)}**",
                "",
                "Lanterns burst into neon scaffolds, forming a stage over the battlefield.",
                "Mono Melonía step forward, one eye shining, the crowd of Kalli roaring in the snow.",
                "",
                "Thanatos watches from the edge of the chaos, cloak billowing in the bass.",
                "",
                "**Mono Melonía’s Melonites win the Battle for the Frosted Veil.**",
            ]

        await ctx.send("\n".join(finale))


    # ─────────────────────────────────────────────────────────────
    # SHOWDOWN TEST RUN — RANDOM SQUADS
    # ─────────────────────────────────────────────────────────────

    @frostedveil_showdown.command(name="testrun")
    @is_gm()
    async def frostedveil_showdown_testrun(self, ctx: commands.Context):
        """
        GM: Run a TEST version of the showdown.

        - Uses ALL players who joined the event and have a faction.
        - Randomly assigns them to vanguard/backline in their faction.
        - Uses same player/pet power logic as the real showdown.
        - Lanterns matter again: +5 effective power per lantern.
        - Buffs are RANDOMLY picked from BUFFS for each faction (TEST ONLY),
          so you can re-run and see how much they swing things.
        - Prints a numeric breakdown at the end to explain where HP / damage
          numbers came from.

        Usage: fv showdown testrun
        """

        await ctx.send("🧪 Starting Frosted Veil showdown **test run**… (random buffs, real lanterns)")

        # ------------- CONFIG FOR TEST BATTLE -------------
        HP_FACTOR = 25.0      # team HP = final_power * HP_FACTOR
        DMG_FACTOR = 1.5      # base per-round damage = final_power * DMG_FACTOR
        MAX_ROUNDS = 12       # hard safety cap (usually ends earlier when HP ≤ 0)
        ROUND_DELAY = 20      # seconds between rounds (feel free to tune)

        # ------------- GLOBAL STATE (LANTERNS ONLY) -------------
        state = await self.get_global_state()
        th_lanterns = state.get("lanterns_thanatos", 0)
        mm_lanterns = state.get("lanterns_melonia", 0)

        # ------------- RANDOM BUFFS FOR TEST ONLY -------------
        sil_all = list(BUFFS["silencer"].keys())
        mel_all = list(BUFFS["melonite"].keys())

        # pick a random subset for each side (0..len)
        sil_k = random.randint(0, len(sil_all))
        mel_k = random.randint(0, len(mel_all))
        silencers_buffs = set(random.sample(sil_all, k=sil_k))
        melonites_buffs = set(random.sample(mel_all, k=mel_k))

        # ------------- PARTICIPANTS: ALL EVENT PLAYERS WITH FACTION -------------
        async with self.bot.pool.acquire() as conn:
            rows = await conn.fetch(
                'SELECT "user", winter_faction '
                "FROM profile "
                "WHERE winter_joined = TRUE AND winter_faction IN ('silencer', 'melonite')"
            )

        if not rows:
            return await ctx.send("No event participants with factions — cannot run testrun.")

        sil_ids: list[int] = []
        mel_ids: list[int] = []
        for row in rows:
            if row["winter_faction"] == "silencer":
                sil_ids.append(row["user"])
            elif row["winter_faction"] == "melonite":
                mel_ids.append(row["user"])

        if not sil_ids or not mel_ids:
            return await ctx.send("Both factions need at least one participant for testrun.")

        random.shuffle(sil_ids)
        random.shuffle(mel_ids)

        def split_squad(ids: list[int]) -> tuple[list[int], list[int]]:
            """Simple split into vanguard / backline for TEST ONLY."""
            if not ids:
                return [], []
            if len(ids) == 1:
                return ids, []
            mid = max(1, len(ids) // 2)
            return ids[:mid], ids[mid:]

        silencers_vanguard_ids, silencers_backline_ids = split_squad(sil_ids)
        melonites_vanguard_ids, melonites_backline_ids = split_squad(mel_ids)

        all_ids = list(
            set(
                silencers_vanguard_ids
                + silencers_backline_ids
                + melonites_vanguard_ids
                + melonites_backline_ids
            )
        )
        if not all_ids:
            return await ctx.send("Something went wrong: no IDs after squad split.")

        # ------------- LOAD PETS & PROFILES -------------
        async with self.bot.pool.acquire() as conn:
            monsters = await conn.fetch(
                "SELECT owner_id, hp, attack, defense, level, trust_level "
                "FROM monster_pets WHERE equipped = TRUE AND owner_id = ANY($1::bigint[])",
                all_ids,
            )
        pet_stats = {m["owner_id"]: m for m in monsters}

        async with self.bot.pool.acquire() as conn:
            profiles = await conn.fetch(
                'SELECT "user", statatk, statdef, stathp, atkmultiply, defmultiply, '
                "luck, tier, xp "
                "FROM profile WHERE \"user\" = ANY($1::bigint[])",
                all_ids,
            )
        prof_stats = {p["user"]: p for p in profiles}

        # ------------- POWER FORMULAS (SAME AS REAL SHOWDOWN) -------------

        def detailed_power(uid: int) -> tuple[float, float, float]:
            """
            Returns (total_power, pet_power, char_power) for this player.

            total_power = 0.7 * pet_power + 0.3 * char_power
            where:
              pet_power  = hp * 0.25 + atk * 0.6 + defense * 0.4 + level * 6 + trust * 3
              char_power = stathp * 0.3 + statatk * 0.6 + statdef * 0.4,
                           * (atk/def multipliers avg),
                           + luck*5 + tier*10 + sqrt(xp)
            """
            pet = pet_stats.get(uid)
            prof = prof_stats.get(uid)
            if not pet or not prof:
                return 0.0, 0.0, 0.0

            # Pet base stats
            hp = pet["hp"]
            atk = pet["attack"]
            defense = pet["defense"]
            level = pet["level"]
            trust = pet["trust_level"]

            # Player stats (cast numerics, handle NULL)
            stat_atk = float(prof["statatk"] or 0)
            stat_def = float(prof["statdef"] or 0)
            stat_hp = float(prof["stathp"] or 0)
            atk_mul = float(prof["atkmultiply"] or 1.0)
            def_mul = float(prof["defmultiply"] or 1.0)
            luck = float(prof["luck"] or 1.0)
            tier = float(prof["tier"] or 0)
            xp = float(prof["xp"] or 0)

            pet_power = (
                hp * 0.25
                + atk * 0.6
                + defense * 0.4
                + level * 6
                + trust * 3
            )

            char_power = (
                stat_hp * 0.3
                + stat_atk * 0.6
                + stat_def * 0.4
            )
            char_power *= (atk_mul + def_mul) / 2.0
            char_power += luck * 5.0
            char_power += tier * 10.0
            char_power += (xp ** 0.5)

            total = pet_power * 0.7 + char_power * 0.3
            return total, pet_power, char_power

        def score_team(
            vanguard_ids: list[int],
            backline_ids: list[int],
            faction: Faction,
        ) -> tuple[float, float, float]:
            """
            Compute team raw power BEFORE lanterns / buffs.
            Returns (total, pet_component, char_component) with vanguard/backline multipliers applied.

            Silencer:
              - Vanguard: +5%   (1.05)
              - Backline: +20%  (1.20)

            Melonite:
              - Vanguard: +15%  (1.15)
              - Backline: +10%  (1.10)
            """
            total = pet_total = char_total = 0.0

            # Vanguard
            for uid in vanguard_ids:
                p_total, p_pet, p_char = detailed_power(uid)
                if p_total <= 0:
                    continue
                if faction == "silencer":
                    mult = 1.05
                else:
                    mult = 1.15
                total += p_total * mult
                pet_total += p_pet * mult
                char_total += p_char * mult

            # Backline
            for uid in backline_ids:
                p_total, p_pet, p_char = detailed_power(uid)
                if p_total <= 0:
                    continue
                if faction == "silencer":
                    mult = 1.20
                else:
                    mult = 1.10
                total += p_total * mult
                pet_total += p_pet * mult
                char_total += p_char * mult

            return total, pet_total, char_total

        # ------------- BASE TEAM POWER (NO LANTERNS, NO BUFFS) -------------
        base_s, base_s_pet, base_s_char = score_team(
            silencers_vanguard_ids,
            silencers_backline_ids,
            "silencer",
        )
        base_m, base_m_pet, base_m_char = score_team(
            melonites_vanguard_ids,
            melonites_backline_ids,
            "melonite",
        )

        # ------------- LANTERN EFFECT (+5 POWER PER LANTERN) -------------
        after_lantern_s = base_s + th_lanterns * 5
        after_lantern_m = base_m + mm_lanterns * 5

        # ------------- APPLY RANDOM TEST BUFFS (GLOBAL MULTIPLIERS) -------------
        final_s = after_lantern_s
        final_m = after_lantern_m

        buff_notes = []

        if "veil_fortress" in silencers_buffs:
            final_s *= 1.10
            buff_notes.append("Silencers: veil_fortress (+10% total power)")
        if "idol_allure" in melonites_buffs:
            final_s *= 0.93
            final_m *= 1.05
            buff_notes.append("Melonites: idol_allure (Silencers -7%, Melonites +5%)")
        if "encore_frenzy" in melonites_buffs:
            final_m *= 1.10
            buff_notes.append("Melonites: encore_frenzy (+10% total power)")

        # grave_silence / spotlight_blind: in this test version they will
        # impact per-round damage slightly (see below) rather than global power.

        # ------------- HP POOLS & BASE DAMAGE FROM FINAL POWER -------------
        max_hp_s = final_s * HP_FACTOR
        max_hp_m = final_m * HP_FACTOR
        cur_hp_s = max_hp_s
        cur_hp_m = max_hp_m

        base_dmg_s = final_s * DMG_FACTOR
        base_dmg_m = final_m * DMG_FACTOR

        # ------------- UTILS FOR DISPLAY -------------
        def fmt_team_list(ids: list[int]) -> str:
            names = []
            for uid in ids:
                user = self.bot.get_user(uid)
                if user:
                    names.append(user.display_name)
            return ", ".join(names) if names else "no one"

        def fmt_buffs(buffs: set[str], faction: str) -> str:
            if not buffs:
                return "None"
            return ", ".join(BUFFS[faction][b]["name"] for b in buffs)

        # ------------- INTRO MESSAGE (SQUADS, LANTERNS, BUFFS, HP) -------------
        intro_lines = [
            "__[TEST RUN] Frosted Veil Showdown — Random Squads__",
            "",
            f"🕯️ Lanterns in play:",
            f"  • Thanatos: **{th_lanterns}** (Silencers)",
            f"  • Mono Melonía: **{mm_lanterns}** (Melonites)",
            "",
            "👥 **Squads (test random assignment):**",
            f"  • Silencer Vanguard: {fmt_team_list(silencers_vanguard_ids)}",
            f"  • Silencer Backline: {fmt_team_list(silencers_backline_ids)}",
            f"  • Melonite Vanguard: {fmt_team_list(melonites_vanguard_ids)}",
            f"  • Melonite Backline: {fmt_team_list(melonites_backline_ids)}",
            "",
            "✨ **Random test buffs this run (NOT from DB):**",
            f"  • Silencers: {fmt_buffs(silencers_buffs, 'silencer')}",
            f"  • Melonites: {fmt_buffs(melonites_buffs, 'melonite')}",
            "",
            "❤️ **Team HP pools (derived from final power):**",
            f"  • Silencers HP ≈ **{int(max_hp_s)}**",
            f"  • Melonites HP ≈ **{int(max_hp_m)}**",
        ]
        await ctx.send("\n".join(intro_lines))

        # ------------- ROUND LOOP: RUN UNTIL ONE SIDE DROPS TO 0 OR MAX_ROUNDS -------------
        winner: Optional[Faction] = None

        for rnd in range(1, MAX_ROUNDS + 1):
            if cur_hp_s <= 0 or cur_hp_m <= 0:
                break

            # base randomized damage for this round
            # a fraction of base_dmg with some RNG
            sil_dmg = base_dmg_s * random.uniform(0.4, 0.8)
            mel_dmg = base_dmg_m * random.uniform(0.4, 0.8)

            # small "momentum" effect: lower-HP side fights harder
            if cur_hp_s < cur_hp_m:
                sil_dmg *= 1.08
            elif cur_hp_m < cur_hp_s:
                mel_dmg *= 1.08

            # buff-driven per-round tweaks (test simplification)
            # grave_silence: dampens Melonite damage mid-fight
            if "grave_silence" in silencers_buffs and 3 <= rnd <= 5:
                mel_dmg *= 0.75

            # spotlight_blind: dampens Silencer damage early
            if "spotlight_blind" in melonites_buffs and rnd <= 2:
                sil_dmg *= 0.80

            # midnight_requiem: Silencers hit a bit harder in later rounds
            if "midnight_requiem" in silencers_buffs and rnd >= MAX_ROUNDS - 2:
                sil_dmg *= 1.08

            # encore_frenzy: Melonites hit a bit harder in later rounds
            if "encore_frenzy" in melonites_buffs and rnd >= MAX_ROUNDS - 2:
                mel_dmg *= 1.10

            # apply damage
            cur_hp_m = max(cur_hp_m - sil_dmg, 0)
            cur_hp_s = max(cur_hp_s - mel_dmg, 0)

            # round narration
            round_lines = [
                f"__Round {rnd} — test clash__",
                f"Silencers deal **{int(sil_dmg)}** damage. (Melonites HP now ≈ {int(cur_hp_m)})",
                f"Melonites deal **{int(mel_dmg)}** damage. (Silencers HP now ≈ {int(cur_hp_s)})",
            ]

            # Small flavor hint based on which side did more damage that round
            if sil_dmg > mel_dmg:
                round_lines.append("→ This round leans **towards Silencers** — the quiet cuts deeper.")
            elif mel_dmg > sil_dmg:
                round_lines.append("→ This round leans **towards Melonites** — rhythm hits harder.")
            else:
                round_lines.append("→ Perfectly balanced hit-trade this round.")

            await ctx.send("\n".join(round_lines))

            # check for KO
            if cur_hp_s <= 0 and cur_hp_m <= 0:
                # double KO: use lanterns as tiebreaker, then random
                if th_lanterns > mm_lanterns:
                    winner = "silencer"
                elif mm_lanterns > th_lanterns:
                    winner = "melonite"
                else:
                    winner = "silencer" if random.random() < 0.5 else "melonite"
                break
            elif cur_hp_s <= 0:
                winner = "melonite"
                break
            elif cur_hp_m <= 0:
                winner = "silencer"
                break

            # still going, wait before next round
            if rnd != MAX_ROUNDS:
                try:
                    await asyncio.sleep(ROUND_DELAY)
                except Exception:
                    # if sleep gets cancelled by bot shutdown etc, just break gracefully
                    break

        # If nobody died by max rounds, decide by remaining HP (then lanterns as extra weight)
        if winner is None:
            if cur_hp_s > cur_hp_m:
                winner = "silencer"
            elif cur_hp_m > cur_hp_s:
                winner = "melonite"
            else:
                # if HP are extremely close, lanterns decide
                if th_lanterns > mm_lanterns:
                    winner = "silencer"
                elif mm_lanterns > th_lanterns:
                    winner = "melonite"
                else:
                    winner = "silencer" if random.random() < 0.5 else "melonite"

        await ctx.send(
            f"🏁 **[TEST] Battle ends!**\n"
            f"Final HP — Silencers: **{int(cur_hp_s)}**, Melonites: **{int(cur_hp_m)}**\n"
            f"Winner (test): **{'Thanatos’ Silencers' if winner == 'silencer' else 'Mono Melonía’s Melonites'}**"
        )

        # ------------- EXPLANATION / BREAKDOWN MESSAGE -------------
        # This is the dev-facing bit so you see where numbers came from.
        explain_lines = [
            "__[TEST DEBUG] Numbers breakdown__",
            "",
            "1️⃣ **Base power from stats (before lanterns & buffs)**",
            f"• Silencers: {base_s:.1f} total  (pets ≈ {base_s_pet:.1f}, characters ≈ {base_s_char:.1f})",
            f"• Melonites: {base_m:.1f} total  (pets ≈ {base_m_pet:.1f}, characters ≈ {base_m_char:.1f})",
            "",
            "2️⃣ **Lantern effect**",
            "   Formula: +5 effective power per lantern (added on top of base stats).",
            f"• Silencers lanterns = {th_lanterns} → +{th_lanterns * 5:.1f} power",
            f"• Melonites lanterns = {mm_lanterns} → +{mm_lanterns * 5:.1f} power",
            f"→ After lanterns — Silencers: {after_lantern_s:.1f}, Melonites: {after_lantern_m:.1f}",
            "",
            "3️⃣ **Buffs used in THIS testrun (random, not DB)**",
            f"• Silencers buffs: {', '.join(silencers_buffs) if silencers_buffs else 'None'}",
            f"• Melonites buffs: {', '.join(melonites_buffs) if melonites_buffs else 'None'}",
        ]

        if buff_notes:
            explain_lines.append("   Applied effects:")
            explain_lines.extend(f"   - {note}" for note in buff_notes)

        explain_lines.extend([
            "",
            "4️⃣ **Final effective power after lanterns + buffs**",
            f"• Silencers final power: {final_s:.1f}",
            f"• Melonites final power: {final_m:.1f}",
            "",
            "5️⃣ **HP pools**",
            f"Formula: team_HP = final_power × {HP_FACTOR:.1f}",
            f"• Silencers HP ≈ {max_hp_s:.1f}",
            f"• Melonites HP ≈ {max_hp_m:.1f}",
            "",
            "6️⃣ **Base damage per round**",
            f"Formula: base_damage_per_round ≈ final_power × {DMG_FACTOR:.2f}, "
            "then randomised each round and tweaked by buffs + HP momentum.",
            f"• Silencers base dmg/round ≈ {base_dmg_s:.1f}",
            f"• Melonites base dmg/round ≈ {base_dmg_m:.1f}",
            "",
            "7️⃣ **Per-player contribution formula reminder**",
            "For each player:",
            "• pet_power  = hp * 0.25 + atk * 0.6 + defense * 0.4 + level * 6 + trust * 3",
            "• char_power = stathp * 0.3 + statatk * 0.6 + statdef * 0.4,",
            "               then multiplied by average (atkmultiply, defmultiply),",
            "               then + luck*5 + tier*10 + sqrt(xp)",
            "• combined player_power = 0.7 * pet_power + 0.3 * char_power",
            "",
            "Those combined powers are summed (with vanguard/backline multipliers) "
            "to give the base team power above.",
        ])

        # Discord 2000 char limit – split if needed
        explain_text = "\n".join(explain_lines)
        for chunk_start in range(0, len(explain_text), 1900):
            await ctx.send(explain_text[chunk_start:chunk_start + 1900])




async def setup(bot: commands.Bot):
    await bot.add_cog(FrostedVeil(bot))
