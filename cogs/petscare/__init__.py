from __future__ import annotations

"""
The IdleRPG Discord Bot
Copyright (C) 2018-2021 Diniboy and Gelbpunkt
Copyright (C) 2023-2024 Lunar (PrototypeX37)
Copyright (C) 2025 Danaelis

GNU AGPL v3
"""

"""
PetsCare Cog — feeding, bonding, trust, and status utilities for pets.
This cog **does not** declare top-level commands. setup() attaches its
functions under the existing `$pets` command group from `cogs.pets`.
"""

import datetime as dt
from typing import Any, Dict, List, Optional

import asyncpg
import discord
from discord.ext import commands

from utils.i18n import _
from utils import random as urandom  # has randint, choice


class DirectPetFoodSelect(discord.ui.Select):
    def __init__(self, cog: "PetsCare", ctx: commands.Context, pet_id: int):
        self.cog = cog
        self.ctx = ctx
        self.pet_id = pet_id

        options = []
        for key, food in cog.FOOD_TYPES.items():
            label = key.replace("_", " ").title()
            tier_note = " | Warrior+" if food.get("tier_required") else ""
            options.append(
                discord.SelectOption(
                    label=label,
                    description=(
                        f"${int(food['cost']):,} | Hunger +{food['hunger']} | "
                        f"Happy +{food['happiness']}{tier_note}"
                    ),
                    value=key,
                    emoji="🍖" if key != "treats" else "🍬",
                )
            )

        super().__init__(
            placeholder="Choose food for this pet...",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.ctx.author.id:
            return await interaction.response.send_message(
                "This food menu is not for you.", ephemeral=True
            )

        await interaction.response.defer()
        food_key = self.values[0]
        await self.cog.feed(
            self.ctx,
            self.pet_id,
            food_type=food_key.replace("_", " "),
        )

        view = self.view
        if view:
            for child in view.children:
                child.disabled = True
            try:
                await interaction.message.edit(view=view)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass


class DirectPetFoodView(discord.ui.View):
    def __init__(self, cog: "PetsCare", ctx: commands.Context, pet_id: int):
        super().__init__(timeout=45)
        self.ctx = ctx
        self.add_item(DirectPetFoodSelect(cog, ctx, pet_id))

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.gray, emoji="❌", row=1)
    async def cancel_button(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            return await interaction.response.send_message(
                "This food menu is not for you.", ephemeral=True
            )
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="Feed cancelled.", view=self)


