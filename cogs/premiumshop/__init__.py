


"""
The IdleRPG Discord Bot
Copyright (C) 2018-2021 Diniboy and Gelbpunkt
Copyright (C) 2023-2024 Lunar (PrototypeX37)
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



import contextlib
import datetime as _dt
import logging
import random
from traceback import format_exc
from typing import Dict, Any, Optional, List

import discord
from discord.ext import commands

from utils.checks import has_char, is_gm  # noqa: F401
from utils.i18n import _, locale_doc
from classes.converters import IntGreaterThan
from cogs.shard_communication import user_on_cooldown as user_cooldown

log = logging.getLogger(__name__)

# =========================
# Constants / Config
# =========================

THUMBNAIL_URL = "https://i.imgur.com/2QygQtD.png"

# Single source of truth (names+prices)
ITEMS: Dict[str, Dict[str, Any]] = {
    # canonical short keys
    "petage":     {"name": "Pet Age Potion",          "price": 200,  "short": "petage"},
    "petspeed":   {"name": "Pet Speed Growth Potion", "price": 300,  "short": "petspeed"},
    "petxp":      {"name": "Pet XP Potion",           "price": 1200, "short": "petxp"},
    "legendary":  {"name": "Legendary Crate",         "price": 500,  "short": "legendary"},
    "divine":     {"name": "Divine Crate",            "price": 1000, "short": "divine"},
    "materials":  {"name": "Materials Crate",         "price": 450,  "short": "materials"},
    "pethouse":   {"name": "Pet Oikos (Base House)",  "price": 6666, "short": "pethouse"},
    "petext":     {"name": "Pet Oikos Extension",     "price": 2500, "short": "petext"},
    # long aliases
    "pet age potion":           {"name": "Pet Age Potion",          "price": 200,  "short": "petage"},
    "pet speed growth potion":  {"name": "Pet Speed Growth Potion", "price": 300,  "short": "petspeed"},
    "pet xp potion":            {"name": "Pet XP Potion",           "price": 1200, "short": "petxp"},
    "legendary crate":          {"name": "Legendary Crate",         "price": 500,  "short": "legendary"},
    "divine crate":             {"name": "Divine Crate",            "price": 1000, "short": "divine"},
    "materials crate":          {"name": "Materials Crate",         "price": 450,  "short": "materials"},
    "pet house":                {"name": "Pet Oikos (Base House)",  "price": 6666, "short": "pethouse"},
    "pet oikos":                {"name": "Pet Oikos (Base House)",  "price": 6666, "short": "pethouse"},
    "pet oikos house":          {"name": "Pet Oikos (Base House)",  "price": 6666, "short": "pethouse"},
    "house extension":          {"name": "Pet Oikos Extension",     "price": 2500, "short": "petext"},
    "pet extension":            {"name": "Pet Oikos Extension",     "price": 2500, "short": "petext"},
    "pet oikos extension":      {"name": "Pet Oikos Extension",     "price": 2500, "short": "petext"},
}

# DB type strings MUST match your schema
CONSUMABLE_DB_TYPES = {
    "petage":   "pet_age_potion",
    "petspeed": "pet_speed_growth_potion",
    "petxp":    "pet_xp_potion",
}

SHOP_ORDER = (
    "petage",
    "petspeed",
    "petxp",
    "legendary",
    "divine",
    "materials",
    "pethouse",
    "petext",
)

TIER_REWARDS = {1: 350, 2: 800, 3: 1600, 4: 3500}
REDEEM_COOLDOWN_SECONDS = 40 * 24 * 60 * 60  # 3,456,000


class PremiumShopSelect(discord.ui.Select):
    def __init__(self, view: "PremiumShopView"):
        self.shop_view = view
        options = []
        emoji_map = {
            "petage": "🧪",
            "petspeed": "⚡",
            "petxp": "🔮",
            "legendary": "📦",
            "divine": "✨",
            "materials": "🧰",
            "pethouse": "🏛️",
            "petext": "🧱",
        }
        for key in SHOP_ORDER:
            item = ITEMS[key]
            options.append(
                discord.SelectOption(
                    label=item["name"],
                    description=f"{item['price']} drachmas | {key}",
                    value=key,
                    emoji=emoji_map.get(key, "🛒"),
                )
            )
        super().__init__(
            placeholder="Choose a premium item...",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.shop_view.ctx.author.id:
            return await interaction.response.send_message("This is not your shop menu.", ephemeral=True)
        self.shop_view.selected = self.values[0]
        await self.shop_view.refresh()
        await interaction.response.edit_message(embed=self.shop_view.embed(), view=self.shop_view)


class PremiumShopView(discord.ui.View):
    def __init__(self, cog: "PremiumShop", ctx: commands.Context, dragoncoins: int):
        super().__init__(timeout=90)
        self.cog = cog
        self.ctx = ctx
        self.dragoncoins = dragoncoins
        self.selected = SHOP_ORDER[0]
        self.add_item(PremiumShopSelect(self))

    async def refresh(self):
        self.dragoncoins = await self.cog._safe_fetch_dragoncoins(self.ctx.author.id)

    def embed(self) -> discord.Embed:
        embed = self.cog.shop_embed(self.ctx, self.dragoncoins)
        item = ITEMS[self.selected]
        embed.add_field(
            name="Selected",
            value=(
                f"**{item['name']}**\n"
                f"Price: **{item['price']}** <:drachma:1411644930216038461>\n"
                f"Key: `{self.selected}`"
            ),
            inline=False,
        )
        return embed

    async def refresh_message(self):
        await self.refresh()
        try:
            await self.message.edit(embed=self.embed(), view=self)  # type: ignore[attr-defined]
        except (discord.NotFound, discord.Forbidden, discord.HTTPException, AttributeError):
            pass

    async def buy(self, interaction: discord.Interaction, amount: int):
        if interaction.user.id != self.ctx.author.id:
            return await interaction.response.send_message("This is not your shop menu.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        item = ITEMS[self.selected]
        if self.selected == "pethouse":
            amount = 1
        pool = getattr(self.cog.bot, "pool", None)
        if not pool:
            await interaction.followup.send("The shop is currently unavailable. Please try again later.", ephemeral=True)
            return
        profile_exists = await pool.fetchval('SELECT 1 FROM profile WHERE "user" = $1;', self.ctx.author.id)
        if not profile_exists:
            await interaction.followup.send("You need a character profile first.", ephemeral=True)
            return
        try:
            await self.cog.dragoncoinbuy.callback(self.cog, self.ctx, self.selected, amount=amount)
        except Exception as exc:
            log.exception("Premium shop button purchase failed")
            await interaction.followup.send(f"Purchase failed: {exc}", ephemeral=True)
            return
        await self.refresh_message()
        await interaction.followup.send(
            f"Shop refreshed after selecting **{amount}x {item['name']}**.",
            ephemeral=True,
        )

    @discord.ui.button(label="Buy x1", style=discord.ButtonStyle.green, row=1)
    async def buy_one(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self.buy(interaction, 1)

    @discord.ui.button(label="Buy x5", style=discord.ButtonStyle.primary, row=1)
    async def buy_five(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self.buy(interaction, 5)

    @discord.ui.button(label="Buy x10", style=discord.ButtonStyle.primary, row=1)
    async def buy_ten(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self.buy(interaction, 10)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.red, row=1)
    async def close(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            return await interaction.response.send_message("This is not your shop menu.", ephemeral=True)
        await interaction.message.delete()
        self.stop()

# =========================
# Cog
# =========================

class PremiumShop(commands.Cog):
    """Premium shop, purchasing, and pet consumables."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ---------- utils ----------

    @staticmethod
    def _now_utc() -> _dt.datetime:
        return _dt.datetime.now(_dt.timezone.utc)

    @staticmethod
    def _ensure_aware(dt: Optional[_dt.datetime]) -> Optional[_dt.datetime]:
        if dt is None:
            return None
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=_dt.timezone.utc)

    async def _safe_fetch_dragoncoins(self, user_id: int) -> int:
        pool = getattr(self.bot, "pool", None)
        if not pool:
            return 0
        try:
            async with pool.acquire() as conn:
                return await conn.fetchval(
                    'SELECT COALESCE(dragoncoins, 0) FROM profile WHERE "user" = $1;',
                    user_id,
                ) or 0
        except Exception:
            log.exception("Failed to fetch dragoncoins for %s", user_id)
            return 0

    async def _ensure_pet_house_columns(self, conn) -> None:
        await conn.execute(
            """
            ALTER TABLE profile
            ADD COLUMN IF NOT EXISTS pet_house_slots INTEGER NOT NULL DEFAULT 0
            """
        )

    # ---------- shop embed (auto-chunk fields) ----------

    def _shop_item_blocks(self) -> List[str]:
        # concise lines to keep field sizes small
        return [
            _("<:ageup:1405960113151541459> **Pet Age Potion** — 200 (`petage`)\n"
              "Instantly age your pet to the next growth stage"),
            _("<:finalpotion:1405960417666142419> **Pet Speed Growth Potion** — 300 (`petspeed`)\n"
              "Doubles growth speed for a specific pet"),
            _("<:splicepotion:1405960928301809764> **Pet XP Potion** — 1200 (`petxp`)\n"
              "Gives a pet permanent x2 XP multiplier"),
            _("<:c_legendary:1405959222536966256> **Legendary Crate** — 500 (`legendary`)\n"
              "Items with stats ~41–80; rarely drachmas"),
            _("<:f_divine:1405959418163630211> **Divine Crate** — 1000 (`divine`)\n"
              "Items with stats ~47–100"),
            _("<:c_mats:1405959241898004480> **Materials Crate** — 450 (`materials`)\n"
              "3–10 random crafting materials"),
            _("🏛️ **Pet Oikos (Base House)** — 6666 (`pethouse`)\n"
              "Unlocks pet storage with **15** house slots"),
            _("🧱 **Pet Oikos Extension** — 2500 (`petext`)\n"
              "Adds **+5** house slots per purchase"),
            _("*More items coming soon…*"),
        ]

    @staticmethod
    def _chunk_for_field(blocks: List[str], limit: int = 1024) -> List[str]:
        chunks: List[str] = []
        cur = ""
        for b in blocks:
            add = b + "\n\n"
            if len(cur) + len(add) > limit:
                if cur:
                    chunks.append(cur.rstrip())
                    cur = ""
                if len(add) > limit:
                    # hard-split long single block (unlikely)
                    chunks.append((b[: limit - 1] + "…"))
                else:
                    cur = add
            else:
                cur += add
        if cur:
            chunks.append(cur.rstrip())
        return chunks

    def shop_embed(self, ctx: commands.Context, dragoncoins: int) -> discord.Embed:
        description = _(
            "Welcome to the Drachma Shop!\n\n"
            "**Buy:** choose an item below, or use `{prefix}drachmabuy <item> [amount]`\n"
            "**Currency:** <:drachma:1411644930216038461> Drachmas\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ).format(prefix=ctx.clean_prefix)

        blocks = self._shop_item_blocks()
        fields = self._chunk_for_field(blocks, limit=1024)

        embed = discord.Embed(
            title=_("Drachma Shop"),
            description=description,
            colour=discord.Colour.purple(),
        )
        embed.add_field(
            name=_("Your Drachmas"),
            value=_("**{coins}** <:drachma:1411644930216038461>").format(coins=dragoncoins),
            inline=False,
        )
        if len(fields) == 1:
            embed.add_field(name=_("Available Items"), value=fields[0], inline=False)
        else:
            for i, part in enumerate(fields, start=1):
                embed.add_field(
                    name=_("Available Items ({i}/{n})").format(i=i, n=len(fields)),
                    value=part,
                    inline=False,
                )
        embed.set_footer(text=_("Premium Shop • Use {prefix}dcbuy to purchase").format(prefix=ctx.clean_prefix))
        embed.set_thumbnail(url=THUMBNAIL_URL)
        return embed

    async def _send_shop_embed(self, ctx: commands.Context, dragoncoins: int) -> None:
        embed = self.shop_embed(ctx, dragoncoins)
        view = PremiumShopView(self, ctx, dragoncoins)
        try:
            message = await ctx.send(embed=view.embed(), view=view)
            view.message = message
        except discord.Forbidden:
            # plaintext fallback if embeds blocked
            blocks = self._shop_item_blocks()
            lines = [
                _("**Drachma Shop**"),
                _("Your Drachmas: **{coins}** <:drachma:1411644930216038461>").format(coins=dragoncoins),
                "",
                _("Buy with `{prefix}drachmabuy <item> [amount]`").format(prefix=ctx.clean_prefix),
                "",
                *blocks,
            ]
            await ctx.send("\n\n".join(lines))

    # ---------- diagnostics ----------

    @commands.command(name="pingdcshop")
    async def ping_dcshop(self, ctx: commands.Context):
        """Minimal sanity check to verify cog/command registration."""
        await ctx.send("✅ premium_shop loaded. Try `$dcshop`.")

    @commands.command(name="dcshopdebug")
    async def dcshop_debug(self, ctx: commands.Context):
        """Run dcshop with full error surfacing."""
        await ctx.send("🧪 Running dcshop debug…")
        try:
            coins = await self._safe_fetch_dragoncoins(ctx.author.id)
            await self._send_shop_embed(ctx, coins)
        except Exception:
            tb = format_exc()
            await ctx.send(f"💥 Exception in dcshop:\n```py\n{tb[-1900:]}\n```")

    # ---------- shop commands ----------

    @commands.command(name="drachmashop", aliases=["dcshop"], brief=_("Show the premium shop"), hidden=True)
    @locale_doc
    async def dragoncoinshop(self, ctx: commands.Context):
        _(
            """Show the premium shop. For a detailed explanation of premium items, check `{prefix}help drachmashop`."""
        )
        try:
            coins = await self._safe_fetch_dragoncoins(ctx.author.id)
            await self._send_shop_embed(ctx, coins)
            with contextlib.suppress(discord.HTTPException):
                await ctx.message.add_reaction("🟣")  # breadcrumb so you know it ran
        except discord.Forbidden:
            with contextlib.suppress(Exception):
                await ctx.send("I lack permission to send messages/embeds here.")
        except Exception:
            tb = format_exc()
            with contextlib.suppress(Exception):
                await ctx.send(f"💥 Unexpected error in dcshop:\n```py\n{tb[-1900:]}\n```")

    @has_char()
    @commands.command(aliases=["dcbuy", "drachmabuy"], brief=_("Buy premium items"), hidden=True)
    @locale_doc
    async def dragoncoinbuy(self, ctx: commands.Context, item: str, *, amount: IntGreaterThan(0) = 1):
        _(
            """`[amount]` - The amount of items to buy; defaults to 1
            `<item>` - The premium item to buy

            Buy one or more premium items from the shop."""
        )

        normalized_item = " ".join(item.lower().split())
        if normalized_item not in ITEMS:
            return await ctx.send(
                _(
                    "Invalid item. Available items: "
                    "petage/pet age potion, petspeed/pet speed growth potion, "
                    "petxp/pet xp potion, "
                    "legendary/legendary crate, divine/divine crate, materials/materials crate, "
                    "pethouse/pet house, petext/pet extension."
                )
            )

        short_key = ITEMS[normalized_item]["short"]
        item_info = ITEMS[short_key]
        price = item_info["price"]
        total_cost = price * amount

        pool = getattr(self.bot, "pool", None)
        if not pool:
            return await ctx.send(_("The shop is currently unavailable. Please try again later."))

        post_purchase_note: Optional[str] = None

        async with pool.acquire() as conn:
            async with conn.transaction():
                if short_key in {"pethouse", "petext"}:
                    await self._ensure_pet_house_columns(conn)
                    house_slots = await conn.fetchval(
                        'SELECT COALESCE(pet_house_slots, 0) FROM profile WHERE "user" = $1;',
                        ctx.author.id,
                    ) or 0

                    if short_key == "pethouse":
                        if amount != 1:
                            return await ctx.send(_("You can only buy one base Pet Oikos at a time."))
                        if house_slots >= 15:
                            return await ctx.send(_("You already own a Pet Oikos. Use `dcbuy petext` for more slots."))
                    elif house_slots < 15:
                        return await ctx.send(_("You need to buy the base Pet Oikos first (`dcbuy pethouse`)."))

                dragoncoins = await conn.fetchval(
                    'SELECT COALESCE(dragoncoins, 0) FROM profile WHERE "user" = $1;',
                    ctx.author.id,
                )
                if dragoncoins < total_cost:
                    return await ctx.send(
                        _("You don't have enough Dragon Coins. You need {cost} but have {current}.").format(
                            cost=total_cost, current=dragoncoins
                        )
                    )

                # Deduct coins
                await conn.execute(
                    'UPDATE profile SET dragoncoins = COALESCE(dragoncoins, 0) - $1 WHERE "user" = $2;',
                    total_cost,
                    ctx.author.id,
                )

                # Consumables
                if short_key in CONSUMABLE_DB_TYPES:
                    ctype = CONSUMABLE_DB_TYPES[short_key]
                    existing = await conn.fetchrow(
                        'SELECT id, quantity FROM user_consumables WHERE user_id = $1 AND consumable_type = $2;',
                        ctx.author.id,
                        ctype,
                    )
                    if existing:
                        await conn.execute(
                            'UPDATE user_consumables SET quantity = quantity + $1 WHERE id = $2;',
                            amount,
                            existing["id"],
                        )
                    else:
                        await conn.execute(
                            'INSERT INTO user_consumables (user_id, consumable_type, quantity) VALUES ($1, $2, $3);',
                            ctx.author.id,
                            ctype,
                            amount,
                        )

                # Crates
                elif short_key in {"legendary", "divine", "materials"}:
                    crate_column = f'crates_{short_key}'
                    row = await conn.fetchrow(
                        f'''
                        UPDATE profile
                        SET "{crate_column}" = COALESCE("{crate_column}", 0) + $1
                        WHERE "user" = $2
                        RETURNING COALESCE("{crate_column}", 0) AS total;
                        ''',
                        amount,
                        ctx.author.id,
                    )
                    if not row:
                        await conn.execute(
                            f'''
                            INSERT INTO profile ("user", dragoncoins, "{crate_column}")
                            VALUES ($1, 0, $2)
                            ON CONFLICT ("user") DO UPDATE
                            SET "{crate_column}" = COALESCE(profile."{crate_column}", 0) + EXCLUDED."{crate_column}";
                            ''',
                            ctx.author.id,
                            amount,
                        )
                        crates_total = amount
                    else:
                        crates_total = row["total"]

                    await ctx.send(
                        _("✅ You now own **{n} {crate} Crates** (+{added} just bought).").format(
                            n=crates_total, crate=short_key.title(), added=amount
                        )
                    )
                elif short_key == "pethouse":
                    await conn.execute(
                        'UPDATE profile SET pet_house_slots = 15 WHERE "user" = $1;',
                        ctx.author.id,
                    )
                    post_purchase_note = _(
                        "🏛️ You now own a **Pet Oikos** with **15** storage spots. "
                        "Use `{prefix}pets store <pet_id>` to move pets in."
                    ).format(prefix=ctx.clean_prefix)
                elif short_key == "petext":
                    added_slots = 5 * amount
                    new_total = await conn.fetchval(
                        """
                        UPDATE profile
                        SET pet_house_slots = COALESCE(pet_house_slots, 0) + $1
                        WHERE "user" = $2
                        RETURNING pet_house_slots
                        """,
                        added_slots,
                        ctx.author.id,
                    )
                    post_purchase_note = _(
                        "🧱 Pet Oikos expanded by **{added}** spots. "
                        "New house capacity: **{total}**."
                    ).format(added=added_slots, total=new_total or 0)

        if post_purchase_note:
            await ctx.send(post_purchase_note)

        await ctx.send(
            _("Successfully purchased **{amount}x {item_name}** for **{cost} <:drachma:1411644930216038461>**!").format(
                amount=amount, item_name=item_info["name"], cost=total_cost
            )
        )

    # ---------- pet consumables ----------

    async def consume_pet_age_potion(self, ctx: commands.Context, pet_id: int):
        pool = getattr(self.bot, "pool", None)
        if not pool:
            return False, _("Service unavailable. Try again later.")

        async with pool.acquire() as conn:
            potion = await conn.fetchrow(
                'SELECT id, quantity FROM user_consumables WHERE user_id = $1 AND consumable_type = $2;',
                ctx.author.id,
                "pet_age_potion",
            )
            if not potion or potion["quantity"] < 1:
                return False, _("You don't have any Pet Age Potions.")

            pet = await conn.fetchrow(
                "SELECT * FROM monster_pets WHERE id = $1 AND user_id = $2",
                pet_id,
                ctx.author.id,
            )
            if not pet:
                return False, _("Pet not found or doesn't belong to you.")
            if pet["growth_stage"] == "adult":
                return False, _("This pet is already fully grown!")

            growth_stages = {
                1: {"stage": "baby",     "growth_time": 2, "stat_multiplier": 0.25},
                2: {"stage": "juvenile", "growth_time": 2, "stat_multiplier": 0.50},
                3: {"stage": "young",    "growth_time": 1, "stat_multiplier": 0.75},
                4: {"stage": "adult",    "growth_time": None, "stat_multiplier": 1.00},
            }

            current_index = pet["growth_index"] or 1
            if current_index not in growth_stages:
                return False, _("Pet has an invalid growth stage.")
            next_stage_index = current_index + 1
            if next_stage_index not in growth_stages:
                return False, _("Pet cannot grow further.")

            stage_data = growth_stages[next_stage_index]
            old_mult = growth_stages[current_index]["stat_multiplier"]
            new_mult = stage_data["stat_multiplier"]
            ratio = new_mult / max(old_mult, 1e-9)

            new_hp = pet["hp"] * ratio
            new_attack = pet["attack"] * ratio
            new_defense = pet["defense"] * ratio

            if stage_data["growth_time"] is not None:
                new_growth_time = self._now_utc() + _dt.timedelta(days=stage_data["growth_time"])
                await conn.execute(
                    """
                    UPDATE monster_pets
                    SET growth_stage=$1, growth_time=$2, hp=$3, attack=$4, defense=$5, growth_index=$6
                    WHERE id=$7
                    """,
                    stage_data["stage"], new_growth_time, new_hp, new_attack, new_defense, next_stage_index, pet_id
                )
            else:
                await conn.execute(
                    """
                    UPDATE monster_pets
                    SET growth_stage=$1, growth_time=NULL, hp=$2, attack=$3, defense=$4, growth_index=$5
                    WHERE id=$6
                    """,
                    stage_data["stage"], new_hp, new_attack, new_defense, next_stage_index, pet_id
                )

            await conn.execute("UPDATE user_consumables SET quantity = quantity - 1 WHERE id = $1;", potion["id"])

        msg = (
            f"**{pet['name']}** has grown into a **{stage_data['stage'].capitalize()}**!\n\n"
            f"**New Stats:**\n"
            f"• HP: {round(new_hp)}\n"
            f"• Attack: {round(new_attack)}\n"
            f"• Defense: {round(new_defense)}"
        )
        return True, msg

    async def consume_pet_xp_potion(self, ctx: commands.Context, pet_id: int):
        pool = getattr(self.bot, "pool", None)
        if not pool:
            return False, _("Service unavailable. Try again later.")

        async with pool.acquire() as conn:
            potion = await conn.fetchrow(
                'SELECT id, quantity FROM user_consumables WHERE user_id = $1 AND consumable_type = $2;',
                ctx.author.id, "pet_xp_potion"
            )
            if not potion or potion["quantity"] < 1:
                return False, _("You don't have any Pet XP Potions.")

            pet = await conn.fetchrow(
                "SELECT * FROM monster_pets WHERE id = $1 AND user_id = $2",
                pet_id, ctx.author.id
            )
            if not pet:
                return False, _("Pet not found or doesn't belong to you.")

            xp_mult = pet["xp_multiplier"] if "xp_multiplier" in pet else 1.0
            if (xp_mult or 1.0) > 1.0:
                return False, _("This pet already has an XP multiplier active!")

            await conn.execute("UPDATE user_consumables SET quantity = quantity - 1 WHERE id = $1;", potion["id"])
            await conn.execute("UPDATE monster_pets SET xp_multiplier = 2.0 WHERE id = $1;", pet_id)

        msg = (
            f"🔮 **Pet XP Potion consumed!**\n\n"
            f"**{pet['name']}** now has **x2 XP permanently!**\n"
            f"This pet will gain double experience from all activities forever!"
        )
        return True, msg

    async def consume_pet_speed_growth_potion(self, ctx: commands.Context, pet_id: int):
        """
        Pet Speed Growth Potion:
        - If not already at max speed, consume 1 potion
        - Halve remaining growth_time immediately
        - Set a persistent multiplier so future recomputes stay fast
        """
        pool = getattr(self.bot, "pool", None)
        if not pool:
            return False, _("Service unavailable. Try again later.")

        async with pool.acquire() as conn:
            # 1) Check inventory
            potion = await conn.fetchrow(
                'SELECT id, quantity FROM user_consumables WHERE user_id = $1 AND consumable_type = $2;',
                ctx.author.id, "pet_speed_growth_potion"
            )
            if not potion or potion["quantity"] < 1:
                return False, _("You don't have any Pet Speed Growth Potions.")

            # 2) Load the pet with current multiplier
            pet = await conn.fetchrow(
                'SELECT id, user_id, name, growth_stage, growth_time, '
                'COALESCE(growth_speed_multiplier, 1.0) AS growth_speed_multiplier '
                'FROM monster_pets WHERE id = $1 AND user_id = $2;',
                pet_id, ctx.author.id
            )
            if not pet:
                return False, _("Pet not found or doesn't belong to you.")
            if pet["growth_stage"] == "adult":
                return False, _("This pet is already an adult and cannot grow further.")

            current_mult = pet["growth_speed_multiplier"] or 1.0
            # 3) Prevent stacking past x2 BEFORE spending the item
            if current_mult >= 2.0:
                return False, _("This pet already has maximum growth speed active.")

            # 4) Spend one potion
            await conn.execute(
                'UPDATE user_consumables SET quantity = quantity - 1 WHERE id = $1;',
                potion["id"]
            )

            # 5) Instant effect: halve remaining time, if any
            now = self._now_utc()
            gtime = self._ensure_aware(pet["growth_time"])
            if gtime is not None:
                remaining = gtime - now
                if remaining.total_seconds() > 0:
                    new_growth_time = now + (remaining / 2)
                    await conn.execute(
                        'UPDATE monster_pets SET growth_time = $1 WHERE id = $2;',
                        new_growth_time, pet_id
                    )

            # 6) Persistent effect: make the scheduler honor x2 going forward
            await conn.execute(
                'UPDATE monster_pets SET growth_speed_multiplier = 2.0, speed_growth_active = TRUE WHERE id = $1;',
                pet_id
            )

        msg = (
            f"<:finalpotion:1398721503268438169> **Pet Speed Growth Potion consumed!**\n\n"
            f"**{pet['name']}** will now grow **2× faster** starting immediately.\n"
            f"Remaining growth time has been cut in half, and the speed boost will persist."
        )
        return True, msg


    # ---------- Redeem / Tier info ----------

    @user_cooldown(REDEEM_COOLDOWN_SECONDS)
    @has_char()
    @commands.command(hidden=True, brief=_("Redeem Drachmas based on your tier"))
    @locale_doc
    async def redeemdc(self, ctx: commands.Context):
        _(
            """Redeem dragon coins based on your tier level.

            **Tier Rewards:**
            • Tier 1: 350 <:drachma:1411644930216038461>
            • Tier 2: 800 <:drachma:1411644930216038461>
            • Tier 3: 1600 <:drachma:1411644930216038461>
            • Tier 4: 3500 <:drachma:1411644930216038461>
            """
        )
        pool = getattr(self.bot, "pool", None)
        if not pool:
            return await ctx.send(_("Service unavailable. Try again later."))

        async with pool.acquire() as conn:
            profile = await conn.fetchrow(
                'SELECT tier, COALESCE(dragoncoins, 0) AS dragoncoins FROM profile WHERE "user" = $1;',
                ctx.author.id,
            )
            if not profile:
                return await ctx.send(_("You don't have a character profile."))

            tier = profile["tier"] or 0
            current = profile["dragoncoins"] or 0
            if tier not in TIER_REWARDS:
                return await ctx.send(_("You need to be at least Tier 1 to redeem Drachmas."))

            add = TIER_REWARDS[tier]
            new_total = current + add

            await conn.execute('UPDATE profile SET dragoncoins = $1 WHERE "user" = $2;', new_total, ctx.author.id)

        embed = discord.Embed(
            title=_("🎉 Drachmas Redeemed!"),
            description=_(
                "You have successfully redeemed your tier rewards!\n\n"
                "**Tier {tier} Reward:** +{coins} <:drachma:1411644930216038461>\n"
                "**Previous Balance:** {previous} <:drachma:1411644930216038461>\n"
                "**New Balance:** {new} <:drachma:1411644930216038461>"
            ).format(tier=tier, coins=add, previous=current, new=new_total),
            colour=discord.Colour.gold(),
            timestamp=self._now_utc(),
        )
        embed.set_footer(text=_("Use {prefix}drachmashop to view the shop").format(prefix=ctx.clean_prefix))
        await ctx.send(embed=embed)

    @has_char()
    @commands.command(hidden=True, brief=_("Show tier rewards information"))
    @locale_doc
    async def tierrewards(self, ctx: commands.Context):
        _(
            """Show information about tier rewards and drachma redemption.

            **Tier Rewards:**
            • Tier 1: 350 <:drachma:1411644930216038461>
            • Tier 2: 800 <:drachma:1411644930216038461>
            • Tier 3: 1600 <:drachma:1411644930216038461>
            • Tier 4: 3500 <:drachma:1411644930216038461>
            """
        )
        pool = getattr(self.bot, "pool", None)
        if not pool:
            return await ctx.send(_("Service unavailable. Try again later."))

        async with pool.acquire() as conn:
            tier = await conn.fetchval('SELECT tier FROM profile WHERE "user" = $1;', ctx.author.id)
        tier = tier or 0

        rewards_text = (
            "• **Tier 1:** 350 <:drachma:1411644930216038461>\n"
            "• **Tier 2:** 800 <:drachma:1411644930216038461>\n"
            "• **Tier 3:** 1600 <:drachma:1411644930216038461>\n"
            "• **Tier 4:** 3500 <:drachma:1411644930216038461>\n\n"
        )

        embed = discord.Embed(
            title=_("🏆 Tier Rewards Information"),
            description=_(
                "Redeem Drachmas based on your tier level!\n\n"
                "**Available Rewards:**\n{rewards}"
                "**Your Current Tier:** {tier}\n"
                "**Command:** `{prefix}redeemdc`\n"
                "**Cooldown:** 40 days (1 month + 5 days)"
            ).format(rewards=rewards_text, tier=tier, prefix=ctx.clean_prefix),
            colour=discord.Colour.gold(),
            timestamp=self._now_utc(),
        )
        embed.set_footer(text=_("Use {prefix}redeemdc to claim your rewards").format(prefix=ctx.clean_prefix))
        embed.set_thumbnail(url=THUMBNAIL_URL)
        await ctx.send(embed=embed)

    # ---------- Materials crate helper + command ----------

    async def open_materials_crate(self, ctx: commands.Context, consume_crate: bool = None):
        """
        Open one Materials Crate and grant 3-10 crafting materials.

        Notes:
        - When called from the generic `open` crate command, pass
          `consume_crate=True` so this helper decrements the materials crate.
        - When called from `openmaterials`, we also consume one crate here.
        """
        amulet_cog = self.bot.get_cog("AmuletCrafting")
        if not amulet_cog:
            return False, "AmuletCrafting system not available."

        pool = getattr(self.bot, "pool", None)
        if not pool:
            return False, "Service unavailable. Try again later."

        # Auto-detect if the caller already consumed the crate (e.g. crates.$open).
        if consume_crate is None:
            cmd_name = getattr(getattr(ctx, "command", None), "qualified_name", "") or ""
            consume_crate = cmd_name != "open"

        async with pool.acquire() as conn:
            async with conn.transaction():
                if consume_crate:
                    remaining_crates = await conn.fetchval(
                        """
                        UPDATE profile
                        SET crates_materials = COALESCE(crates_materials, 0) - 1
                        WHERE "user" = $1 AND COALESCE(crates_materials, 0) >= 1
                        RETURNING COALESCE(crates_materials, 0);
                        """,
                        ctx.author.id,
                    )
                    if remaining_crates is None:
                        return False, "You don't have any Materials Crates."
                else:
                    remaining_crates = await conn.fetchval(
                        """
                        SELECT COALESCE(crates_materials, 0)
                        FROM profile
                        WHERE "user" = $1
                        """,
                        ctx.author.id,
                    )
                    if remaining_crates is None:
                        return False, "Profile not found."

                material_count = random.randint(3, 10)
                materials_gained: List[str] = []

                for _ in range(material_count):
                    resource = amulet_cog.get_random_resource()
                    if not resource:
                        continue

                    existing = await conn.fetchrow(
                        """
                        SELECT amount
                        FROM crafting_resources
                        WHERE user_id = $1 AND resource_type = $2
                        """,
                        ctx.author.id,
                        resource,
                    )
                    if existing:
                        await conn.execute(
                            """
                            UPDATE crafting_resources
                            SET amount = amount + 1
                            WHERE user_id = $1 AND resource_type = $2
                            """,
                            ctx.author.id,
                            resource,
                        )
                    else:
                        await conn.execute(
                            """
                            INSERT INTO crafting_resources (user_id, resource_type, amount)
                            VALUES ($1, $2, 1)
                            """,
                            ctx.author.id,
                            resource,
                        )
                    materials_gained.append(resource.replace("_", " ").title())

                if not materials_gained:
                    if consume_crate:
                        await conn.execute(
                            'UPDATE profile SET crates_materials = COALESCE(crates_materials, 0) + 1 WHERE "user" = $1;',
                            ctx.author.id,
                        )
                    return False, "Something went wrong while generating materials. Your crate was not consumed."

        pretty = "• " + ", ".join(materials_gained)
        msg = (
            "<:c_mats:1405959241898004480> **Materials Crate opened!**\n\n"
            f"You found **{len(materials_gained)}** crafting materials:\n{pretty}\n\n"
            f"Remaining Materials Crates: **{int(remaining_crates)}**"
        )
        return True, msg

    @commands.command(name="openmaterials", hidden=True, brief=_("Open one Materials Crate"))
    @has_char()
    async def cmd_open_materials(self, ctx: commands.Context):
        ok, message = await self.open_materials_crate(ctx, consume_crate=True)
        await ctx.send(message if ok else f"⚠️ {message}")


async def setup(bot: commands.Bot):
    await bot.add_cog(PremiumShop(bot))
