"""
The IdleRPG Discord Bot
Copyright (C) 2018-2021 Diniboy and Gelbpunkt
Copyright (C) 2023-2024 Lunar (PrototypeX37)
Copyright (C) 2025 Danaelis

License: GNU AGPL v3 or later
"""
from __future__ import annotations

import asyncio
import json
from collections import Counter, deque
from datetime import datetime, timezone, timedelta
from typing import Optional

import asyncpg
import discord
from discord.ext import commands, tasks

from utils import misc as rpgtools
from utils.i18n import _, locale_doc
from utils.checks import has_char, is_gm
from utils.divine_familiars import (
    DIVINE_FAMILIARS,
    DIVINE_SHARDS_PER_EGG,
    craft_divine_egg,
    ensure_divine_familiar_tables,
    get_familiar_display_name,
    format_familiar_display_name,
    get_divine_shard_counts,
    resolve_familiar_key,
)
from cogs.shard_communication import user_on_cooldown as user_cooldown

# =====================================================================================
# Time helpers — code uses aware UTC; DB "timestamp without time zone" expects naive UTC
# =====================================================================================

def now_utc() -> datetime:
    """Return an aware UTC datetime."""
    return datetime.now(timezone.utc)

def to_db_naive_utc(dt_aware: datetime) -> datetime:
    """
    Convert an aware UTC datetime to naive UTC for TIMESTAMP WITHOUT TIME ZONE columns.
    If already naive, return as-is (assumed UTC).
    """
    if dt_aware.tzinfo is None:
        return dt_aware
    return dt_aware.astimezone(timezone.utc).replace(tzinfo=None)

def from_db_naive_utc(dt_naive: Optional[datetime]) -> Optional[datetime]:
    """Convert a naive UTC datetime from DB to aware UTC for code math."""
    if dt_naive is None:
        return None
    if dt_naive.tzinfo is not None:
        return dt_naive.astimezone(timezone.utc)
    return dt_naive.replace(tzinfo=timezone.utc)

# =====================================================================================
# Static data
# =====================================================================================

STAGE_ICON = {"baby": "🍼", "juvenile": "🌱", "young": "🐕", "adult": "🦁"}

GROWTH_STAGES = {
    1: {"stage": "baby",     "growth_time": 2, "stat_multiplier": 0.25, "hunger_modifier": 1.0},
    2: {"stage": "juvenile", "growth_time": 2, "stat_multiplier": 0.50, "hunger_modifier": 0.8},
    3: {"stage": "young",    "growth_time": 1, "stat_multiplier": 0.75, "hunger_modifier": 0.6},
    4: {"stage": "adult",    "growth_time": None, "stat_multiplier": 1.0, "hunger_modifier": 0.0},
}

HOUSE_THEMES = {
    "oikos": {"label": "Oikos", "emoji": "🏛️", "color": 0xD4AF37},
    "olympus": {"label": "Olympus", "emoji": "⚡", "color": 0xF6D64A},
    "underworld": {"label": "Underworld", "emoji": "🔥", "color": 0x8B1E1E},
    "aegean": {"label": "Aegean", "emoji": "🌊", "color": 0x2E86DE},
    "grove": {"label": "Sacred Grove", "emoji": "🌿", "color": 0x2E8B57},
}

def stage_icon(stage: str) -> str:
    return STAGE_ICON.get((stage or "").lower(), "🐾")

def safe_get(rec, key, default=0):
    try:
        return rec.get(key, default) if hasattr(rec, "get") else rec[key]
    except Exception:
        return default

def fmt_time_left_generic(when: Optional[datetime]) -> Optional[str]:
    """Accepts aware or naive UTC, returns HH:MM:SS string until 'when' from now_utc()."""
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    delta = when - now_utc()
    if delta.total_seconds() <= 0:
        return "Ready!"
    return str(delta).split(".")[0]

# =====================================================================================
# Generic confirm views
# =====================================================================================