class PetsCare(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # --------- Configs ----------
        self.FOOD_TYPES: Dict[str, Dict[str, Any]] = {
            "basic_food":     {"hunger": 50,  "happiness": 25,  "cost": 10000, "trust_gain": 1},
            "premium_food":   {"hunger": 100, "happiness": 50,  "cost": 25000, "trust_gain": 2},
            "deluxe_food":    {"hunger": 100, "happiness": 100, "cost": 50000, "trust_gain": 3},
            "elemental_food": {"hunger": 75,  "happiness": 75,  "cost": 75000, "trust_gain": 4, "tier_required": 2},
            "treats":         {"hunger": 10,  "happiness": 50,  "cost":  5000, "trust_gain": 2},
        }
        self.FOOD_ALIASES: Dict[str, str] = {
            "basic food": "basic_food", "basic": "basic_food",
            "premium food": "premium_food", "premium": "premium_food",
            "deluxe food": "deluxe_food", "deluxe": "deluxe_food",
            "elemental food": "elemental_food", "elemental": "elemental_food",
            "treats": "treats", "treat": "treats",
        }
        # Trust bands and their battle bonuses
        self.TRUST_LEVELS: Dict[int, Dict[str, Any]] = {
            0:  {"name": "Distrustful", "bonus": -20, "emoji": "😠"},
            21: {"name": "Cautious",    "bonus":   0, "emoji": "😐"},
            41: {"name": "Trusting",    "bonus":  10, "emoji": "😊"},
            61: {"name": "Loyal",       "bonus":  20, "emoji": "😍"},
            81: {"name": "Devoted",     "bonus":  30, "emoji": "🥰"},
        }

        # Ensure DB columns exist (non-blocking)
        self.bot.loop.create_task(self.initialize_enhanced_tables())

    # ========= DB & Core Helpers =========

    async def initialize_enhanced_tables(self):
        """Add enhanced columns to monster_pets if missing."""
        try:
            async with self.bot.pool.acquire() as conn:
                await conn.execute(
                    """
                    ALTER TABLE monster_pets 
                    ADD COLUMN IF NOT EXISTS trust_level INTEGER DEFAULT 0,
                    ADD COLUMN IF NOT EXISTS experience  INTEGER DEFAULT 0,
                    ADD COLUMN IF NOT EXISTS level       INTEGER DEFAULT 1,
                    ADD COLUMN IF NOT EXISTS skill_points INTEGER DEFAULT 0,
                    ADD COLUMN IF NOT EXISTS learned_skills JSONB DEFAULT '[]',
                    ADD COLUMN IF NOT EXISTS skill_tree_progress JSONB DEFAULT '{}',
                    ADD COLUMN IF NOT EXISTS xp_multiplier DECIMAL(4,2) DEFAULT 1.0,
                    ADD COLUMN IF NOT EXISTS last_update TIMESTAMP DEFAULT NOW()
                    """
                )
        except Exception as e:
            print(f"[PetsCare] Error initializing enhanced pet tables: {e}")

    def get_trust_level_info(self, trust_level: int) -> Dict[str, Any]:
        for threshold in sorted(self.TRUST_LEVELS.keys(), reverse=True):
            if trust_level >= threshold:
                return self.TRUST_LEVELS[threshold]
        return self.TRUST_LEVELS[0]

    def calculate_level_requirements(self, level: int) -> int:
        return int(100 * (level ** 3))

    def get_skill_points_for_level(self, level: int) -> int:
        return 1 if level % 5 == 0 else 0

    async def gain_experience(
        self,
        pet_id: int,
        xp_amount: int,
        trust_gain: int = 0,
        conn: Optional[asyncpg.Connection] = None,
    ) -> Optional[Dict[str, Any]]:
        async def _gain(conn):
            async with conn.transaction():
                pet = await conn.fetchrow(
                    """SELECT experience, level, trust_level, skill_points, COALESCE(xp_multiplier, 1.0) AS xp_multiplier 
                       FROM monster_pets WHERE id = $1 FOR UPDATE""",
                    pet_id
                )
                if not pet:
                    return None

                xpm = float(pet["xp_multiplier"] or 1.0)
                adj_xp = int(round(xp_amount * xpm))

                new_exp = int(pet["experience"]) + adj_xp
                cur_level = int(pet["level"])
                new_level = cur_level
                new_sp = int(pet["skill_points"])
                new_trust = min(100, int(pet["trust_level"]) + int(trust_gain))

                while new_level < 50 and new_exp >= self.calculate_level_requirements(new_level + 1):
                    new_level += 1
                    new_sp += self.get_skill_points_for_level(new_level)

                updated = await conn.fetchrow(
                    """UPDATE monster_pets 
                       SET experience = $1, level = $2, trust_level = $3, skill_points = $4
                       WHERE id = $5
                       RETURNING experience, level, trust_level, skill_points""",
                    new_exp, new_level, new_trust, new_sp, pet_id
                )

                return {
                    "leveled_up": int(updated["level"]) > cur_level,
                    "new_level": int(updated["level"]),
                    "new_experience": int(updated["experience"]),
                    "new_trust": int(updated["trust_level"]),
                    "skill_points_gained": int(updated["skill_points"]) - int(pet["skill_points"]),
                    "xp_multiplier_applied": xpm > 1.0,
                    "original_xp": xp_amount,
                    "adjusted_xp": adj_xp,
                }

        try:
            if conn is not None:
                return await _gain(conn)
            async with self.bot.pool.acquire() as conn:
                return await _gain(conn)
        except Exception as e:
            print(f"[PetsCare] gain_experience error for pet {pet_id}: {e}")
            return None

    async def handle_pet_death(self, conn: asyncpg.Connection, user_id: int, pet_id: int, pet_name: str):
        try:
            await conn.execute("DELETE FROM monster_pets WHERE id = $1", pet_id)
            user = self.bot.get_user(user_id)
            if user and user.id != 5:
                await user.send(_(f"💀 Your pet **{pet_name}** has perished from starvation..."))
        except Exception as e:
            print(f"[PetsCare] handle_pet_death error: {e}")

    async def handle_pet_runaway(self, conn: asyncpg.Connection, user_id: int, pet_id: int, pet_name: str):
        try:
            await conn.execute("DELETE FROM monster_pets WHERE id = $1", pet_id)
            user = self.bot.get_user(user_id)
            if user and user.id != 5:
                await user.send(_(f"🏃 Your pet **{pet_name}** ran away due to low happiness..."))
        except Exception as e:
            print(f"[PetsCare] handle_pet_runaway error: {e}")

    async def check_pet(self, user_id: int, pet_id: Optional[int] = None) -> List[asyncpg.Record]:
        results: List[asyncpg.Record] = []
        try:
            async with self.bot.pool.acquire() as conn:
                if pet_id:
                    pets = await conn.fetch("SELECT * FROM monster_pets WHERE id = $1", pet_id)
                else:
                    pets = await conn.fetch("SELECT * FROM monster_pets WHERE user_id = $1", user_id)

                now = dt.datetime.utcnow()

                for pet in pets:
                    if pet["growth_stage"] == "adult":
                        results.append(pet)
                        continue

                    last_update = pet["last_update"] or now
                    if last_update.tzinfo is not None:
                        last_update = last_update.replace(tzinfo=None)

                    hours = (now - last_update).total_seconds() / 3600.0

                    if pet["growth_stage"] == "baby":
                        hunger_rate, happy_rate = 10 / 12, 5 / 12
                    elif pet["growth_stage"] == "juvenile":
                        hunger_rate, happy_rate = 8 / 12, 4 / 12
                    else:
                        hunger_rate, happy_rate = 6 / 12, 3 / 12

                    new_hunger = max(0, int(pet["hunger"]) - int(hours * hunger_rate))
                    new_happy = max(0, int(pet["happiness"]) - int(hours * happy_rate))

                    await conn.execute(
                        """UPDATE monster_pets
                           SET hunger = $1, happiness = $2, last_update = $3
                           WHERE id = $4""",
                        new_hunger, new_happy, now, pet["id"]
                    )

                    if new_hunger == 0 and int(pet["user_id"]) != 5:
                        await self.handle_pet_death(conn, pet["user_id"], pet["id"], pet["name"])
                        continue
                    if new_happy == 0 and int(pet["user_id"]) != 5:
                        await self.handle_pet_runaway(conn, pet["user_id"], pet["id"], pet["name"])
                        continue

                    updated = await conn.fetchrow("SELECT * FROM monster_pets WHERE id = $1", pet["id"])
                    if updated:
                        results.append(updated)
        except Exception as e:
            print(f"[PetsCare] check_pet error: {e}")
        return results

    async def _fetch_pet(self, conn, user_id: int, pet_id: int):
        """Return the user's pet row or None."""
        return await conn.fetchrow(
            "SELECT * FROM monster_pets WHERE user_id = $1 AND id = $2",
            user_id, pet_id
        )

    async def _resolve_target_pet(self, conn, ctx: commands.Context, pet_id: Optional[int]):
        """Resolve an explicit pet id, or fall back to the equipped pet."""
        if pet_id is not None:
            pet = await self._fetch_pet(conn, ctx.author.id, pet_id)
            if not pet:
                await ctx.send(f"❌ You don't have a pet with ID {pet_id}.")
                return None, None
            return int(pet_id), pet

        pet = await conn.fetchrow(
            "SELECT * FROM monster_pets WHERE user_id = $1 AND equipped = TRUE",
            ctx.author.id,
        )
        if not pet:
            await ctx.send(
                "❌ No pet ID provided and no pet is equipped. "
                "Use `$pets equip <pet_id>` or pass a pet ID."
            )
            return None, None
        return int(pet["id"]), pet

    # ========= Command bodies (attached under `$pets` group in setup) =========

    async def feed(self, ctx: commands.Context, pet_id: Optional[int] = None, *, food_type: Optional[str] = None):
        """$pets feed [pet_id] [food_type] — Feed your pet with specific food types."""
        if food_type is None:
            async with self.bot.pool.acquire() as conn:
                resolved_pet_id, pet = await self._resolve_target_pet(conn, ctx, pet_id)
            if not pet:
                try:
                    ctx.command.reset_cooldown(ctx)
                except Exception:
                    pass
                return

            return await ctx.send(
                f"Choose food for **{pet['name']}**.",
                view=DirectPetFoodView(self, ctx, resolved_pet_id),
            )

        food_key_in = food_type.lower().strip()
        if food_key_in in self.FOOD_ALIASES:
            food_key = self.FOOD_ALIASES[food_key_in]
        elif food_key_in in self.FOOD_TYPES:
            food_key = food_key_in
        else:
            valid = ", ".join(sorted(self.FOOD_ALIASES.keys()))
            await ctx.send(f"❌ Invalid food type. Valid types: {valid}")
            try:
                ctx.command.reset_cooldown(ctx)
            except Exception:
                pass
            return

        food = self.FOOD_TYPES[food_key]

        async with self.bot.pool.acquire() as conn:
            if food.get("tier_required"):
                tier = await conn.fetchval('SELECT tier FROM profile WHERE "user" = $1', ctx.author.id)
                if tier is None or int(tier) < int(food["tier_required"]):
                    await ctx.send(
                        f"❌ Elemental food requires **Warrior tier** (Tier {food['tier_required']}) or higher. "
                        f"Your current tier: {tier or 0}"
                    )
                    try:
                        ctx.command.reset_cooldown(ctx)
                    except Exception:
                        pass
                    return

            money = await conn.fetchval('SELECT "money" FROM profile WHERE "user" = $1', ctx.author.id)
            if money is None or int(money) < int(food["cost"]):
                await ctx.send(f"❌ You don't have enough money. You need ${food['cost']} for {food_key.replace('_',' ')}.")
                try:
                    ctx.command.reset_cooldown(ctx)
                except Exception:
                    pass
                return

            resolved_pet_id, pet = await self._resolve_target_pet(conn, ctx, pet_id)
            if not pet:
                try:
                    ctx.command.reset_cooldown(ctx)
                except Exception:
                    pass
                return
            pet_id = resolved_pet_id

            new_hunger = min(100, int(pet["hunger"]) + int(food["hunger"]))
            new_happy  = min(100, int(pet["happiness"]) + int(food["happiness"]))
            trust_gain = int(food["trust_gain"])
            xp_gain    = int(food["cost"]) // 75

            if not getattr(ctx, "_daily_attachment_cooldown_claimed", False):
                cooldown_key = f"cd:{ctx.author.id}:pets feed"
                cooldown_claimed = await self.bot.redis.execute_command(
                    "SET",
                    cooldown_key,
                    "pets feed",
                    "EX",
                    3600,
                    "NX",
                )
                if not cooldown_claimed:
                    ttl = await self.bot.redis.execute_command("TTL", cooldown_key)
                    if ttl == -1:
                        ttl = 3600
                        await self.bot.redis.execute_command("EXPIRE", cooldown_key, ttl)
                    elif ttl == -2:
                        ttl = 3600
                    hours, remainder = divmod(max(int(ttl), 0), 3600)
                    minutes, seconds = divmod(remainder, 60)
                    await ctx.send(
                        f"⏳ You must wait **{hours:02}:{minutes:02}:{seconds:02}** before feeding your pet again."
                    )
                    try:
                        ctx.command.reset_cooldown(ctx)
                    except Exception:
                        pass
                    return

            await conn.execute(
                """UPDATE monster_pets
                   SET hunger = $1, happiness = $2, last_update = $3
                   WHERE id = $4""",
                new_hunger, new_happy, dt.datetime.utcnow(), pet_id
            )

            lvl_result = await self.gain_experience(pet_id, xp_gain, trust_gain, conn=conn)

            await conn.execute(
                'UPDATE profile SET money = money - $1 WHERE "user" = $2',
                int(food["cost"]), ctx.author.id
            )

        trust_info = self.get_trust_level_info(int(pet["trust_level"]) + trust_gain)
        embed = discord.Embed(
            title=f"🍖 Fed {pet['name']} with {food_key.replace('_', ' ').title()}",
            color=discord.Color.green()
        )
        embed.add_field(
            name="📊 Stats Updated",
            value=f"**Hunger:** {pet['hunger']}% → {new_hunger}%\n"
                  f"**Happiness:** {pet['happiness']}% → {new_happy}%",
            inline=True
        )
        xp_mult_text = (
            f"\n**XP Multiplier:** x{lvl_result.get('original_xp', xp_gain)} → x{lvl_result.get('adjusted_xp', xp_gain)}"
            if lvl_result and lvl_result.get("xp_multiplier_applied") else ""
        )
        embed.add_field(
            name="🌟 Experience & Trust",
            value=f"**XP Gained:** +{xp_gain}\n"
                  f"**Trust Gained:** +{trust_gain}\n"
                  f"**Trust Level:** {trust_info['emoji']} {trust_info['name']}"
                  f"{xp_mult_text}",
            inline=True
        )
        if lvl_result and lvl_result["leveled_up"]:
            embed.add_field(
                name="🎉 Level Up!",
                value=f"**{pet['name']}** reached level {lvl_result['new_level']}!\n"
                      f"**Skill Points:** +{lvl_result['skill_points_gained']}",
                inline=False
            )
            embed.color = discord.Color.gold()

        embed.set_footer(text=f"Cost: ${food['cost']} | Use $pets status {pet_id} to track progress")
        await ctx.send(embed=embed)

    async def pet(self, ctx: commands.Context, pet_id: Optional[int] = None):
        """$pets pet [pet_id] — Pet your companion to increase happiness and trust."""
        async with self.bot.pool.acquire() as conn:
            resolved_pet_id, pet = await self._resolve_target_pet(conn, ctx, pet_id)
            if not pet:
                try:
                    ctx.command.reset_cooldown(ctx)
                except Exception:
                    pass
                return
            pet_id = resolved_pet_id

            happiness_boost = 10 if int(pet["happiness"]) > 50 else 5
            new_happy = min(100, int(pet["happiness"]) + happiness_boost)
            trust_gain = urandom.randint(0, 1)

            await conn.execute(
                "UPDATE monster_pets SET happiness = $1, last_update = $2 WHERE id = $3",
                new_happy, dt.datetime.utcnow(), pet_id
            )
            lvl_result = await self.gain_experience(pet_id, 5, trust_gain)

        responses = [
            f"🐾 {pet['name']} wags its tail happily as you pet it!",
            f"😊 {pet['name']} purrs contentedly under your gentle touch.",
            f"💕 {pet['name']} leans into your hand, clearly enjoying the attention!",
            f"🌟 {pet['name']} looks up at you with pure adoration in its eyes!",
            f"🎉 {pet['name']} jumps excitedly, overjoyed by your affection!",
        ]
        trust_info = self.get_trust_level_info(int(pet["trust_level"]) + trust_gain)

        embed = discord.Embed(title="🐾 Pet Interaction", description=urandom.choice(responses), color=discord.Color.blue())
        embed.add_field(
            name="📈 Effects",
            value=f"**Happiness:** +{happiness_boost}%\n**Trust:** +{trust_gain}\n**XP:** +5",
            inline=True
        )
        embed.add_field(
            name="🌟 Trust Level",
            value=f"{trust_info['emoji']} {trust_info['name']}",
            inline=True
        )
        if lvl_result and lvl_result["leveled_up"]:
            embed.add_field(
                name="🎉 Level Up!",
                value=f"**{pet['name']}** reached level {lvl_result['new_level']}!\n"
                      f"**Skill Points:** +{lvl_result['skill_points_gained']}",
                inline=False
            )
            embed.color = discord.Color.gold()
        await ctx.send(embed=embed)

    async def play(self, ctx: commands.Context, pet_id: Optional[int] = None):
        """$pets play [pet_id] — Play with your pet for happiness, trust, and XP."""
        async with self.bot.pool.acquire() as conn:
            resolved_pet_id, pet = await self._resolve_target_pet(conn, ctx, pet_id)
            if not pet:
                try:
                    ctx.command.reset_cooldown(ctx)
                except Exception:
                    pass
                return
            pet_id = resolved_pet_id

            happiness_boost = 25
            new_happy = min(100, int(pet["happiness"]) + happiness_boost)
            trust_gain = 1
            xp_gain = 10

            await conn.execute(
                "UPDATE monster_pets SET happiness = $1, last_update = $2 WHERE id = $3",
                new_happy, dt.datetime.utcnow(), pet_id
            )
            lvl_result = await self.gain_experience(pet_id, xp_gain, trust_gain)

        responses = [
            f"🎾 You play fetch with {pet['name']} — it's having the time of its life!",
            f"🏃 You chase {pet['name']} around in a fun game of tag!",
            f"🎪 {pet['name']} shows off some amazing tricks during playtime!",
            f"🌳 You explore the outdoors together — {pet['name']} is thrilled!",
            f"🎯 You play a challenging game with {pet['name']} — it's learning and growing!",
        ]
        trust_info = self.get_trust_level_info(int(pet["trust_level"]) + trust_gain)

        xp_mult_text = (
            f"\n**XP Multiplier:** x{lvl_result.get('original_xp', xp_gain)} → x{lvl_result.get('adjusted_xp', xp_gain)}"
            if lvl_result and lvl_result.get("xp_multiplier_applied") else ""
        )
        embed = discord.Embed(title="🎮 Play Session", description=urandom.choice(responses), color=discord.Color.purple())
        embed.add_field(
            name="Effects",
            value=f"**Happiness:** +{happiness_boost}%\n**Trust:** +{trust_gain}\n**XP:** +{xp_gain}{xp_mult_text}",
            inline=True
        )
        embed.add_field(name="🌟 Trust Level", value=f"{trust_info['emoji']} {trust_info['name']}", inline=True)
        if lvl_result and lvl_result["leveled_up"]:
            embed.add_field(
                name="🎉 Level Up!",
                value=f"**{pet['name']}** reached level {lvl_result['new_level']}!\n"
                      f"**Skill Points:** +{lvl_result['skill_points_gained']}",
                inline=False
            )
            embed.color = discord.Color.gold()
        await ctx.send(embed=embed)

    async def treat(self, ctx: commands.Context, pet_id: Optional[int] = None):
        """$pets treat [pet_id] — Give your pet a special treat for big boosts."""
        async with self.bot.pool.acquire() as conn:
            resolved_pet_id, pet = await self._resolve_target_pet(conn, ctx, pet_id)
            if not pet:
                try:
                    ctx.command.reset_cooldown(ctx)
                except Exception:
                    pass
                return
            pet_id = resolved_pet_id

            happiness_boost = 50
            new_happy = min(100, int(pet["happiness"]) + happiness_boost)
            trust_gain = 5
            xp_gain = 25

            await conn.execute(
                "UPDATE monster_pets SET happiness = $1, last_update = $2 WHERE id = $3",
                new_happy, dt.datetime.utcnow(), pet_id
            )
            lvl_result = await self.gain_experience(pet_id, xp_gain, trust_gain)

        responses = [
            f"🍖 {pet['name']} devours the special treat with pure joy!",
            f"🎁 {pet['name']} looks absolutely delighted with the surprise treat!",
            f"💝 {pet['name']} shows its gratitude with the most adorable expression!",
            f"🌟 {pet['name']} seems to glow with happiness after the treat!",
            f"🎉 {pet['name']} does a happy dance after receiving the special treat!",
        ]
        trust_info = self.get_trust_level_info(int(pet["trust_level"]) + trust_gain)

        xp_mult_text = (
            f"\n**XP Multiplier:** x{lvl_result.get('original_xp', xp_gain)} → x{lvl_result.get('adjusted_xp', xp_gain)}"
            if lvl_result and lvl_result.get("xp_multiplier_applied") else ""
        )
        embed = discord.Embed(title="🍖 Special Treat", description=urandom.choice(responses), color=discord.Color.orange())
        embed.add_field(
            name="📈 Effects",
            value=f"**Happiness:** +{happiness_boost}%\n**Trust:** +{trust_gain}\n**XP:** +{xp_gain}{xp_mult_text}",
            inline=True
        )
        embed.add_field(name="🌟 Trust Level", value=f"{trust_info['emoji']} {trust_info['name']}", inline=True)
        if lvl_result and lvl_result["leveled_up"]:
            embed.add_field(
                name="🎉 Level Up!",
                value=f"**{pet['name']}** reached level {lvl_result['new_level']}!\n"
                      f"**Skill Points:** +{lvl_result['skill_points_gained']}",
                inline=False
            )
        await ctx.send(embed=embed)

    async def status(self, ctx: commands.Context, pet_id: Optional[int] = None):
        """$pets status [pet_id] — View your pet's detailed care & progression status."""
        async with self.bot.pool.acquire() as conn:
            resolved_pet_id, pet = await self._resolve_target_pet(conn, ctx, pet_id)
            if not pet:
                return
            pet_id = resolved_pet_id

        trust_info = self.get_trust_level_info(int(pet["trust_level"]))
        next_level_xp = self.calculate_level_requirements(int(pet["level"]) + 1)
        progress_to_next = (int(pet["experience"]) / next_level_xp * 100) if next_level_xp > 0 else 0.0

        thresholds = sorted(self.TRUST_LEVELS.keys())
        next_thresh = next((t for t in thresholds if t > int(pet["trust_level"])), None)
        if next_thresh:
            cur_thresh = max(t for t in thresholds if t <= int(pet["trust_level"]))
            trust_progress = ((int(pet["trust_level"]) - cur_thresh) / (next_thresh - cur_thresh) * 100)
        else:
            trust_progress = 100.0

        try:
            xpm = float(pet["xp_multiplier"] or 1.0)
        except (KeyError, TypeError):
            xpm = 1.0
        xpm_text = f"**XP Multiplier:** x{xpm:g}" if xpm > 1.0 else ""

        embed = discord.Embed(title=f"📊 {pet['name']}'s Status", color=discord.Color.blue())
        embed.add_field(
            name="🌟 Basic Info",
            value=f"**Element:** {pet['element']}\n"
                  f"**Growth Stage:** {str(pet['growth_stage']).capitalize()}\n"
                  f"**Equipped:** {'✅' if pet['equipped'] else '❌'}",
            inline=True
        )
        embed.add_field(
            name="⚔️ Battle Stats",
            value=f"**HP:** {pet['hp']}\n**Attack:** {pet['attack']}\n**Defense:** {pet['defense']}\n**IV:** {pet['IV']}%",
            inline=True
        )
        embed.add_field(
            name="💚 Care Status",
            value=f"**Hunger:** {pet['hunger']}%\n**Happiness:** {pet['happiness']}%\n**Trust:** {pet['trust_level']}/100",
            inline=True
        )
        embed.add_field(
            name="📈 Progression",
            value=f"**Level:** {pet['level']}/50\n"
                  f"**Experience:** {pet['experience']}/{next_level_xp} ({progress_to_next:.1f}%)\n"
                  f"**Skill Points:** {pet['skill_points']}\n"
                  f"{xpm_text}",
            inline=True
        )
        embed.add_field(
            name="🎯 Trust Level",
            value=f"{trust_info['emoji']} **{trust_info['name']}**\n"
                  f"**Battle Bonus:** {trust_info['bonus']:+d}%\n"
                  f"**Progress:** {trust_progress:.1f}%",
            inline=True
        )

        # SAFE access for JSONB learned_skills (asyncpg.Record doesn't have .get)
        learned_raw = pet["learned_skills"] if "learned_skills" in pet else []
        learned: List[str]
        if isinstance(learned_raw, list):
            learned = learned_raw
        else:
            try:
                import json
                learned = json.loads(learned_raw) if learned_raw else []
            except Exception:
                learned = []

        if learned:
            top = "\n".join(f"✅ {s}" for s in learned[:5])
            if len(learned) > 5:
                top += f"\n... and {len(learned) - 5} more"
            embed.add_field(name="🎓 Learned Skills", value=top, inline=False)

        embed.set_footer(text=f"Use $pets feed {pet_id} <food_type> • $pets play/treat to bond faster")
        await ctx.send(embed=embed)

    async def feedhelp(self, ctx: commands.Context):
        """$pets feedhelp — Detailed guide on pet feeding mechanics and strategy."""
        embed = discord.Embed(
            title="🍖 Pet Feeding System Guide",
            description="Master the art of feeding your pets for optimal growth and bonding!",
            color=discord.Color.gold()
        )
        embed.add_field(
            name="🥘 Food Types & Effects",
            value=(
                "**Basic Food** ($10,000)\n"
                "• +50 hunger, +25 happiness, +1 trust • ~133 XP\n\n"
                "**Premium Food** ($25,000)\n"
                "• +100 hunger, +50 happiness, +2 trust • ~333 XP\n\n"
                "**Deluxe Food** ($50,000)\n"
                "• +100 hunger, +100 happiness, +3 trust • ~666 XP\n\n"
                "**Elemental Food** ($75,000) — Warrior tier+\n"
                "• +75 hunger, +75 happiness, +4 trust • ~1000 XP\n\n"
                "**Treats** ($5,000)\n"
                "• +10 hunger, +50 happiness, +2 trust • ~66 XP"
            ),
            inline=False
        )
        embed.add_field(
            name="💖 Trust System & Battle Bonuses",
            value=(
                "**Distrustful** (0-20): **-20%** 😠\n"
                "**Cautious** (21-40): **0%** 😐\n"
                "**Trusting** (41-60): **+10%** 😊\n"
                "**Loyal** (61-80): **+20%** 😍\n"
                "**Devoted** (81-100): **+30%** 🥰"
            ),
            inline=False
        )
        embed.add_field(
            name="⚠️ Important",
            value=(
                "• 1-hour cooldown between feedings\n"
                "• Warrior tier+ needed for elemental food\n"
                "• Skill points every 5 levels (5, 10, 15...)\n"
                "• Hunger decays by growth stage; adults are self-sufficient\n"
                "• Trust heavily affects all battle stats"
            ),
            inline=False
        )
        embed.set_footer(text="Use $pets feed [pet_id] <food_type> • $pets status [pet_id] to track progress")
        await ctx.send(embed=embed)


# ========= extension setup =========

async def setup(bot: commands.Bot):
    cog = PetsCare(bot)
    await bot.add_cog(cog)

    parent = bot.get_cog("Pets")
    if not parent or not hasattr(parent, "pets"):
        bot.logger.warning("[PetsCare] Parent 'Pets' cog/group missing. Load 'cogs.pets' first.")
        return

    group: commands.Group = parent.pets  # type: ignore

    # Remove any existing subcommands to avoid duplicates on reload
    for name in ("feed", "pet", "play", "treat", "status", "feedhelp"):
        old = group.get_command(name)
        if old:
            group.remove_command(old.name)

    # Map class method name -> (public subcommand name, help text)
    to_add = {
        "feed":     ("feed",     "Feed your pet with specific food types."),
        "pet":      ("pet",      "Pet your companion to increase happiness and trust."),
        "play":     ("play",     "Play with your pet for happiness, trust, and XP."),
        "treat":    ("treat",    "Give your pet a special treat for big boosts."),
        "status":   ("status",   "View your pet's detailed care & progression status."),
        "feedhelp": ("feedhelp", "Detailed guide on pet feeding mechanics and strategy."),
    }

    # Use the unbound function from the class so discord.py injects 'self'
    for attr, (public_name, help_text) in to_add.items():
        unbound = getattr(PetsCare, attr, None)
        if unbound is None:
            bot.logger.warning(f"[PetsCare] Missing method '{attr}', skipping.")
            continue

        cmd = commands.Command(unbound, name=public_name, help=help_text)
        cmd.cog = cog  # bind to the cog instance so 'self' is injected

        # ------- WORKING COOLDOWNS -------
        # Attach cooldown buckets so ctx.command.reset_cooldown(ctx) works properly.
        if public_name == "feed":
            # 1 use per 3600s (1 hour) per user
            cmd._buckets = commands.CooldownMapping.from_cooldown(1, 3600, commands.BucketType.user)
        elif public_name == "treat":
            # 1 use per 1800s (30 minutes) per user
            cmd._buckets = commands.CooldownMapping.from_cooldown(1, 1800, commands.BucketType.user)
        elif public_name == "play":
            # 1 use per 300s (5 minutes) per user
            cmd._buckets = commands.CooldownMapping.from_cooldown(1, 300, commands.BucketType.user)
        elif public_name == "pet":
            # 1 use per 60s per user
            cmd._buckets = commands.CooldownMapping.from_cooldown(1, 60, commands.BucketType.user)
        # status/feedhelp: no cooldown
        # --------------------------------

        group.add_command(cmd)