class ConfirmView(discord.ui.View):
    """Generic 2-button confirm/decline with user authorization."""
    def __init__(
        self,
        allowed_user: discord.abc.User,
        *,
        timeout: int = 120,
        accept_label="Accept",
        decline_label="Decline",
        style_accept=discord.ButtonStyle.success,
        style_decline=discord.ButtonStyle.danger,
        accept_emoji="✅",
        decline_emoji="❌",
    ):
        super().__init__(timeout=timeout)
        self.allowed_user = allowed_user
        self.value: bool | None = None

        self._btn_accept = discord.ui.Button(label=accept_label, style=style_accept, emoji=accept_emoji)
        self._btn_decline = discord.ui.Button(label=decline_label, style=style_decline, emoji=decline_emoji)
        self._btn_accept.callback = self._on_accept
        self._btn_decline.callback = self._on_decline
        self.add_item(self._btn_accept)
        self.add_item(self._btn_decline)

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.allowed_user.id:
            await interaction.response.send_message("❌ You are not authorized to respond.", ephemeral=True)
            return False
        return True

    async def _on_accept(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        self.value = True
        await interaction.response.send_message("✅ Accepted.", ephemeral=True)
        self.stop()

    async def _on_decline(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        self.value = False
        await interaction.response.send_message("❌ Declined.", ephemeral=True)
        self.stop()

class SellConfirmationView(ConfirmView):
    def __init__(self, initiator: discord.Member, receiver: discord.Member, price: int, timeout=120):
        super().__init__(allowed_user=receiver, timeout=timeout,
                         accept_label="Accept Sale", decline_label="Decline Sale")

class TradeConfirmationView(ConfirmView):
    def __init__(self, initiator: discord.User, receiver: discord.User, timeout=120):
        super().__init__(allowed_user=receiver, timeout=timeout,
                         accept_label="Accept Trade", decline_label="Decline Trade")

# =====================================================================================
# Selects / Paginators
# =====================================================================================

class PetSelect(discord.ui.Select):
    def __init__(self, pets):
        options = []
        for i, pet in enumerate(pets):
            gs = (pet.get("growth_stage") or "baby").lower()
            options.append(
                discord.SelectOption(
                    label=f"{pet.get('name','?')} (ID: {pet.get('id','?')})",
                    description=f"{pet.get('element','?')} | IV: {safe_get(pet, 'IV', 0)}% | {gs.capitalize()}",
                    value=str(i),
                    emoji=stage_icon(gs),
                )
            )
        super().__init__(placeholder="Select a pet to view...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        view: "PetPaginator" = self.view  # type: ignore
        if interaction.user.id != view.author.id:
            return await interaction.response.send_message("This is not your pet list.", ephemeral=True)
        view.index = int(self.values[0])
        await view.send_page(interaction)

class PetFoodSelect(discord.ui.Select):
    def __init__(self, parent_view: "PetPaginator", pet_id: int):
        self.parent_view = parent_view
        self.pet_id = pet_id
        care_cog = parent_view.cog.bot.get_cog("PetsCare")
        food_types = getattr(care_cog, "FOOD_TYPES", {})

        options = []
        for key, food in food_types.items():
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
        food_key = self.values[0]
        await self.parent_view.run_pet_command(
            interaction,
            "feed",
            self.pet_id,
            food_type=food_key.replace("_", " "),
            done_message=f"Food selected: **{food_key.replace('_', ' ').title()}**.",
        )
        view = self.view
        if view:
            for child in view.children:
                child.disabled = True
            try:
                await interaction.message.edit(view=view)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

class PetFoodView(discord.ui.View):
    def __init__(self, parent_view: "PetPaginator", pet_id: int):
        super().__init__(timeout=45)
        self.parent_view = parent_view
        self.pet_id = pet_id
        self.add_item(PetFoodSelect(parent_view, pet_id))

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.gray, emoji="❌", row=1)
    async def cancel_button(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if interaction.user.id != self.parent_view.author.id:
            return await interaction.response.send_message("This is not your pet menu.", ephemeral=True)
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="Feed cancelled.", view=self)

class PetPaginator(discord.ui.View):
    def __init__(
        self,
        pets,
        author: discord.abc.User,
        cog_instance: commands.Cog,
        ctx: commands.Context | None = None,
        *,
        actions_enabled: bool = True,
    ):
        super().__init__(timeout=60)
        self.pets = pets
        self.author = author
        self.cog = cog_instance
        self.ctx = ctx
        self.actions_enabled = actions_enabled and ctx is not None
        self.index = 0
        self.message: discord.Message | None = None
        if not self.actions_enabled:
            for child in list(self.children):
                if isinstance(child, discord.ui.Button) and child.label != "Close":
                    self.remove_item(child)
        if pets:
            self.add_item(PetSelect(pets))

    async def on_timeout(self):
        if self.message:
            try:
                await self.message.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

    def _embed_for(self, pet) -> discord.Embed:
        # Core fields
        gs_index = int(pet.get("growth_index", 1))
        gs_info = GROWTH_STAGES.get(gs_index, GROWTH_STAGES[1])
        gs_name = (pet.get("growth_stage") or gs_info["stage"]).lower()

        hp = round(safe_get(pet, "hp", 0))
        atk = round(safe_get(pet, "attack", 0))
        df = round(safe_get(pet, "defense", 0))
        iv = safe_get(pet, "IV", 0)
        petid = pet.get("id", "?")

        # Timers / status
        growth_left = None
        if gs_name != "adult":
            growth_left = fmt_time_left_generic(pet.get("growth_time"))

        # Enhanced stats
        lvl = safe_get(pet, "level", 1)
        exp = safe_get(pet, "experience", 0)
        sp = safe_get(pet, "skill_points", 0)
        trust = safe_get(pet, "trust_level", 0)
        xpm = float(safe_get(pet, "xp_multiplier", 1.0))
        trust_info = getattr(self.cog, "get_trust_level_info", lambda x: {"emoji": "🤝", "name": "Trust", "bonus": 0})(trust)
        xpm_text = f"\n**XP Multiplier:** x{xpm:g}" if xpm > 1.0 else ""

        # Embed
        desc = (
            f"**Stage:** {gs_name.capitalize()} {stage_icon(gs_name)}\n"
            f"**ID:** {petid}\n"
            f"**Equipped:** {bool(pet.get('equipped'))}"
        )
        embed = discord.Embed(
            title=f"🐾 Your Pet: {pet.get('name','?')}",
            description=desc,
            color=discord.Color.green(),
        )
        embed.add_field(
            name="✨ **Stats**",
            value=f"**IV** {iv}%\n**HP:** {hp}\n**Attack:** {atk}\n**Defense:** {df}",
            inline=False,
        )
        embed.add_field(
            name="🌟 **Enhanced Stats**",
            value=(
                f"**Level:** {lvl}/50\n"
                f"**Experience:** {exp}\n"
                f"**Skill Points:** {sp}\n"
                f"**Trust:** {trust_info.get('emoji','')} {trust_info.get('name','')} ({trust}/100)"
                f"{xpm_text}"
            ),
            inline=False,
        )
        embed.add_field(
            name="🌟 **Details**",
            value=(
                f"**Element:** {pet.get('element','?')}\n"
                f"**Happiness:** {safe_get(pet, 'happiness', 0)}%\n"
                f"**Hunger:** {safe_get(pet, 'hunger', 0)}%"
            ),
            inline=False,
        )
        if growth_left:
            embed.add_field(name="⏳ **Growth Time Left**", value=growth_left, inline=False)
        elif gs_name == "adult":
            embed.add_field(name="🎉 **Growth**", value="Your pet is fully grown!", inline=False)

        if url := pet.get("url"):
            embed.set_image(url=url)

        embed.set_footer(text=f"Viewing pet {self.index + 1} of {len(self.pets)} | Use the dropdown to navigate")
        return embed

    def get_embed(self) -> discord.Embed:
        return self._embed_for(self.pets[self.index])

    def selected_pet_id(self) -> int:
        return int(safe_get(self.pets[self.index], "id", 0))

    async def refresh_selected_pet(self) -> None:
        pet_id = self.selected_pet_id()
        async with self.cog.bot.pool.acquire() as conn:
            pet = await conn.fetchrow(
                """
                SELECT *
                FROM monster_pets
                WHERE user_id = $1 AND id = $2
                """,
                self.author.id,
                pet_id,
            )
        if pet:
            self.pets[self.index] = pet

    async def refresh_message(self) -> None:
        if not self.message:
            return
        try:
            await self.message.edit(embed=self.get_embed(), view=self)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    def get_group_command(self, name: str) -> commands.Command | None:
        group = getattr(self.cog, "pets", None)
        if group is None or not hasattr(group, "get_command"):
            return None
        return group.get_command(name)

    async def run_pet_command(
        self,
        interaction: discord.Interaction,
        command_name: str,
        *args,
        done_message: str | None = None,
        refresh: bool = True,
        **kwargs,
    ) -> None:
        if interaction.user.id != self.author.id:
            return await interaction.response.send_message("This is not your pet menu.", ephemeral=True)
        if self.ctx is None:
            return await interaction.response.send_message("This pet menu cannot run actions.", ephemeral=True)

        command = self.get_group_command(command_name)
        if command is None:
            return await interaction.response.send_message(
                f"That pet action is not loaded yet. Try reloading the pet cogs.",
                ephemeral=True,
            )

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        original_command = self.ctx.command
        self.ctx.command = command
        try:
            if not command.enabled:
                raise commands.DisabledCommand(f"{command.qualified_name} command is disabled")
            for predicate in command.checks:
                allowed = await discord.utils.maybe_coroutine(predicate, self.ctx)
                if not allowed:
                    raise commands.CheckFailure(f"The checks for {command.qualified_name} failed.")
            if command._buckets.valid:
                current = datetime.now(timezone.utc).timestamp()
                bucket = command._buckets.get_bucket(self.ctx, current)
                if bucket is not None:
                    retry_after = bucket.update_rate_limit(current)
                    if retry_after:
                        raise commands.CommandOnCooldown(bucket, retry_after, command._buckets.type)

            callback = command.callback
            if command.cog is not None:
                await callback(command.cog, self.ctx, *args, **kwargs)
            else:
                await callback(self.ctx, *args, **kwargs)
        except commands.CommandOnCooldown as exc:
            await interaction.followup.send(
                f"⏳ `{command.qualified_name}` is on cooldown. Try again in **{timedelta(seconds=int(exc.retry_after))}**.",
                ephemeral=True,
            )
            return
        except commands.CommandError as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
            return
        except Exception as exc:
            logger = getattr(self.cog.bot, "logger", None)
            if logger:
                logger.exception("Pet action button failed")
            await interaction.followup.send(f"❌ Pet action failed: {exc}", ephemeral=True)
            return
        finally:
            self.ctx.command = original_command

        if refresh and command_name != "release":
            await self.refresh_selected_pet()
            await self.refresh_message()
        if done_message:
            await interaction.followup.send(done_message, ephemeral=True)

    async def send_page(self, interaction: discord.Interaction):
        embed = self.get_embed()
        if self.message is None:
            self.message = interaction.message
        if interaction.response.is_done():
            await self.message.edit(embed=embed, view=self)
        else:
            await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Feed", style=discord.ButtonStyle.green, emoji="🍖", row=1)
    async def feed_button(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if interaction.user.id != self.author.id:
            return await interaction.response.send_message("This is not your pet menu.", ephemeral=True)
        care_cog = self.cog.bot.get_cog("PetsCare")
        if not care_cog or not getattr(care_cog, "FOOD_TYPES", None):
            return await interaction.response.send_message("Pet feeding is not loaded yet.", ephemeral=True)
        pet = self.pets[self.index]
        pet_id = self.selected_pet_id()
        await interaction.response.send_message(
            f"Choose food for **{safe_get(pet, 'name', 'this pet')}**.",
            view=PetFoodView(self, pet_id),
            ephemeral=True,
        )

    @discord.ui.button(label="Pet", style=discord.ButtonStyle.primary, emoji="🐾", row=1)
    async def pet_button(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self.run_pet_command(interaction, "pet", self.selected_pet_id(), done_message="Pet interaction sent.")

    @discord.ui.button(label="Play", style=discord.ButtonStyle.primary, emoji="🎾", row=1)
    async def play_button(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self.run_pet_command(interaction, "play", self.selected_pet_id(), done_message="Play session sent.")

    @discord.ui.button(label="Train", style=discord.ButtonStyle.secondary, emoji="🏋️", row=1)
    async def train_button(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self.run_pet_command(interaction, "train", self.selected_pet_id(), done_message="Training session sent.")

    @discord.ui.button(label="Status", style=discord.ButtonStyle.secondary, emoji="📊", row=1)
    async def status_button(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self.run_pet_command(
            interaction,
            "status",
            self.selected_pet_id(),
            done_message="Status sent.",
            refresh=False,
        )

    @discord.ui.button(label="Treat", style=discord.ButtonStyle.green, emoji="🍬", row=2)
    async def treat_button(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self.run_pet_command(interaction, "treat", self.selected_pet_id(), done_message="Treat sent.")

    @discord.ui.button(label="Release", style=discord.ButtonStyle.red, emoji="💔", row=2)
    async def release_button(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self.run_pet_command(
            interaction,
            "release",
            self.selected_pet_id(),
            done_message="Release confirmation opened.",
            refresh=False,
        )

    @discord.ui.button(label="Close", style=discord.ButtonStyle.red, row=3)
    async def close_button(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if interaction.user.id != self.author.id:
            return await interaction.response.send_message("This is not your pet list.", ephemeral=True)
        await interaction.message.delete()
        self.stop()

# =====================================================================================
# Main Cog
# =====================================================================================

class Pets(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        if not self.check_egg_hatches.is_running():
            self.check_egg_hatches.start()
        if not self.check_pet_growth.is_running():
            self.check_pet_growth.start()

        self.emoji_to_element = {
            "<:f_corruption:1170192253256466492>": "Corrupted",
            "<:f_water:1170191321571545150>": "Water",
            "<:f_electric:1170191219926777936>": "Electric",
            "<:f_light:1170191258795376771>": "Light",
            "<:f_dark:1170191180164771920>": "Dark",
            "<:f_nature:1170191149802213526>": "Wind",
            "<:f_earth:1170191288361033806>": "Nature",
            "<:f_fire:1170192046632468564>": "Fire",
        }
        self.bot.loop.create_task(self.initialize_pet_house_tables())

    async def _ensure_pet_house_columns(self, conn):
        await conn.execute(
            """
            ALTER TABLE profile
            ADD COLUMN IF NOT EXISTS pet_house_slots INTEGER NOT NULL DEFAULT 0
            """
        )
        await conn.execute(
            """
            ALTER TABLE profile
            ADD COLUMN IF NOT EXISTS pet_house_name VARCHAR(60)
            """
        )
        await conn.execute(
            """
            ALTER TABLE profile
            ADD COLUMN IF NOT EXISTS pet_house_image TEXT
            """
        )
        await conn.execute(
            """
            ALTER TABLE profile
            ADD COLUMN IF NOT EXISTS pet_house_motto VARCHAR(180)
            """
        )
        await conn.execute(
            """
            ALTER TABLE profile
            ADD COLUMN IF NOT EXISTS pet_house_theme VARCHAR(20)
            """
        )
        await conn.execute(
            """
            ALTER TABLE monster_pets
            ADD COLUMN IF NOT EXISTS in_house BOOLEAN NOT NULL DEFAULT FALSE
            """
        )

    async def initialize_pet_house_tables(self):
        try:
            async with self.bot.pool.acquire() as conn:
                await self._ensure_pet_house_columns(conn)
        except Exception as e:
            print(f"[Pets] Error initializing pet house columns: {e}")

    def _calc_inventory_limit(
        self,
        tier: int | None,
        member: discord.Member | None,
        guild: discord.Guild | None,
    ) -> int:
        try:
            tier_value = int(tier) if tier is not None else 0
        except (TypeError, ValueError):
            try:
                # Some rows may hold values like "4.0"; normalize them to integer tiers.
                tier_value = int(float(tier))
            except (TypeError, ValueError):
                tier_value = 0

        maxslot = 20
        if guild and guild.id == 1199287508794626078 and getattr(member, "premium_since", None):
            maxslot = max(maxslot, 22)
        if tier_value == 1:
            maxslot = max(maxslot, 22)
        elif tier_value == 2:
            maxslot = 24
        elif tier_value == 3:
            maxslot = 27
        elif tier_value == 4:
            maxslot = 25
        return maxslot

    async def _active_pet_item_count(
        self,
        conn,
        user_id: int,
        skip_pet_id: int = -1,
        skip_egg_id: int = -1,
    ) -> int:
        return await conn.fetchval(
            """
            SELECT
                (SELECT COUNT(*)
                 FROM monster_pets
                 WHERE user_id = $1
                   AND COALESCE(in_house, FALSE) = FALSE
                   AND ($2 = -1 OR id != $2)) +
                (SELECT COUNT(*)
                 FROM monster_eggs
                 WHERE user_id = $1
                   AND hatched = FALSE
                   AND ($3 = -1 OR id != $3)) +
                (SELECT COUNT(*)
                 FROM splice_requests
                 WHERE user_id = $1
                   AND status = 'pending')
            """,
            user_id,
            skip_pet_id,
            skip_egg_id,
        ) or 0

    async def _house_slots(self, conn, user_id: int) -> int:
        return await conn.fetchval(
            'SELECT COALESCE(pet_house_slots, 0) FROM profile WHERE "user" = $1',
            user_id,
        ) or 0

    async def _housed_pet_count(self, conn, user_id: int) -> int:
        return await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM monster_pets
            WHERE user_id = $1 AND COALESCE(in_house, FALSE) = TRUE
            """,
            user_id,
        ) or 0

    @staticmethod
    def _default_house_name(user: discord.abc.User) -> str:
        display = getattr(user, "display_name", None) or user.name
        return f"{display}'s Oikos"

    @staticmethod
    def _is_image_url(url: str) -> bool:
        normalized = url.strip().lower().split("?", 1)[0]
        return normalized.startswith(("http://", "https://")) and normalized.endswith(
            (".png", ".jpg", ".jpeg", ".webp", ".gif")
        )

    @staticmethod
    def _theme_key(theme: Optional[str]) -> str:
        key = (theme or "oikos").strip().lower()
        return key if key in HOUSE_THEMES else "oikos"

    # ---------- commands ----------

    @commands.group(invoke_without_command=True)
    async def pets(self, ctx):
        if hasattr(self, "check_pet"):
            await self.check_pet(ctx.author.id)

        try:
            async with self.bot.pool.acquire() as conn:
                await self._ensure_pet_house_columns(conn)
                pets = await conn.fetch(
                    """
                    SELECT *
                    FROM monster_pets
                    WHERE user_id = $1 AND COALESCE(in_house, FALSE) = FALSE
                    ORDER BY equipped DESC, id
                    """,
                    ctx.author.id,
                )
                if not pets:
                    housed_count = await self._housed_pet_count(conn, ctx.author.id)
                    if housed_count > 0:
                        await ctx.send(
                            _("You have no active pets right now. You currently have **{count}** pet(s) in your Oikos house.\n"
                              "Use `{prefix}pets house` to view them or `{prefix}pets unstore <id>` to retrieve one.").format(
                                count=housed_count, prefix=ctx.clean_prefix
                            )
                        )
                    else:
                        await ctx.send("You don't have any pets.")
                    return

            view = PetPaginator(pets, ctx.author, self, ctx)
            embed = view.get_embed()
            view.message = await ctx.send(embed=embed, view=view)
        except Exception as e:
            await ctx.send(str(e))

    @pets.command(brief=_("View pets stored in your Pet Oikos house"))
    async def house(self, ctx):
        try:
            async with self.bot.pool.acquire() as conn:
                await self._ensure_pet_house_columns(conn)
                profile_row = await conn.fetchrow(
                    """
                    SELECT
                        COALESCE(pet_house_slots, 0) AS pet_house_slots,
                        pet_house_name,
                        pet_house_image,
                        pet_house_motto,
                        pet_house_theme
                    FROM profile
                    WHERE "user" = $1
                    """,
                    ctx.author.id,
                )
                if not profile_row:
                    return await ctx.send(_("You don't have a character profile yet."))

                house_slots = profile_row["pet_house_slots"] or 0
                if house_slots < 15:
                    return await ctx.send(
                        _("You don't own a Pet Oikos yet. Buy one with `{prefix}dcbuy pethouse`.").format(
                            prefix=ctx.clean_prefix
                        )
                    )

                housed_pets = await conn.fetch(
                    """
                    SELECT *
                    FROM monster_pets
                    WHERE user_id = $1 AND COALESCE(in_house, FALSE) = TRUE
                    ORDER BY id
                    """,
                    ctx.author.id,
                )
                housed_count = len(housed_pets)

                theme_key = self._theme_key(profile_row["pet_house_theme"])
                theme_info = HOUSE_THEMES[theme_key]
                house_name = profile_row["pet_house_name"] or self._default_house_name(ctx.author)
                house_motto = profile_row["pet_house_motto"] or _("A safe sanctuary for your companions.")
                house_image = profile_row["pet_house_image"]

                overview = discord.Embed(
                    title=f"{theme_info['emoji']} {house_name}",
                    description=(
                        f"{house_motto}\n\n"
                        f"**Theme:** {theme_info['label']}\n"
                        f"**Storage:** {housed_count}/{house_slots}"
                    ),
                    color=theme_info["color"],
                )
                if house_image:
                    overview.set_image(url=house_image)
                overview.set_footer(
                    text=f"Customize: {ctx.clean_prefix}pets housename | houseimage | housemotto | housetheme"
                )
                await ctx.send(embed=overview)

                view = PetPaginator(housed_pets, ctx.author, self, ctx, actions_enabled=False)
            if not housed_pets:
                return await ctx.send(_("🏛️ Your Pet Oikos is currently empty."))

            view.message = await ctx.send(embed=view.get_embed(), view=view)
        except Exception as e:
            await ctx.send(str(e))

    @pets.command(brief=_("Set or reset your Pet Oikos house name"))
    async def housename(self, ctx, *, name: str):
        try:
            raw = name.strip()
            async with self.bot.pool.acquire() as conn:
                await self._ensure_pet_house_columns(conn)
                slots = await self._house_slots(conn, ctx.author.id)
                if slots < 15:
                    return await ctx.send(_("Buy a base Pet Oikos first with `{prefix}dcbuy pethouse`.").format(prefix=ctx.clean_prefix))

                if raw.lower() in {"reset", "default"}:
                    value = None
                    text = _("🏛️ House name reset to default.")
                else:
                    if len(raw) < 2 or len(raw) > 60:
                        return await ctx.send(_("House name must be between 2 and 60 characters."))
                    value = raw
                    text = _("🏛️ House name set to **{name}**.").format(name=raw)

                updated = await conn.fetchval(
                    'UPDATE profile SET pet_house_name = $1 WHERE "user" = $2 RETURNING "user";',
                    value,
                    ctx.author.id,
                )
                if not updated:
                    return await ctx.send(_("You don't have a character profile yet."))

            await ctx.send(text)
        except Exception as e:
            await ctx.send(str(e))

    @pets.command(brief=_("Set or reset your Pet Oikos house image"))
    async def houseimage(self, ctx, *, image_url: str):
        try:
            raw = image_url.strip()
            async with self.bot.pool.acquire() as conn:
                await self._ensure_pet_house_columns(conn)
                slots = await self._house_slots(conn, ctx.author.id)
                if slots < 15:
                    return await ctx.send(_("Buy a base Pet Oikos first with `{prefix}dcbuy pethouse`.").format(prefix=ctx.clean_prefix))

                if raw.lower() in {"reset", "default", "none"}:
                    value = None
                    text = _("🏛️ House image cleared.")
                else:
                    if not self._is_image_url(raw):
                        return await ctx.send(_("Please provide a valid direct image URL ending in `.png`, `.jpg`, `.jpeg`, `.webp`, or `.gif`."))
                    value = raw
                    text = _("🏛️ House image updated.")

                updated = await conn.fetchval(
                    'UPDATE profile SET pet_house_image = $1 WHERE "user" = $2 RETURNING "user";',
                    value,
                    ctx.author.id,
                )
                if not updated:
                    return await ctx.send(_("You don't have a character profile yet."))

            await ctx.send(text)
        except Exception as e:
            await ctx.send(str(e))

    @pets.command(brief=_("Set or reset your Pet Oikos motto"))
    async def housemotto(self, ctx, *, motto: str):
        try:
            raw = motto.strip()
            async with self.bot.pool.acquire() as conn:
                await self._ensure_pet_house_columns(conn)
                slots = await self._house_slots(conn, ctx.author.id)
                if slots < 15:
                    return await ctx.send(_("Buy a base Pet Oikos first with `{prefix}dcbuy pethouse`.").format(prefix=ctx.clean_prefix))

                if raw.lower() in {"reset", "default"}:
                    value = None
                    text = _("🏛️ House motto reset.")
                else:
                    if len(raw) < 3 or len(raw) > 180:
                        return await ctx.send(_("House motto must be between 3 and 180 characters."))
                    value = raw
                    text = _("🏛️ House motto updated.")

                updated = await conn.fetchval(
                    'UPDATE profile SET pet_house_motto = $1 WHERE "user" = $2 RETURNING "user";',
                    value,
                    ctx.author.id,
                )
                if not updated:
                    return await ctx.send(_("You don't have a character profile yet."))

            await ctx.send(text)
        except Exception as e:
            await ctx.send(str(e))

    @pets.command(brief=_("Set your Pet Oikos visual theme"))
    async def housetheme(self, ctx, theme: Optional[str] = None):
        try:
            async with self.bot.pool.acquire() as conn:
                await self._ensure_pet_house_columns(conn)
                slots = await self._house_slots(conn, ctx.author.id)
                if slots < 15:
                    return await ctx.send(_("Buy a base Pet Oikos first with `{prefix}dcbuy pethouse`.").format(prefix=ctx.clean_prefix))

                if not theme:
                    choices = ", ".join([f"`{k}`" for k in HOUSE_THEMES.keys()])
                    return await ctx.send(
                        _("Available house themes: {choices}\nUse `{prefix}pets housetheme <theme>`").format(
                            choices=choices, prefix=ctx.clean_prefix
                        )
                    )

                choice = theme.strip().lower()
                if choice in {"reset", "default"}:
                    value = None
                    text = _("🏛️ House theme reset to default (`oikos`).")
                elif choice in HOUSE_THEMES:
                    value = choice
                    text = _("🏛️ House theme set to **{theme}** {emoji}.").format(
                        theme=HOUSE_THEMES[choice]["label"],
                        emoji=HOUSE_THEMES[choice]["emoji"],
                    )
                else:
                    choices = ", ".join([f"`{k}`" for k in HOUSE_THEMES.keys()])
                    return await ctx.send(_("Invalid theme. Available: {choices}").format(choices=choices))

                updated = await conn.fetchval(
                    'UPDATE profile SET pet_house_theme = $1 WHERE "user" = $2 RETURNING "user";',
                    value,
                    ctx.author.id,
                )
                if not updated:
                    return await ctx.send(_("You don't have a character profile yet."))

            await ctx.send(text)
        except Exception as e:
            await ctx.send(str(e))

    @pets.command(brief=_("Store one of your pets in your Pet Oikos house"))
    async def store(self, ctx, pet_id: int):
        try:
            async with self.bot.pool.acquire() as conn:
                await self._ensure_pet_house_columns(conn)
                house_slots = await self._house_slots(conn, ctx.author.id)
                if house_slots < 15:
                    return await ctx.send(
                        _("You don't own a Pet Oikos yet. Buy one with `{prefix}dcbuy pethouse`.").format(
                            prefix=ctx.clean_prefix
                        )
                    )

                housed_count = await self._housed_pet_count(conn, ctx.author.id)
                if housed_count >= house_slots:
                    return await ctx.send(
                        _("Your Oikos is full (**{used}/{slots}**). Buy more slots with `{prefix}dcbuy petext`.").format(
                            used=housed_count,
                            slots=house_slots,
                            prefix=ctx.clean_prefix,
                        )
                    )

                pet = await conn.fetchrow(
                    "SELECT * FROM monster_pets WHERE user_id = $1 AND id = $2;",
                    ctx.author.id,
                    pet_id,
                )
                if not pet:
                    return await ctx.send(_("❌ No pet with ID `{id}` found in your collection.").format(id=pet_id))
                if bool(pet.get("in_house")):
                    return await ctx.send(_("That pet is already stored in your Oikos."))
                if bool(pet.get("equipped")):
                    return await ctx.send(_("Unequip this pet before storing it in your Oikos."))

                await conn.execute(
                    "UPDATE monster_pets SET in_house = TRUE, equipped = FALSE WHERE id = $1 AND user_id = $2;",
                    pet_id,
                    ctx.author.id,
                )

            await ctx.send(
                _("🏛️ **{name}** was moved into your Oikos storage (**{used}/{slots}**).").format(
                    name=pet.get("name", f"Pet #{pet_id}"),
                    used=housed_count + 1,
                    slots=house_slots,
                )
            )
        except Exception as e:
            await ctx.send(str(e))

    @pets.command(brief=_("Retrieve a stored pet from your Pet Oikos house"))
    async def unstore(self, ctx, pet_id: int):
        try:
            async with self.bot.pool.acquire() as conn:
                await self._ensure_pet_house_columns(conn)
                pet = await conn.fetchrow(
                    """
                    SELECT *
                    FROM monster_pets
                    WHERE user_id = $1 AND id = $2 AND COALESCE(in_house, FALSE) = TRUE
                    """,
                    ctx.author.id,
                    pet_id,
                )
                if not pet:
                    return await ctx.send(_("No stored pet with ID `{id}` was found in your Oikos.").format(id=pet_id))

                tier = await conn.fetchval("SELECT tier FROM profile WHERE profile.user = $1", ctx.author.id)
                member = ctx.guild.get_member(ctx.author.id) if getattr(ctx, "guild", None) else None
                maxslot = self._calc_inventory_limit(tier, member, getattr(ctx, "guild", None))
                active_count = await self._active_pet_item_count(conn, ctx.author.id)
                if active_count >= maxslot:
                    return await ctx.send(
                        _("Your active inventory is full (**{count}/{limit}**). Release/trade something first.").format(
                            count=active_count, limit=maxslot
                        )
                    )

                await conn.execute(
                    "UPDATE monster_pets SET in_house = FALSE WHERE id = $1 AND user_id = $2;",
                    pet_id,
                    ctx.author.id,
                )

            await ctx.send(_("🏛️ **{name}** has been retrieved from your Oikos.").format(name=pet.get("name", f"Pet #{pet_id}")))
        except Exception as e:
            await ctx.send(str(e))

    @user_cooldown(120)
    @pets.command(brief=_("Rename your pet or reset its name to the default"))
    async def rename(self, ctx, id: int, *, nickname: str | None = None):
        try:
            async with self.bot.pool.acquire() as conn:
                pet = await conn.fetchrow(
                    "SELECT * FROM monster_pets WHERE user_id = $1 AND id = $2;",
                    ctx.author.id,
                    id,
                )
                if not pet:
                    await ctx.send(_("❌ No pet with ID `{id}` found in your collection.").format(id=id))
                    return

                if nickname:
                    if len(nickname) > 50:
                        await ctx.send(_("❌ Nickname cannot exceed 50 characters."))
                        return
                    await conn.execute("UPDATE monster_pets SET name = $1 WHERE id = $2;", nickname, id)
                    await ctx.send(_("✅ Successfully renamed your pet to **{nickname}**!").format(nickname=nickname))
                else:
                    default_name = pet["default_name"]
                    await conn.execute("UPDATE monster_pets SET name = $1 WHERE id = $2;", default_name, id)
                    await ctx.send(
                        _("✅ Pet's name has been reset to its default: **{default_name}**.").format(
                            default_name=default_name
                        )
                    )
        except Exception as e:
            await ctx.send(str(e))

    @user_cooldown(600)
    @pets.command(brief="Trade your pet or egg with another user's pet or egg")
    @has_char()
    async def trade(self, ctx, your_type: str, your_item_id: int, their_type: str, their_item_id: int):
        your_type = your_type.lower()
        their_type = their_type.lower()
        valid_types = ["pet", "egg"]
        if your_type not in valid_types or their_type not in valid_types:
            await ctx.send("❌ Invalid type specified. Use `pet` or `egg`.")
            await self.bot.reset_cooldown(ctx)
            return

        async with self.bot.pool.acquire() as conn:
            await self._ensure_pet_house_columns(conn)
            # yours
            if your_type == "pet":
                your_item = await conn.fetchrow(
                    "SELECT * FROM monster_pets WHERE user_id = $1 AND id = $2;",
                    ctx.author.id,
                    your_item_id,
                )
                your_table = "monster_pets"
            else:
                your_item = await conn.fetchrow(
                    "SELECT * FROM monster_eggs WHERE user_id = $1 AND id = $2 AND hatched = FALSE;",
                    ctx.author.id,
                    your_item_id,
                )
                your_table = "monster_eggs"

            if not your_item:
                await ctx.send(
                    f"❌ You don't have an {'unhatched ' if your_type=='egg' else ''}{your_type} with ID `{your_item_id}`."
                )
                await self.bot.reset_cooldown(ctx)
                return

            # theirs
            if their_type == "pet":
                their_item = await conn.fetchrow("SELECT * FROM monster_pets WHERE id = $1;", their_item_id)
                their_table = "monster_pets"
            else:
                their_item = await conn.fetchrow(
                    "SELECT * FROM monster_eggs WHERE id = $1 AND hatched = FALSE;", their_item_id
                )
                their_table = "monster_eggs"
            if not their_item:
                await ctx.send(
                    f"❌ No {'unhatched ' if their_type=='egg' else ''}{their_type} found with ID `{their_item_id}`."
                )
                await self.bot.reset_cooldown(ctx)
                return

            their_user_id = their_item["user_id"]
            if their_user_id == ctx.author.id:
                await ctx.send("❌ You cannot trade with your own items.")
                await self.bot.reset_cooldown(ctx)
                return

            their_user = self.bot.get_user(their_user_id)
            if not their_user:
                await ctx.send("❌ Could not find the user who owns the item.")
                await self.bot.reset_cooldown(ctx)
                return

            view = TradeConfirmationView(ctx.author, their_user)

            trade_embed = discord.Embed(
                title="🐾 Pet/Egg Trade Proposal",
                description=f"{ctx.author.mention} wants to trade their {your_type} with {their_user.mention}'s {their_type}.",
                color=discord.Color.blue(),
            )

            if your_type == "pet":
                trade_embed.add_field(
                    name=f"{ctx.author.name}'s {your_type.capitalize()}",
                    value=(
                        f"**{your_item['name']}** (ID: `{your_item_id}`)\n"
                        f"**Attack:** {your_item['attack']}\n"
                        f"**HP:** {your_item['hp']}\n"
                        f"**Defense:** {your_item['defense']}\n"
                        f"**IV:** {your_item['IV']}%"
                    ),
                    inline=True,
                )
                yourname = your_item["name"]
            else:
                trade_embed.add_field(
                    name=f"{ctx.author.name}'s {your_type.capitalize()}",
                    value=(
                        f"**{your_item['egg_type']}** (ID: `{your_item_id}`)\n"
                        f"**Attack:** {your_item['attack']}\n"
                        f"**HP:** {your_item['hp']}\n"
                        f"**Defense:** {your_item['defense']}\n"
                        f"**IV:** {your_item['IV']}%"
                    ),
                    inline=True,
                )
                yourname = your_item["egg_type"]

            if their_type == "pet":
                trade_embed.add_field(
                    name=f"{their_user.name}'s {their_type.capitalize()}",
                    value=(
                        f"**{their_item['name']}** (ID: `{their_item_id}`)\n"
                        f"**Attack:** {their_item['attack']}\n"
                        f"**HP:** {their_item['hp']}\n"
                        f"**Defense:** {their_item['defense']}\n"
                        f"**IV:** {their_item['IV']}%"
                    ),
                    inline=True,
                )
                theirname = their_item["name"]
            else:
                trade_embed.add_field(
                    name=f"{their_user.name}'s {their_type.capitalize()}",
                    value=(
                        f"**{their_item['egg_type']}** (ID: `{their_item_id}`)\n"
                        f"**Attack:** {their_item['attack']}\n"
                        f"**HP:** {their_item['hp']}\n"
                        f"**Defense:** {their_item['defense']}\n"
                        f"**IV:** {their_item['IV']}%"
                    ),
                    inline=True,
                )
                theirname = their_item["egg_type"]

            trade_embed.set_footer(text="React below to accept or decline the trade.")
            await ctx.send(embed=trade_embed, view=view)

            await view.wait()

            if view.value is True:
                async with self.bot.pool.acquire() as conn:
                    await self._ensure_pet_house_columns(conn)
                    # re-check existence
                    if your_type == "pet":
                        your_item = await conn.fetchrow(
                            "SELECT * FROM monster_pets WHERE user_id = $1 AND id = $2;",
                            ctx.author.id,
                            your_item_id,
                        )
                    else:
                        your_item = await conn.fetchrow(
                            "SELECT * FROM monster_eggs WHERE user_id = $1 AND id = $2 AND hatched = FALSE;",
                            ctx.author.id,
                            your_item_id,
                        )
                    if not your_item:
                        await ctx.send(f"❌ Your {your_type} is no longer available for trade.")
                        await self.bot.reset_cooldown(ctx)
                        return

                    if their_type == "pet":
                        their_item = await conn.fetchrow("SELECT * FROM monster_pets WHERE id = $1;", their_item_id)
                    else:
                        their_item = await conn.fetchrow(
                            "SELECT * FROM monster_eggs WHERE id = $1 AND hatched = FALSE;", their_item_id
                        )
                    if not their_item:
                        await ctx.send(f"❌ Their {their_type} is no longer available for trade.")
                        await self.bot.reset_cooldown(ctx)
                        return

                    their_user_id = their_item["user_id"]
                    if their_user_id == ctx.author.id:
                        await ctx.send("❌ You cannot trade with your own items.")
                        await self.bot.reset_cooldown(ctx)
                        return

                    their_tier = await conn.fetchval("SELECT tier FROM profile WHERE profile.user = $1", their_user_id)
                    author_tier = await conn.fetchval("SELECT tier FROM profile WHERE profile.user = $1", ctx.author.id)
                    their_pet_count = await self._active_pet_item_count(
                        conn,
                        their_user_id,
                        their_item_id if their_type == "pet" else -1,
                        their_item_id if their_type == "egg" else -1,
                    )
                    author_pet_count = await self._active_pet_item_count(
                        conn,
                        ctx.author.id,
                        your_item_id if your_type == "pet" else -1,
                        your_item_id if your_type == "egg" else -1,
                    )

                    their_member = ctx.guild.get_member(their_user_id) if getattr(ctx, "guild", None) else None
                    author_member = ctx.guild.get_member(ctx.author.id) if getattr(ctx, "guild", None) else None

                    their_max = self._calc_inventory_limit(their_tier, their_member, getattr(ctx, "guild", None))
                    author_max = self._calc_inventory_limit(author_tier, author_member, getattr(ctx, "guild", None))

                    if their_pet_count + 1 > their_max:
                        await ctx.send(
                            f"❌ {their_user.mention} cannot have more than {their_max} active pets or eggs (including pending splices). "
                            f"They currently have {their_pet_count} active items and would exceed the limit."
                        )
                        await self.bot.reset_cooldown(ctx)
                        return
                    if author_pet_count + 1 > author_max:
                        await ctx.send(
                            f"❌ You cannot have more than {author_max} active pets or eggs. You currently have {author_pet_count} active items and would exceed the limit."
                        )
                        await self.bot.reset_cooldown(ctx)
                        return

                try:
                    async with self.bot.pool.acquire() as conn:
                        await self._ensure_pet_house_columns(conn)
                        async with conn.transaction():
                            if your_type == "pet":
                                await conn.execute(
                                    f"UPDATE {your_table} SET user_id = $1, equipped = FALSE, in_house = FALSE WHERE id = $2;",
                                    their_user_id,
                                    your_item_id,
                                )
                            else:
                                await conn.execute(
                                    f"UPDATE {your_table} SET user_id = $1 WHERE id = $2;",
                                    their_user_id,
                                    your_item_id,
                                )

                            if their_type == "pet":
                                await conn.execute(
                                    f"UPDATE {their_table} SET user_id = $1, equipped = FALSE, in_house = FALSE WHERE id = $2;",
                                    ctx.author.id,
                                    their_item_id,
                                )
                            else:
                                await conn.execute(
                                    f"UPDATE {their_table} SET user_id = $1 WHERE id = $2;",
                                    ctx.author.id,
                                    their_item_id,
                                )

                    success_embed = discord.Embed(
                        title="✅ Trade Successful!",
                        description=(
                            f"{ctx.author.mention} traded their **{your_type}** **{yourname}** with "
                            f"{their_user.mention}'s **{their_type}** **{theirname}**."
                        ),
                        color=discord.Color.green(),
                    )
                    await ctx.send(embed=success_embed)
                except Exception as e:
                    await ctx.send(embed=discord.Embed(title="❌ Trade Failed", description=str(e), color=discord.Color.red()))
                    await self.bot.reset_cooldown(ctx)
            elif view.value is False:
                await ctx.send(
                    embed=discord.Embed(
                        title="❌ Trade Declined",
                        description=f"{their_user.mention} has declined the trade request from {ctx.author.mention}.",
                        color=discord.Color.red(),
                    )
                )
                await self.bot.reset_cooldown(ctx)
            else:
                await ctx.send(
                    embed=discord.Embed(
                        title="⌛ Trade Timed Out",
                        description=f"The trade request to {their_user.mention} timed out. No changes were made.",
                        color=discord.Color.orange(),
                    )
                )
                await self.bot.reset_cooldown(ctx)

    def create_item_embed(self, user: discord.User, item_type: str, item: asyncpg.Record, item_id: int) -> discord.Embed:
        """Creates a simple stats embed for an item."""
        item_type = item_type.lower()
        try:
            if item_type == "pet":
                item_name = item["name"]
            else:
                item_name = item["egg_type"]

            embed = discord.Embed(
                title=f"{user.name}'s {item_type.capitalize()}",
                description=f"**Name:** {item_name}\n**ID:** `{item_id}`",
                color=discord.Color.blue(),
            )
            embed.add_field(
                name="📊 Stats",
                value=(
                    f"**Attack:** {safe_get(item, 'attack', 0)}\n"
                    f"**HP:** {safe_get(item, 'hp', 0)}\n"
                    f"**Defense:** {safe_get(item, 'defense', 0)}\n"
                    f"**IV:** {safe_get(item, 'IV', 0)}%"
                ),
                inline=False,
            )
            return embed
        except Exception as e:
            return discord.Embed(title="Error in create_item_embed", description=str(e), color=discord.Color.red())

    @user_cooldown(600)
    @pets.command(brief="Sell your pet or egg to another user for in-game money")
    @has_char()
    async def sell(self, ctx, item_type: str, your_item_id: int, buyer: discord.Member, price: int):
        item_type = item_type.lower()
        if item_type not in ["pet", "egg"]:
            await ctx.send("❌ Invalid type specified. Use `pet` or `egg`.")
            await self.bot.reset_cooldown(ctx)
            return
        if price <= 0:
            await ctx.send("❌ The price must be a positive integer.")
            await self.bot.reset_cooldown(ctx)
            return
        if buyer.id == ctx.author.id:
            await ctx.send("❌ You cannot sell an item to yourself.")
            await self.bot.reset_cooldown(ctx)
            return

        async with self.bot.pool.acquire() as conn:
            await self._ensure_pet_house_columns(conn)
            if item_type == "pet":
                your_item = await conn.fetchrow(
                    "SELECT * FROM monster_pets WHERE user_id = $1 AND id = $2;",
                    ctx.author.id,
                    your_item_id,
                )
                your_table = "monster_pets"
            else:
                your_item = await conn.fetchrow(
                    "SELECT * FROM monster_eggs WHERE user_id = $1 AND id = $2;",
                    ctx.author.id,
                    your_item_id,
                )
                your_table = "monster_eggs"

            if not your_item:
                await ctx.send(f"❌ You don't have a {item_type} with ID `{your_item_id}`.")
                await self.bot.reset_cooldown(ctx)
                return

            buyer_money = await conn.fetchval('SELECT "money" FROM profile WHERE "user" = $1;', buyer.id)
            if buyer_money is None:
                await ctx.send("❌ The buyer does not have a profile.")
                await self.bot.reset_cooldown(ctx)
                return
            if buyer_money < price:
                await ctx.send(f"❌ {buyer.mention} does not have enough money to buy the item.")
                await self.bot.reset_cooldown(ctx)
                return

            sale_embed = discord.Embed(
                title="💰 Item Sale Proposal",
                description=f"{ctx.author.mention} is offering to sell their {item_type} to {buyer.mention} for **${price}**.",
                color=discord.Color.gold(),
            )
            if item_type == "pet":
                sale_embed.add_field(
                    name=f"{ctx.author.name}'s Pet",
                    value=(
                        f"**{your_item['name']}** (ID: `{your_item_id}`)\n"
                        f"**Attack:** {your_item['attack']}\n"
                        f"**HP:** {your_item['hp']}\n"
                        f"**Defense:** {your_item['defense']}\n"
                        f"**IV:** {your_item['IV']}%"
                    ),
                    inline=True,
                )
                item_name = your_item["name"]
            else:
                sale_embed.add_field(
                    name=f"{ctx.author.name}'s Egg",
                    value=(
                        f"**{your_item['egg_type']}** (ID: `{your_item_id}`)\n"
                        f"**Attack:** {your_item['attack']}\n"
                        f"**HP:** {your_item['hp']}\n"
                        f"**Defense:** {your_item['defense']}\n"
                        f"**IV:** {your_item['IV']}%"
                    ),
                    inline=True,
                )
                item_name = your_item["egg_type"]

            sale_embed.set_footer(text="React below to accept or decline the sale.")
            view = SellConfirmationView(ctx.author, buyer, price)
            await ctx.send(embed=sale_embed, view=view)

            await view.wait()

            if view.value is True:
                try:
                    buyer_tier = await conn.fetchval("SELECT tier FROM profile WHERE profile.user = $1", buyer.id)
                    pet_and_egg_count = await conn.fetchval(
                        """
                        SELECT COUNT(*) FROM (
                            SELECT id FROM monster_pets WHERE user_id = $1 AND COALESCE(in_house, FALSE) = FALSE
                            UNION ALL
                            SELECT id FROM monster_eggs WHERE user_id = $1 AND hatched = FALSE
                        ) AS combined
                        """,
                        buyer.id,
                    )
                except Exception as e:
                    await ctx.send(_("An error occurred while checking pets and eggs. Please try again later."))
                    if hasattr(self.bot, "logger"):
                        self.bot.logger.error(f"Error checking pet and egg count: {e}")
                    return

                maxslot = self._calc_inventory_limit(buyer_tier, buyer, getattr(ctx, "guild", None))

                if pet_and_egg_count >= maxslot:
                    await ctx.send(_("They cannot have more than the active pet/egg limit. Free a slot or store pets in Oikos."))
                    return

                buyer_money = await conn.fetchval('SELECT "money" FROM profile WHERE "user" = $1;', buyer.id)
                if buyer_money < price:
                    await ctx.send(f"❌ {buyer.mention} does not have enough money to buy the item.")
                    await self.bot.reset_cooldown(ctx)
                    return

                try:
                    async with conn.transaction():
                        if item_type == "pet":
                            await conn.execute(
                                f"UPDATE {your_table} SET user_id = $1, in_house = FALSE, equipped = FALSE WHERE id = $2;",
                                buyer.id,
                                your_item_id,
                            )
                        else:
                            await conn.execute(f"UPDATE {your_table} SET user_id = $1 WHERE id = $2;", buyer.id, your_item_id)
                        await conn.execute('UPDATE profile SET money = money - $1 WHERE "user" = $2;', price, buyer.id)
                        await conn.execute('UPDATE profile SET money = money + $1 WHERE "user" = $2;', price, ctx.author.id)

                    await ctx.send(
                        embed=discord.Embed(
                            title="✅ Sale Successful!",
                            description=(
                                f"**{item_name}** has been sold to {buyer.mention} for **${price}**.\n"
                                f"{ctx.author.mention} has received **${price}**."
                            ),
                            color=discord.Color.green(),
                        )
                    )
                except Exception as e:
                    await ctx.send(embed=discord.Embed(title="❌ Sale Failed", description=str(e), color=discord.Color.red()))
                    await self.bot.reset_cooldown(ctx)
            elif view.value is False:
                await ctx.send(embed=discord.Embed(title="❌ Sale Declined", description=f"{buyer.mention} has declined the sale offer from {ctx.author.mention}.", color=discord.Color.red()))
                await self.bot.reset_cooldown(ctx)
            else:
                await ctx.send(embed=discord.Embed(title="⌛ Sale Timed Out", description=f"The sale offer to {buyer.mention} timed out. No changes were made.", color=discord.Color.orange()))
                await self.bot.reset_cooldown(ctx)

    class EggSelect(discord.ui.Select):
        def __init__(self, eggs):
            options = []
            for i, egg in enumerate(eggs):
                element = (egg.get('element') or 'unknown').lower()
                if 'fire' in element:
                    element_emoji = "🔥"
                elif 'water' in element:
                    element_emoji = "💧"
                elif 'electric' in element:
                    element_emoji = "⚡"
                elif 'light' in element:
                    element_emoji = "✨"
                elif 'dark' in element:
                    element_emoji = "🌑"
                elif 'wind' in element or 'nature' in element:
                    element_emoji = "🌿"
                elif 'corrupt' in element:
                    element_emoji = "☠️"
                else:
                    element_emoji = "🥚"
                options.append(
                    discord.SelectOption(
                        label=f"{egg.get('egg_type','?')} (ID: {egg.get('id','?')})",
                        description=f"{egg.get('element','?')} | IV: {safe_get(egg,'IV',0)}%",
                        value=str(i),
                        emoji=element_emoji,
                    )
                )
            super().__init__(placeholder="Select an egg to view...", min_values=1, max_values=1, options=options)

        async def callback(self, interaction: discord.Interaction):
            view = self.view
            if interaction.user.id != view.author.id:
                return await interaction.response.send_message("These are not your eggs.", ephemeral=True)
            view.index = int(self.values[0])
            await view.send_page(interaction)

    class EggPaginator(discord.ui.View):
        def __init__(self, eggs, author):
            super().__init__(timeout=60)
            self.eggs = eggs
            self.author = author
            self.index = 0
            self.message = None
            if eggs:
                self.add_item(Pets.EggSelect(eggs))

        async def on_timeout(self):
            if self.message:
                try:
                    await self.message.delete()
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass

        def get_embed(self):
            egg = self.eggs[self.index]

            # Hatching timer (DB naive -> aware, then math with aware 'now')
            hatch_at = from_db_naive_utc(egg["hatch_time"])
            time_left_str = "Ready to hatch!"
            if hatch_at:
                delta = hatch_at - now_utc()
                time_left_str = "Ready to hatch!" if delta.total_seconds() <= 0 else str(delta).split(".")[0]

            # Fun special-case
            hp_display = "???" if egg.get('id') == 6666 else egg.get('hp')
            attack_display = "???" if egg.get('id') == 6666 else egg.get('attack')
            defense_display = "???" if egg.get('id') == 6666 else egg.get('defense')

            iv = safe_get(egg, 'IV', 0)
            if iv >= 90:
                color = discord.Color.gold()
            elif iv >= 75:
                color = discord.Color.purple()
            elif iv >= 50:
                color = discord.Color.blue()
            else:
                color = discord.Color.green()

            embed = discord.Embed(
                title=f"🥚 Your Egg: {egg.get('egg_type','?')}",
                color=color,
                description=f"**ID:** {egg.get('id','?')}\n**Element:** {egg.get('element','?')}",
            )
            embed.add_field(
                name="✨ **Stats**",
                value=(
                    f"**IV:** {iv}%\n"
                    f"**HP:** {hp_display}\n"
                    f"**Attack:** {attack_display}\n"
                    f"**Defense:** {defense_display}"
                ),
                inline=False,
            )
            embed.add_field(name="⏳ **Hatching Time**", value=time_left_str, inline=False)
            if egg.get('url'):
                embed.set_image(url=egg['url'])
            embed.set_footer(text=f"Viewing egg {self.index + 1} of {len(self.eggs)} | Use the dropdown to navigate")
            return embed

        async def send_page(self, interaction: discord.Interaction):
            embed = self.get_embed()
            if self.message is None:
                self.message = interaction.message
            if interaction.response.is_done():
                await self.message.edit(embed=embed, view=self)
            else:
                await interaction.response.edit_message(embed=embed, view=self)

        @discord.ui.button(label="Close", style=discord.ButtonStyle.red, row=1)
        async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user.id != self.author.id:
                return await interaction.response.send_message("These are not your eggs.", ephemeral=True)
            await interaction.message.delete()
            self.stop()

    @pets.command(brief=_("Check your monster eggs"))
    async def eggs(self, ctx):
        async with self.bot.pool.acquire() as conn:
            eggs = await conn.fetch("SELECT * FROM monster_eggs WHERE user_id = $1 AND hatched = FALSE;", ctx.author.id)
            if not eggs:
                await ctx.send(_("You don't have any eggs to incubate."))
                return
        view = self.EggPaginator(eggs, ctx.author)
        embed = view.get_embed()
        view.message = await ctx.send(embed=embed, view=view)

    @pets.command(name="divineshards", aliases=["dshards"], brief=_("Check your divine familiar shard progress"))
    async def divineshards(self, ctx):
        async with self.bot.pool.acquire() as conn:
            await ensure_divine_familiar_tables(conn)
            shard_counts = await get_divine_shard_counts(conn, ctx.author.id)

        embed = discord.Embed(
            title="✨ Divine Familiar Shards",
            description="Collect shards from Spring PvE and god raids. Craft an egg at 20 shards.",
            color=discord.Color.gold(),
        )

        progress_emoji_by_key = {
            "astraea_familiar": "🟨",     # Sorinveil
            "sepulchure_familiar": "🟦",  # Vaion
            "drakath_familiar": "⬜",     # Astrephiel
            "primordial_familiar": "🟪",  # Mayeia
        }

        for key, familiar in DIVINE_FAMILIARS.items():
            current = int(shard_counts.get(key, 0))
            required = max(1, int(DIVINE_SHARDS_PER_EGG))
            if current <= 0:
                progress_slots = 0
            else:
                # Ceil scaling so any non-zero shard progress shows at least one filled block.
                progress_slots = min(10, max(1, (current * 10 + required - 1) // required))
            fill_emoji = progress_emoji_by_key.get(key, "🟪")
            progress_bar = (fill_emoji * progress_slots) + ("⬛" * (10 - progress_slots))
            embed.add_field(
                name=format_familiar_display_name(familiar),
                value=f"**{current}/{DIVINE_SHARDS_PER_EGG}**\n{progress_bar}",
                inline=False,
            )

        embed.set_footer(
            text=f"Craft: {ctx.clean_prefix}pets divinecraft <familiar name>"
        )
        await ctx.send(embed=embed)

    @pets.command(name="divinecraft", aliases=["dcraft"], brief=_("Craft a divine familiar egg from shards"))
    async def divinecraft(self, ctx, *, familiar_input: str):
        familiar_key = resolve_familiar_key(familiar_input)
        if familiar_key is None:
            valid = ", ".join([cfg["name"] for cfg in DIVINE_FAMILIARS.values()])
            return await ctx.send(
                _("Unknown divine familiar. Valid options: {valid}").format(valid=valid)
            )

        async with self.bot.pool.acquire() as conn:
            await self._ensure_pet_house_columns(conn)
            await ensure_divine_familiar_tables(conn)

            tier = await conn.fetchval(
                "SELECT tier FROM profile WHERE profile.user = $1",
                ctx.author.id,
            )
            member = (
                ctx.guild.get_member(ctx.author.id)
                if getattr(ctx, "guild", None)
                else None
            )
            maxslot = self._calc_inventory_limit(
                tier,
                member,
                getattr(ctx, "guild", None),
            )
            active_count = await self._active_pet_item_count(conn, ctx.author.id)
            if active_count >= maxslot:
                return await ctx.send(
                    _("Your active inventory is full (**{count}/{limit}**).").format(
                        count=active_count,
                        limit=maxslot,
                    )
                )

            result = await craft_divine_egg(
                conn,
                ctx.author.id,
                familiar_key,
                shards_required=DIVINE_SHARDS_PER_EGG,
            )

        if not result.get("ok"):
            remaining = int(result.get("remaining", 0))
            needed = max(0, DIVINE_SHARDS_PER_EGG - remaining)
            familiar_name = get_familiar_display_name(familiar_key)
            if result.get("error") == "not_enough_shards":
                return await ctx.send(
                    _(
                        "You need **{needed}** more shard(s) for **{name}** "
                        "(currently **{current}/{required}**)."
                    ).format(
                        needed=needed,
                        name=familiar_name,
                        current=remaining,
                        required=DIVINE_SHARDS_PER_EGG,
                    )
                )
            return await ctx.send(_("Could not craft this divine egg right now."))

        stats = result["stats"]
        familiar = result["familiar"]
        embed = discord.Embed(
            title="🐣 Divine Egg Crafted",
            description=(
                f"Crafted **{format_familiar_display_name(familiar)} Egg** from shards.\n"
                f"Remaining shards: **{result['remaining']}**"
            ),
            color=discord.Color.green(),
        )
        embed.add_field(
            name="Stats",
            value=(
                f"HP: **{stats['hp']}**\n"
                f"ATK: **{stats['attack']}**\n"
                f"DEF: **{stats['defense']}**\n"
                f"IV: **{stats['iv_percent']:.2f}%**"
            ),
            inline=True,
        )
        embed.add_field(name="Element", value=f"**{familiar['element']}**", inline=True)
        embed.add_field(name="Egg ID", value=f"`{result['egg_id']}`", inline=True)
        if familiar.get("url"):
            embed.set_thumbnail(url=familiar["url"])
        await ctx.send(embed=embed)

    @pets.command(brief=_("Equip a pet to fight alongside you in battles"))
    async def equip(self, ctx, pet_id: int):
        try:
            async with self.bot.pool.acquire() as conn:
                await self._ensure_pet_house_columns(conn)
                pet = await conn.fetchrow("SELECT * FROM monster_pets WHERE user_id = $1 AND id = $2;", ctx.author.id, pet_id)
                if not pet:
                    await ctx.send(f"❌ You don't have a pet with ID {pet_id}.")
                    return
                if bool(pet.get("in_house")):
                    await ctx.send(f"❌ **{pet['name']}** is stored in your Oikos. Use `$pets unstore {pet_id}` first.")
                    return
                if pet["growth_stage"] not in ["young", "adult"]:
                    await ctx.send(f"❌ **{pet['name']}** must be at least in the **young** growth stage to be equipped.")
                    return
                # Unequip current
                await conn.execute("UPDATE monster_pets SET equipped = FALSE WHERE user_id = $1 AND equipped = TRUE;", ctx.author.id)
                # Equip selected
                await conn.execute("UPDATE monster_pets SET equipped = TRUE WHERE id = $1;", pet_id)

            trust_info = getattr(self, "get_trust_level_info", lambda x: {"emoji": "🤝", "name": "Trust", "bonus": 0})(pet.get('trust_level', 0))
            embed = discord.Embed(title="⚔️ Pet Equipped!", description=f"**{pet['name']}** is now equipped and ready for battle!", color=discord.Color.green())
            embed.add_field(name="📊 Battle Stats", value=f"**HP:** {pet['hp']}\n**Attack:** {pet['attack']}\n**Defense:** {pet['defense']}\n**Element:** {pet['element']}", inline=True)
            embed.add_field(name="🌟 Trust Bonus", value=f"{trust_info['emoji']} **{trust_info['name']}**\n**Battle Bonus:** {trust_info['bonus']:+d}%", inline=True)
            embed.set_footer(text="Your pet will now fight alongside you in battles and raids!")
            await ctx.send(embed=embed)
        except Exception as e:
            await ctx.send(f"❌ An error occurred while equipping the pet: {e}")

    @pets.command(brief=_("Unequip your currently equipped pet"))
    async def unequip(self, ctx):
        async with self.bot.pool.acquire() as conn:
            pet = await conn.fetchrow("SELECT * FROM monster_pets WHERE user_id = $1 AND equipped = TRUE;", ctx.author.id)
            if not pet:
                await ctx.send("❌ You don't have any pet currently equipped.")
                return
            await conn.execute("UPDATE monster_pets SET equipped = FALSE WHERE id = $1;", pet['id'])
        embed = discord.Embed(title="🎒 Pet Unequipped", description=f"**{pet['name']}** has been unequipped and is now resting.", color=discord.Color.blue())
        embed.set_footer(text="Your pet will no longer participate in battles until re-equipped.")
        await ctx.send(embed=embed)

    # ---------- background loops ----------

    @tasks.loop(minutes=1)
    async def check_pet_growth(self):
        """Advance pet growth when growth_time elapses.
        Uses SQL NOW() + INTERVAL where possible to avoid tz issues."""
        try:
            growth_stages = GROWTH_STAGES
            async with self.bot.pool.acquire() as conn:
                pets = await conn.fetch(
                    """
                    SELECT id, user_id, name, growth_stage, growth_index, growth_time,
                           hp, attack, defense,
                           COALESCE(growth_speed_multiplier, 1.0) AS growth_speed_multiplier,
                           COALESCE(speed_growth_active, FALSE)    AS speed_growth_active
                    FROM monster_pets
                    WHERE growth_time <= NOW() AND growth_stage != 'adult';
                    """
                )
                for pet in pets:
                    next_idx = pet["growth_index"] + 1
                    if next_idx not in growth_stages:
                        continue
                    stage = growth_stages[next_idx]

                    # Next interval with multiplier
                    if stage["growth_time"] is not None:
                        mult = pet["growth_speed_multiplier"] or 1.0
                        if pet["speed_growth_active"] and mult < 2.0:
                            mult = 2.0
                            await conn.execute("UPDATE monster_pets SET growth_speed_multiplier = 2.0 WHERE id = $1;", pet["id"])
                        effective_days = stage["growth_time"] / max(mult, 1e-9)
                        # Use INTERVAL in SQL (timedelta maps to INTERVAL in asyncpg)
                        growth_delta = timedelta(days=effective_days)
                    else:
                        growth_delta = None

                    old_mult = growth_stages[pet["growth_index"]]["stat_multiplier"]
                    new_mult = stage["stat_multiplier"]
                    ratio = new_mult / max(old_mult, 1e-9)

                    newhp = pet["hp"] * ratio
                    newatk = pet["attack"] * ratio
                    newdef = pet["defense"] * ratio

                    if growth_delta is not None:
                        await conn.fetchrow(
                            """
                            UPDATE monster_pets
                            SET growth_stage = $1, growth_time = NOW() + $2,
                                hp = $3, attack = $4, defense = $5, growth_index = $6
                            WHERE id = $7
                            RETURNING hp, attack, defense;
                            """,
                            stage["stage"], growth_delta, newhp, newatk, newdef, next_idx, pet["id"],
                        )
                    else:
                        await conn.fetchrow(
                            """
                            UPDATE monster_pets
                            SET growth_stage = $1, growth_time = NULL,
                                hp = $2, attack = $3, defense = $4, growth_index = $5
                            WHERE id = $6
                            RETURNING hp, attack, defense;
                            """,
                            stage["stage"], newhp, newatk, newdef, next_idx, pet["id"],
                        )

                    if stage["stage"] == "adult":
                        await conn.execute(
                            "UPDATE monster_pets SET speed_growth_active = FALSE, growth_speed_multiplier = 1.0 WHERE id = $1;",
                            pet["id"],
                        )

                    user = self.bot.get_user(pet["user_id"])
                    if user:
                        mult = pet["growth_speed_multiplier"] or 1.0
                        msg = f"Your pet **{pet['name']}** has grown into a {stage['stage']}!"
                        if stage["stage"] != "adult" and mult > 1.0:
                            msg += f" (Growth speed {mult:.1f}× active!)"
                        elif stage["stage"] == "adult" and (pet["speed_growth_active"] or mult > 1.0):
                            msg += " (Growth speed boost has ended)"
                        try:
                            await user.send(msg)
                        except Exception:
                            pass
        except Exception as e:
            print(f"Error in check_pet_growth: {e}")

    @tasks.loop(minutes=1)
    async def check_egg_hatches(self):
        """Hatch eggs when hatch_time (stored as naive UTC in DB) has elapsed."""
        try:
            async with self.bot.pool.acquire() as conn:
                cutoff = to_db_naive_utc(now_utc())  # param type matches TIMESTAMP WITHOUT TIME ZONE
                eggs = await conn.fetch(
                    "SELECT * FROM monster_eggs WHERE hatched = FALSE AND hatch_time <= $1;",
                    cutoff
                )
                for egg in eggs:
                    await conn.execute("UPDATE monster_eggs SET hatched = TRUE WHERE id = $1;", egg["id"])

                    baby = GROWTH_STAGES[1]
                    stat_mult = baby["stat_multiplier"]

                    hp = round(egg["hp"] * stat_mult)
                    attack = round(egg["attack"] * stat_mult)
                    defense = round(egg["defense"] * stat_mult)
                    iv_value = egg.get("IV") or egg.get("iv") or 0

                    # Set next growth_time via NOW() + INTERVAL to avoid tz mismatch regardless of column type
                    await conn.execute(
                        """
                        INSERT INTO monster_pets (
                            user_id, name, default_name, hp, attack, defense, element, url,
                            growth_stage, growth_index, growth_time, "IV"
                        )
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, NOW() + $11, $12);
                        """,
                        egg["user_id"],
                        egg["egg_type"],
                        egg["egg_type"],
                        hp,
                        attack,
                        defense,
                        egg["element"],
                        egg["url"],
                        baby["stage"],
                        1,
                        timedelta(days=baby["growth_time"]),
                        iv_value,
                    )

                    user = self.bot.get_user(egg["user_id"])
                    if user:
                        try:
                            await user.send(
                                f"Your **Egg** has hatched into a pet named **{egg['egg_type']}**! Check your pet menu to see it."
                            )
                        except Exception:
                            pass
        except Exception as e:
            print(f"Error in check_egg_hatches: {e}")
            user = self.bot.get_user(524674960153903126)
            if user:
                try:
                    await user.send(f"Error in check_egg_hatches: {e}")
                except Exception:
                    pass

    # ---------- GM helpers ----------

    @is_gm()
    @commands.command(name="gmcreatemonster")
    async def gmcreatemonster(self, ctx):
        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel

        await ctx.send("Please enter the **name** of the monster (or type `cancel` to cancel):")
        try:
            name_msg = await ctx.bot.wait_for("message", timeout=60.0, check=check)
        except asyncio.TimeoutError:
            return await ctx.send("Timed out. Monster creation cancelled.")
        if name_msg.content.lower() == "cancel":
            return await ctx.send("Monster creation cancelled.")
        monster_name = name_msg.content.strip()

        await ctx.send("Please enter the **level** of the monster (1-10) (or type `cancel` to cancel):")
        try:
            level_msg = await ctx.bot.wait_for("message", timeout=60.0, check=check)
        except asyncio.TimeoutError:
            return await ctx.send("Timed out. Monster creation cancelled.")
        if level_msg.content.lower() == "cancel":
            return await ctx.send("Monster creation cancelled.")
        try:
            level_int = int(level_msg.content.strip())
            if level_int < 1 or level_int > 10:
                return await ctx.send("Invalid level. Must be between 1 and 10. Monster creation cancelled.")
        except ValueError:
            return await ctx.send("Invalid input for level. Monster creation cancelled.")

        valid_elements = {"Corrupted", "Water", "Electric", "Light", "Dark", "Wind", "Nature", "Fire"}
        await ctx.send("Please enter the **element** of the monster (or type `cancel` to cancel):\n" f"Valid elements are: {', '.join(valid_elements)}")
        try:
            element_msg = await ctx.bot.wait_for("message", timeout=60.0, check=check)
        except asyncio.TimeoutError:
            return await ctx.send("Timed out. Monster creation cancelled.")
        if element_msg.content.lower() == "cancel":
            return await ctx.send("Monster creation cancelled.")
        monster_element = element_msg.content.strip().capitalize()
        if monster_element not in valid_elements:
            return await ctx.send("Invalid element. Must be one of: " + ", ".join(valid_elements) + ". Monster creation cancelled.")

        await ctx.send("Please enter the **HP, Attack, and Defense** of the monster in the format:\n`hp, attack, defense` (e.g., `100, 95, 100`) (or type `cancel` to cancel):")
        try:
            stats_msg = await ctx.bot.wait_for("message", timeout=60.0, check=check)
        except asyncio.TimeoutError:
            return await ctx.send("Timed out. Monster creation cancelled.")
        if stats_msg.content.lower() == "cancel":
            return await ctx.send("Monster creation cancelled.")
        try:
            parts = [part.strip() for part in stats_msg.content.split(",")]
            if len(parts) != 3:
                return await ctx.send("Invalid format. Expected format: `hp, attack, defense`. Monster creation cancelled.")
            hp_val, attack_val, defense_val = map(int, parts)
        except ValueError:
            return await ctx.send("Stat values must be integers. Monster creation cancelled.")

        await ctx.send("Please enter the **image URL** for the monster (must end with `.png`, `.jpg` or `.webp`) (or type `cancel` to cancel):")
        try:
            url_msg = await ctx.bot.wait_for("message", timeout=60.0, check=check)
        except asyncio.TimeoutError:
            return await ctx.send("Timed out. Monster creation cancelled.")
        if url_msg.content.lower() == "cancel":
            return await ctx.send("Monster creation cancelled.")
        monster_url = url_msg.content.strip()
        if not (monster_url.lower().endswith((".png", ".jpg", ".webp"))):
            return await ctx.send("Invalid image URL. Must end with `.png`, `.jpg`, or `.webp`. Monster creation cancelled.")

        await ctx.send("Please enter whether the monster is public and found in the wild (`true` or `false`) (or type `cancel` to cancel):")
        try:
            public_msg = await ctx.bot.wait_for("message", timeout=60.0, check=check)
        except asyncio.TimeoutError:
            return await ctx.send("Timed out. Monster creation cancelled.")
        if public_msg.content.lower() == "cancel":
            return await ctx.send("Monster creation cancelled.")
        ispublic_str = public_msg.content.strip().lower()
        if ispublic_str not in ["true", "false"]:
            return await ctx.send("Invalid input for ispublic. Must be `true` or `false`. Monster creation cancelled.")
        is_public = ispublic_str == "true"

        new_monster = {
            "name": monster_name,
            "hp": hp_val,
            "attack": attack_val,
            "defense": defense_val,
            "element": monster_element,
            "url": monster_url,
            "ispublic": is_public,
        }

        try:
            with open("monsters.json", "r") as f:
                data = json.load(f)
        except Exception:
            return await ctx.send("Error loading monsters data. Monster creation cancelled.")

        data.setdefault(str(level_int), []).append(new_monster)

        try:
            with open("monsters.json", "w") as f:
                json.dump(data, f, indent=4)
        except Exception:
            return await ctx.send("Error saving monsters data. Monster creation cancelled.")

        await ctx.send(f"Monster **{monster_name}** has been successfully added to level {level_int}!")

    @is_gm()
    @commands.command(
        name="gmgivespliceegg",
        brief="GM: give a splice egg from splice_combinations (by ID or result name)"
    )
    async def gmgivespliceegg(self, ctx: commands.Context, key: str, member: Optional[discord.Member] = None):
        """
        Give a splice egg based on a row in `splice_combinations`.
        Usage:
          $gmgivespliceegg <id> [@member]
          $gmgivespliceegg "<result_name with spaces>" [@member]
        """
        import random as _r

        target = member or ctx.author

        def _is_int(s: str) -> bool:
            return s.isdigit()

        try:
            async with self.bot.pool.acquire() as conn:
                await self._ensure_pet_house_columns(conn)
                if _is_int(key):
                    row = await conn.fetchrow(
                        """
                        SELECT id, result_name, hp, attack, defense, element, url
                        FROM splice_combinations
                        WHERE id = $1
                        """,
                        int(key),
                    )
                else:
                    row = await conn.fetchrow(
                        """
                        SELECT id, result_name, hp, attack, defense, element, url
                        FROM splice_combinations
                        WHERE LOWER(result_name) = LOWER($1)
                        """,
                        key,
                    )

                if not row:
                    hint = "ID" if _is_int(key) else "result name"
                    return await ctx.send(f"❌ No splice combination found for {hint} `{key}`.")

                combo_id   = row["id"]
                result_name = row["result_name"] or "Spliced"
                base_hp    = int(row["hp"] or 100)
                base_atk   = int(row["attack"] or 100)
                base_def   = int(row["defense"] or 100)
                element    = row["element"] or "Corrupted"
                img_url    = row["url"]

                # Capacity check
                pet_and_egg_count = await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM (
                        SELECT id FROM monster_pets WHERE user_id = $1 AND COALESCE(in_house, FALSE) = FALSE
                        UNION ALL
                        SELECT id FROM monster_eggs WHERE user_id = $1 AND hatched = FALSE
                    ) AS combined
                    """,
                    target.id,
                )
                buyer_tier = await conn.fetchval("SELECT tier FROM profile WHERE profile.user = $1", target.id)
                maxslot = self._calc_inventory_limit(buyer_tier, target, getattr(ctx, "guild", None))

                if pet_and_egg_count >= maxslot:
                    return await ctx.send("❌ Target inventory full. They must free a slot before receiving another egg.")

                # Prepare stats
                iv_min, iv_max = 50, 100
                iv_roll = _r.randint(iv_min, iv_max)

                def vary(v: int) -> int:
                    delta = max(1, int(round(v * 0.1)))
                    return max(1, v + _r.randint(-delta, delta))

                hp  = vary(base_hp)
                atk = vary(base_atk)
                df  = vary(base_def)

                egg_type = f"{result_name} Egg"
                hatch_days = 2
                hatch_time = now_utc() + timedelta(days=hatch_days)

                egg_id = await conn.fetchval(
                    """
                    INSERT INTO monster_eggs (user_id, egg_type, element, hp, attack, defense, "IV", url, hatched, hatch_time)
                    VALUES ($1,       $2,       $3,      $4,  $5,     $6,      $7,  $8,  FALSE,   $9)
                    RETURNING id
                    """,
                    target.id, egg_type, element, hp, atk, df, iv_roll, img_url,
                    to_db_naive_utc(hatch_time)
                )

            # Notify
            embed = discord.Embed(
                title="🐣 Splice Egg Granted",
                description=f"**{egg_type}** (combo #{combo_id}) has been added to {target.mention}.",
                color=discord.Color.purple(),
            )
            embed.add_field(name="Result", value=result_name, inline=True)
            embed.add_field(name="Element", value=element, inline=True)
            embed.add_field(name="IV", value=f"{iv_roll}%", inline=True)
            embed.add_field(name="Stats", value=f"HP {hp} • ATK {atk} • DEF {df}", inline=False)
            embed.add_field(name="Hatch Time", value=f"{hatch_days} day(s) (Egg ID: {egg_id})", inline=False)
            if img_url:
                embed.set_thumbnail(url=img_url)

            await ctx.send(embed=embed)

            try:
                if target.id != ctx.author.id:
                    await target.send(f"🎁 You received a **Splice Egg** (**{egg_type}**) from {ctx.author.mention}!")
            except Exception:
                pass

        except Exception as e:
            await ctx.send(f"❌ Failed to grant splice egg: {e}")

    @pets.command(brief=_("Release a pet or an egg with a sad farewell"))
    async def release(self, ctx, id: int):
        """Release a pet or an egg with a sad farewell story."""
        import random

        # (Stories unchanged for brevity) — same as your original lists
        pet_stories_standard = [
            _("You whisper farewell to **{name}**, and the wind carries your words to Olympus, where even the gods turn their faces away from mortal sorrow."),
            _("With trembling hands you release **{name}**; its bewildered gaze follows you like a prayer unanswered beneath Apollo’s fading light."),
            _("**{name}** looks back again and again, each glance a plea to the Fates to weave its thread beside yours once more."),
            _("As you walk away, **{name}**’s quiet whimper joins the laments of the nymphs, echoing through the trees until even Echo falls silent."),
            _("The warmth of **{name}**’s touch fades from your hand, and Hermes himself bears the memory to the realm where lost bonds sleep."),
        ]
        pet_stories_extra = [
            _("**{name}** paws at your feet as you turn away, and even Hermes pauses his journey—unwilling to carry such sorrow to the heavens."),
            _("The light of trust fades from **{name}**’s eyes, and the gods bear witness to a bond unmade, their silence heavier than thunder."),
            _("You walk away, and **{name}**’s cry rises through the air like a prayer denied—its echo carried by Apollo’s dying light."),
            _("At the place you left it, **{name}** waits beneath the cypress trees, a faithful soul watched only by the indifferent stars."),
            _("As night falls, **{name}** curls upon the earth, and the Fates weave its grief into the fabric of eternity."),
        ]
        pet_stories_extra_extra = [
            _("As you turn away, **{name}** calls into the storm—its voice carried to Olympus, where even the gods fall silent before such devotion betrayed."),
            _("**{name}** follows your fading scent until its strength fails beneath the gaze of Artemis, who mourns its loyalty with silver tears of rain."),
            _("The Fates record **{name}**’s final cry in their loom, a thread woven with sorrow that no mortal hand can ever unspool."),
            _("**{name}** lingers where you left it, guarded only by shadows—Hecate’s hounds watching in silence as its heart fades into myth."),
            _("Each night, your dreams echo with **{name}**’s voice, carried by Hermes across the veil—a reminder that no mortal ever escapes the weight of love betrayed."),
        ]
        pet_stories_darkest = [
            _("When you turn away, **{name}** calls out once—its voice rising to Olympus itself. The gods hear, but none dare answer a plea born of mortal betrayal."),
            _("**{name}** wanders beneath the gaze of uncaring stars until even Artemis turns her light aside, unwilling to witness such loyal sorrow."),
            _("The bond once blessed by the gods unravels; the thread between you and **{name}** severed by Atropos herself, leaving only silence where love once dwelled."),
            _("**{name}** waits at the crossroads where you parted, guarded only by Hecate’s shadows, its faithful heart fading into legend and dust."),
            _("As the seasons turn, whispers reach the underworld—Hermes speaks of a creature who died still calling your name, and even Hades lowers his gaze."),
        ]

        egg_stories_standard = [
            _("You set down the **{name}** egg beneath a cypress tree; even the breeze seems to sigh, as if Hermes himself laments its destined solitude."),
            _("The **{name}** egg cools in your absence, and a single drop of dew forms upon it—a silent tear offered by Gaia for what is lost."),
            _("You leave the **{name}** egg upon the earth, and Apollo’s light fades from its shell, its pulse now bound to the will of the Fates."),
            _("As you walk away, the **{name}** egg’s soft glow dwindles, and somewhere unseen, the Moirai cut a thread that will never reach the dawn."),
            _("The **{name}** egg rests quietly among wildflowers, watched by indifferent gods who have already written its story in the stars."),
        ]
        egg_stories_extra = [
            _("The **{name}** egg trembles as you set it upon the earth, and even the wind grows still—as if the gods themselves mourn what is to come."),
            _("You leave the **{name}** egg at the foot of a forgotten temple; its faint glow fades as Apollo turns his face away."),
            _("The **{name}** egg cracks softly, releasing a sigh that drifts to Olympus—an unanswered prayer to the gods of mercy."),
            _("As you walk away, the **{name}** egg’s warmth bleeds into the soil, and Gaia reclaims what might have been a blessed creation."),
            _("The **{name}** egg lies silent beneath the stars, its fate sealed by mortal hands while the Moirai weave a thread that ends too soon."),
        ]
        egg_stories_extra_extra = [
            _("The **{name}** egg lies where you left it, trembling beneath a sky heavy with prophecy—its faint pulse silenced as the Moirai cut its thread too soon."),
            _("You abandon the **{name}** egg upon ancient stone; Apollo’s light fades from it, and only the whispers of forgotten gods remain to mourn."),
            _("As night falls, the **{name}** egg glows like a dying star—its spirit already descending to Hades before it has ever known daylight."),
            _("The **{name}** egg cracks in sorrow, and from within, a voice calls to you across the veil, begging the gods for mercy they will not grant."),
            _("Rain falls upon the **{name}** egg as you turn away; Gaia reclaims what might have lived, folding it gently back into the eternal silence."),
        ]
        egg_stories_darkest = [
            _("The **{name}** egg is left beneath a sky heavy with omens; as thunder rolls, even Zeus averts his gaze from the fate sealed within its cracking shell."),
            _("You abandon the **{name}** egg at the edge of the sea—Poseidon’s waves claim it, and the creature within is sung to sleep by the mourning of the nymphs."),
            _("The **{name}** egg cools upon the altar of forgotten promises; the Fates cut its fragile thread before life can defy destiny."),
            _("Without your care, the **{name}** egg turns to stone, a relic of hubris watched over by silent gods who know the price of neglect."),
            _("As dawn breaks, the **{name}** egg releases a final breath—a whisper carried by Hermes to the underworld, where even Hades shows pity."),
        ]

        try:
            pet_all = pet_stories_standard * 9 + pet_stories_extra * 6 + pet_stories_extra_extra * 5 + pet_stories_darkest * 4
            egg_all = egg_stories_standard * 9 + egg_stories_extra * 6 + egg_stories_extra_extra * 5 + egg_stories_darkest * 4

            async with self.bot.pool.acquire() as conn:
                pet = await conn.fetchrow("SELECT * FROM monster_pets WHERE user_id = $1 AND id = $2;", ctx.author.id, id)
                egg = await conn.fetchrow("SELECT * FROM monster_eggs WHERE user_id = $1 AND id = $2;", ctx.author.id, id)
                if not pet and not egg:
                    await ctx.send(_("❌ No pet or egg with ID `{id}` found in your collection.").format(id=id))
                    return

                item_name = pet["name"] if pet else egg["egg_type"]
                story = random.choice(pet_all if pet else egg_all)

                confirmation_message = await ctx.send(
                    _("⚠️ Are you sure you want to release your **{item_name}**? This action cannot be undone.").format(item_name=item_name)
                )

                confirm_view = discord.ui.View()

                async def confirm_callback(interaction: discord.Interaction):
                    try:
                        if interaction.user != ctx.author:
                            await interaction.response.send_message(_("❌ You are not authorized to respond to this release."), ephemeral=True)
                            return
                        await interaction.response.defer()
                        async with self.bot.pool.acquire() as c2:
                            pet2 = await c2.fetchrow("SELECT 1 FROM monster_pets WHERE user_id = $1 AND id = $2;", ctx.author.id, id)
                            egg2 = await c2.fetchrow("SELECT 1 FROM monster_eggs WHERE user_id = $1 AND id = $2;", ctx.author.id, id)
                            if not pet2 and not egg2:
                                await ctx.send(_("❌ No pet or egg with ID `{id}` found in your collection.").format(id=id))
                                return
                            if pet2:
                                await c2.execute("DELETE FROM monster_pets WHERE id = $1 AND user_id = $2;", id, ctx.author.id)
                            else:
                                await c2.execute("DELETE FROM monster_eggs WHERE id = $1 AND user_id = $2;", id, ctx.author.id)
                        await interaction.followup.send(story.format(name=item_name))
                        for child in confirm_view.children:
                            child.disabled = True
                        await confirmation_message.edit(view=confirm_view)
                    except Exception as e:
                        print(e)

                async def cancel_callback(interaction: discord.Interaction):
                    if interaction.user != ctx.author:
                        await interaction.response.send_message(_("❌ You are not authorized to cancel this release."), ephemeral=True)
                        return
                    await interaction.response.send_message(_("✅ Release action cancelled."), ephemeral=True)
                    for child in confirm_view.children:
                        child.disabled = True
                    await confirmation_message.edit(view=confirm_view)

                btn_confirm = discord.ui.Button(label=_("Confirm Release"), style=discord.ButtonStyle.red, emoji="💔")
                btn_cancel = discord.ui.Button(label=_("Cancel"), style=discord.ButtonStyle.gray, emoji="❌")
                btn_confirm.callback = confirm_callback
                btn_cancel.callback = cancel_callback
                confirm_view.add_item(btn_confirm)
                confirm_view.add_item(btn_cancel)

                await confirmation_message.edit(view=confirm_view)
        except Exception as e:
            await ctx.send(str(e))

    @pets.command(brief=_("Learn how to use the pet system"))
    async def help(self, ctx):
        try:
            embed = discord.Embed(
                title=_("Enhanced Pet System Guide"),
                description=_("Learn how to care for, train, and develop your pets with the new trust, leveling, and skill tree system!"),
                color=discord.Color.green(),
            )
            embed.add_field(
                name=_("🐾 Getting Started"),
                value=_(
                    "**How to Get a Pet:**\n"
                    "Find **monster eggs** as rare rewards during PVE battles. Use `$pets eggs` to check hatching progress!\n\n"
                    "**Basic Commands:**\n"
                    "• `$pets` - View active pets (inventory)\n"
                    "• `$pets eggs` - Check unhatched eggs and timers"
                ),
                inline=False,
            )
            embed.add_field(
                name=_("⚔️ Battle & Management"),
                value=_(
                    "• `$pets equip <id>` - Equip pet for battles (Young stage+ only)\n"
                    "• `$pets unequip` - Unequip current battle pet\n"
                    "• `$pets rename <id> <name>` - Rename your pet\n"
                    "• `$pets release <id>` - Release pet permanently (⚠️ irreversible)\n"
                    "• `$pets house` - View pets stored in your Oikos\n"
                    "• `$pets store <id>` / `$pets unstore <id>` - Move pets in/out of Oikos\n"
                    "• `$pets housename`, `$pets houseimage`, `$pets housemotto`, `$pets housetheme` - Customize your Oikos"
                ),
                inline=False,
            )
            embed.add_field(
                name=_("🌳 Skills & Training"),
                value=_(
                    "• `$pets train [pet_id]` - Train a pet for XP, trust, and level progress\n"
                    "• `$pets skillshelp` - View the pet skills and leveling help\n"
                    "• `$pets skilllist [element]` - View all elements or one element's skill tree\n"
                    "• `$pets skills [pet_id]` - View a pet's learned skills and progress\n"
                    "• `$pets skillinfo [pet_id] <skill name>` - View detailed skill info\n"
                    "• `$pets learn [pet_id] <skill name>` - Learn a skill for a pet\n"
                    "• `$pets unlearn [pet_id] <skill name>` - Unlearn a skill and refund SP"
                ),
                inline=False,
            )
            embed.add_field(
                name=_("💰 Trading Commands"),
                value=_(
                    "• `$pets trade <type> <your_id> <type> <their_id>` - Trade pets with others\n"
                    "• `$pets sell <type> <id> <@user> <amount>` - Sell pets for money\n"
                    "*All trades/sales require both parties to accept within 2 minutes*"
                ),
                inline=False,
            )
            await ctx.send(embed=embed)
        except Exception as e:
            await ctx.send(f"❌ Error: {e}")

# =====================================================================================

async def setup(bot):
    await bot.add_cog(Pets(bot))
