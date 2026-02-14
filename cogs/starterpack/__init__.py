# cogs/starter_pack.py
from __future__ import annotations
"""
The Echoes Of Olympus Discord Bot
Copyright (C) 2026 Danaelis

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

import discord
from discord.ext import commands
from discord import ui

from collections import Counter
import random as pyrandom

from utils.checks import has_char
from utils.i18n import _, locale_doc
from utils import misc as rpgtools
from cogs.antiscript import next_reset_unix



# -------------------- UI: Tutorial paginator --------------------

class TutorialView(ui.View):
    def __init__(self, ctx: commands.Context, pages: list[discord.Embed]):
        super().__init__(timeout=180)
        self.ctx = ctx
        self.pages = pages
        self.index = 0

        self.prev_btn.disabled = True
        self.next_btn.disabled = len(pages) <= 1

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.ctx.author.id

    @ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: ui.Button):
        self.index = max(0, self.index - 1)
        self.prev_btn.disabled = self.index == 0
        self.next_btn.disabled = self.index >= len(self.pages) - 1
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: ui.Button):
        self.index = min(len(self.pages) - 1, self.index + 1)
        self.prev_btn.disabled = self.index == 0
        self.next_btn.disabled = self.index >= len(self.pages) - 1
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @ui.button(label="Close", style=discord.ButtonStyle.danger)
    async def close_btn(self, interaction: discord.Interaction, button: ui.Button):
        for c in self.children:
            c.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()


# -------------------- UI: Pet choice paginator --------------------

class PetChoiceView(ui.View):
    def __init__(self, ctx: commands.Context, pages: list[discord.Embed], pet_names: list[str], on_choose):
        super().__init__(timeout=180)
        self.ctx = ctx
        self.pages = pages
        self.pet_names = pet_names
        self.on_choose = on_choose
        self.index = 0

        self.prev_btn.disabled = True
        self.next_btn.disabled = len(pages) <= 1
        self._refresh_choose_label()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.ctx.author.id

    def _refresh_choose_label(self):
        self.choose_btn.label = f"Choose: {self.pet_names[self.index]}"

    @ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: ui.Button):
        self.index = max(0, self.index - 1)
        self.prev_btn.disabled = self.index == 0
        self.next_btn.disabled = self.index >= len(self.pages) - 1
        self._refresh_choose_label()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @ui.button(label="Choose", style=discord.ButtonStyle.success)
    async def choose_btn(self, interaction: discord.Interaction, button: ui.Button):
        pet = self.pet_names[self.index]
        # disable UI first to avoid double-clicks
        for c in self.children:
            c.disabled = True
        await interaction.response.edit_message(view=self)

        await self.on_choose(interaction, pet)
        self.stop()

    @ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: ui.Button):
        self.index = min(len(self.pages) - 1, self.index + 1)
        self.prev_btn.disabled = self.index == 0
        self.next_btn.disabled = self.index >= len(self.pages) - 1
        self._refresh_choose_label()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @ui.button(label="Close", style=discord.ButtonStyle.danger)
    async def close_btn(self, interaction: discord.Interaction, button: ui.Button):
        for c in self.children:
            c.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()


# -------------------- Cog --------------------

class StarterPack(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        # ---------------- CONFIG ----------------
        self.SUPPORT_CHANNEL_ID: int | None = None  # set your support channel id here

        self.STARTER_MONEY = 50_000

        # what "opening the pack" does (virtual opens)
        self.OPEN_DIVINE = 1
        self.OPEN_MYSTERY = 10
        self.OPEN_RARE = 15

        # crafting resources (must match your crafting cog ALL_RESOURCES keys)
        self.CRAFT_RESOURCES = {
            "dragon_scales": 15,
            "mystic_dust": 10,
            "harmony_stones": 8,
        }

        # pet choices pulled from splice_combinations
        self.PET_CHOICES = ["Drynthros", "Ophionyx", "Tynpyros"]

    # ---------------- Helpers ----------------

    def support_mention(self, ctx: commands.Context) -> str:
        return f"<#{self.SUPPORT_CHANNEL_ID}>" if self.SUPPORT_CHANNEL_ID else "#support"

    def support_url(self, ctx: commands.Context) -> str | None:
        if not self.SUPPORT_CHANNEL_ID or not ctx.guild:
            return None
        return f"https://discord.com/channels/{ctx.guild.id}/{self.SUPPORT_CHANNEL_ID}"

    async def get_level(self, ctx: commands.Context) -> int:
        return int(rpgtools.xptolevel(ctx.character_data["xp"]))

    async def packs_available(self, user_id: int) -> int:
        return await self.bot.pool.fetchval(
            'SELECT crates_starterpack FROM profile WHERE "user"=$1;',
            user_id,
        ) or 0

    # -------- Mystery roll logic (copied from Crates cog) --------

    def roll_mystery_counts(self, amount: int) -> dict[str, int]:
        crates = {
            "common": 0,
            "uncommon": 0,
            "rare": 0,
            "magic": 0,
            "legendary": 0,
            "fortune": 0,
            "divine": 0,
        }
        for _ in range(amount):
            rng = pyrandom.randint(0, 10000)
            if rng < 5:
                new_rarity = "divine"
            elif rng < 15:
                new_rarity = "fortune"
            elif rng < 30:
                new_rarity = "legendary"
            elif rng < 200:
                new_rarity = "magic"
            elif rng < 1000:
                new_rarity = "rare"
            elif rng < 2000:
                new_rarity = "uncommon"
            else:
                new_rarity = "common"
            crates[new_rarity] += 1
        return crates

    async def open_fortune_virtual(self, ctx: commands.Context, conn, amount: int) -> tuple[int, int]:
        gained_xp = 0
        gained_money = 0

        level = int(rpgtools.xptolevel(ctx.character_data["xp"]))

        for _ in range(amount):
            if pyrandom.randint(1, 100) <= 50:
                nurflevel = min(level, 50)
                xpvar = 2000 * level + 1500
                gained_xp += pyrandom.randint(1000 * nurflevel, xpvar)
            else:
                if pyrandom.randint(1, 100) <= 75:
                    val = pyrandom.randint(250000, 470000)
                else:
                    val = pyrandom.randint(470001, 850000)
                gained_money += round(val, -2)

        if gained_xp:
            await conn.execute('UPDATE profile SET "xp"="xp"+$1 WHERE "user"=$2;', gained_xp, ctx.author.id)
        if gained_money:
            await conn.execute('UPDATE profile SET "money"="money"+$1 WHERE "user"=$2;', gained_money, ctx.author.id)

        return gained_xp, gained_money

    async def open_item_crates_virtual(self, ctx: commands.Context, conn, rarity: str, amount: int) -> list[dict]:
        items = []
        for _ in range(amount):
            rand = pyrandom.randint(0, 9)

            if rarity == "common":
                if rand < 2:
                    minstat, maxstat = (20, 30)
                elif rand < 5:
                    minstat, maxstat = (10, 19)
                else:
                    minstat, maxstat = (1, 9)
            elif rarity == "uncommon":
                if rand < 2:
                    minstat, maxstat = (30, 35)
                elif rand < 5:
                    minstat, maxstat = (20, 29)
                else:
                    minstat, maxstat = (10, 19)
            elif rarity == "rare":
                if rand < 2:
                    minstat, maxstat = (35, 40)
                elif rand < 5:
                    minstat, maxstat = (30, 34)
                else:
                    minstat, maxstat = (20, 29)
            elif rarity == "magic":
                if rand < 2:
                    minstat, maxstat = (41, 55)
                elif rand < 5:
                    minstat, maxstat = (35, 40)
                else:
                    minstat, maxstat = (30, 34)
            elif rarity == "legendary":
                if rand < 2:
                    minstat, maxstat = (49, 80)
                elif rand < 5:
                    minstat, maxstat = (46, 60)
                else:
                    minstat, maxstat = (41, 45)
            elif rarity == "divine":
                r = pyrandom.randint(1, 100)
                if r <= 10:
                    minstat, maxstat = (77, 100)
                elif r <= 60:
                    minstat, maxstat = (57, 76)
                elif r <= 80:
                    minstat, maxstat = (52, 56)
                else:
                    minstat, maxstat = (47, 51)
            else:
                minstat, maxstat = (1, 9)

            item = await self.bot.create_random_item(
                minstat=minstat,
                maxstat=maxstat,
                minvalue=1,
                maxvalue=250,
                owner=ctx.author,
                conn=conn,
            )
            items.append(item)

            try:
                await self.bot.log_transaction(
                    ctx,
                    from_=1,
                    to=ctx.author.id,
                    subject="starterpack crate item",
                    data={"Rarity": rarity, "Name": item["name"], "Value": item["value"]},
                    conn=conn,
                )
            except Exception:
                pass

        return items

    # -------- crafting resources using YOUR helper --------

    async def grant_crafting_resources(self, user_id: int):
        # Find the cog that contains give_crafting_resource
        target_cog = None
        for cog in self.bot.cogs.values():
            if hasattr(cog, "give_crafting_resource"):
                target_cog = cog
                break

        if not target_cog:
            return

        for rtype, amt in self.CRAFT_RESOURCES.items():
            try:
                await target_cog.give_crafting_resource(user_id, rtype, amt)
            except Exception:
                pass

    # -------- pet grant (adult) from splice_combinations -> monster_pets --------

    async def grant_adult_pet(self, user_id: int, pet_name: str):
        async with self.bot.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT result_name, hp, attack, defense, element, url
                FROM splice_combinations
                WHERE LOWER(result_name) = LOWER($1)
                ORDER BY id DESC
                LIMIT 1;
                """,
                pet_name,
            )
            if not row:
                raise ValueError(f"Pet `{pet_name}` not found in splice_combinations.")

            name = str(row["result_name"]).strip()
            hp = int(row["hp"])
            attack = int(row["attack"])
            defense = int(row["defense"])
            element = str(row["element"]).strip().capitalize() if row["element"] else "Neutral"
            url = str(row["url"]).strip() if row["url"] else None

            # Prevent duplicates (optional but recommended)
            exists = await conn.fetchval(
                "SELECT 1 FROM monster_pets WHERE user_id=$1 AND LOWER(name)=LOWER($2) LIMIT 1;",
                int(user_id),
                name,
            )
            if exists:
                raise ValueError(f"You already own `{name}`.")

            await conn.execute(
                """
                INSERT INTO monster_pets (
                    user_id, name, hp, attack, defense, element,
                    grown, growth_stage, growth_time, growth_index,
                    equipped, url, default_name
                )
                VALUES (
                    $1, $2, $3, $4, $5, $6,
                    TRUE, 'adult', now(), 3,
                    FALSE, $7, $8
                );
                """,
                int(user_id),
                name,
                hp,
                attack,
                defense,
                element,
                url,
                name,
            )

            return {"name": name, "hp": hp, "attack": attack, "defense": defense, "element": element, "url": url}

    async def get_pet_preview(self, pet_name: str) -> dict | None:
        """
        Preview pet stats for embeds (no DB mutation).
        """
        async with self.bot.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT result_name, hp, attack, defense, element, url
                FROM splice_combinations
                WHERE LOWER(result_name) = LOWER($1)
                ORDER BY id DESC
                LIMIT 1;
                """,
                pet_name,
            )
        if not row:
            return None
        return {
            "name": str(row["result_name"]).strip(),
            "hp": int(row["hp"]),
            "attack": int(row["attack"]),
            "defense": int(row["defense"]),
            "element": str(row["element"]).strip().capitalize() if row["element"] else "Neutral",
            "url": str(row["url"]).strip() if row["url"] else None,
        }

    # ---------------- Commands ----------------

    @has_char()
    @commands.command(name="starterpack", aliases=["starter"], brief=_("Starter pack info."))
    @locale_doc
    async def starterpack(self, ctx: commands.Context):
        prefix = ctx.clean_prefix

        embed = discord.Embed(
            title="🎒 Starter Pack",
            description="A one-time pack (claimable up to **level 10**) to help you start faster.",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Contains",
            value=(
                f"- 💰 **${self.STARTER_MONEY:,}**\n"
                f"- 📦 Opens instantly into:\n"
                f"  • **{self.OPEN_DIVINE}** Divine crate\n"
                f"  • **{self.OPEN_MYSTERY}** Mystery crates\n"
                f"  • **{self.OPEN_RARE}** Rare crates\n"
                f"- 🔴 Amulet resources:\n"
                f"  • {self.CRAFT_RESOURCES['dragon_scales']}x Dragon Scales\n"
                f"  • {self.CRAFT_RESOURCES['mystic_dust']}x Mystic Dust\n"
                f"  • {self.CRAFT_RESOURCES['harmony_stones']}x Harmony Stones\n"
                f"- 🐾 Choose **1** starter pet"
            ),
            inline=False,
        )

        embed.add_field(
            name="Help",
            value=f"Tutorial: `{prefix}tuto`\nSupport: {self.support_mention(ctx)}",
            inline=False,
        )

        view = ui.View(timeout=120)

        async def _go_tuto(interaction: discord.Interaction):
            await interaction.response.defer()
            await ctx.invoke(self.tuto)

        async def _go_claim(interaction: discord.Interaction):
            await interaction.response.defer()
            await ctx.invoke(self.claimstarter)

        view.add_item(ui.Button(label="📘 Tutorial", style=discord.ButtonStyle.secondary))
        view.add_item(ui.Button(label="✅ Claim", style=discord.ButtonStyle.success))

        view.children[0].callback = _go_tuto
        view.children[1].callback = _go_claim

        sup_url = self.support_url(ctx)
        if sup_url:
            view.add_item(ui.Button(label="🆘 Support", style=discord.ButtonStyle.link, url=sup_url))

        await ctx.send(embed=embed, view=view)

    @has_char()
    @commands.command(name="tuto", brief=_("Starter tutorial."))
    @locale_doc
    async def tuto(self, ctx: commands.Context):
        prefix = ctx.clean_prefix

        pages: list[discord.Embed] = []

        # Page 1 — Daily routine
        e1 = discord.Embed(
            title="📘 Tutorial (1/5) — Daily routine",
            description=(
                "These are the commands you’ll press all the time.\n\n"
                f"• `{prefix}daily`\n"
                f"• `{prefix}donatordaily` (everyone can use)\n"
                f"• `{prefix}vote` (no top.gg)\n"
                f"• `{prefix}t` (check cooldowns)\n"
                f"• `{prefix}pray` (if you follow a god)\n\n"
                "**Core progression loops:**\n"
                f"• `{prefix}pve` (fight creatures for XP / eggs)\n"
                f"• `{prefix}bt fight` (Battle Tower fights / floors / prestige)\n"
            ),
            color=discord.Color.blurple(),
        )
        pages.append(e1)

        # Page 2 — Adventure + boosts
        e2 = discord.Embed(
            title="📘 Tutorial (2/5) — Adventure & boosts",
            description=(
                "**Adventure:**\n"
                f"• `{prefix}a <level>` send your character out\n"
                f"• `{prefix}s` finish / check adventure\n\n"
                "**Boosters:**\n"
                f"• `{prefix}b` check boosters\n"
                f"• `{prefix}activate <time|luck|money|all>` boosters last 24h\n\n"
                f"• `{prefix}adventureremind` to opt in or out of mentions to remind you when your adventure is finished\n\n"
            ),
            color=discord.Color.blurple(),
        )
        pages.append(e2)

        # Page 3 — Profile / gear / getting stronger
        e3 = discord.Embed(
            title="📘 Tutorial (3/5) — Profile, gear & power",
            description=(
                "**Your character:**\n"
                f"• `{prefix}pp` (profile)\n"
                f"• `{prefix}xp` (level / experience)\n\n"
                "**Weapons & armory:**\n"
                f"• `{prefix}armory` / `{prefix}arm` (open armory)\n"
                f"• `{prefix}ar` (your weapons)\n"
                f"• `{prefix}open <rarity> [amount]` (open crates)\n\n"
                "**Stats upgrades:**\n"
                f"• `{prefix}sp` (spend level-up points)\n"
                f"• `{prefix}rs` (spend money to improve stats)\n"
            ),
            color=discord.Color.blurple(),
        )
        pages.append(e3)

        # Page 4 — Gods, classes, pets, amulets, forge
        e4 = discord.Embed(
            title="📘 Tutorial (4/5) — Systems (Gods / Classes / Pets / Craft)",
            description=(
                "**Gods:**\n"
                f"• `{prefix}follow <god>` / `{prefix}unfollow`\n"
                f"• `{prefix}pray`\n"
                f"• `{prefix}favor` (resets, don’t panic)\n"
                f"• `{prefix}loot` (sell / sacrifice / hoard)\n"
                f"• `{prefix}sacrifice <Loot ID>`\n\n"
                "**Classes:**\n"
                f"• `{prefix}evolve` / `{prefix}tree`\n"
                f"• `{prefix}steal` (thief)\n"
                f"• `{prefix}scout` (ranger)\n\n"
                "**Pets:**\n"
                f"• `{prefix}pets`\n"
                f"• `{prefix}pets equip <id>`\n"
                f"• `{prefix}pets feed <id> <food_type>`\n"
                f"• `{prefix}pets help`\n\n"
                "**Amulets:**\n"
                f"• `{prefix}amulet available`\n"
                f"• `{prefix}amulet resources`\n"
                f"• `{prefix}amulet craft <type> <tier>`\n\n"
                "**Soul Forge:**\n"
                f"• `{prefix}soulforge` / `{prefix}splice <pet1> <pet2>`\n"
                f"• `{prefix}forgestatus` / `{prefix}repairforge`\n"
            ),
            color=discord.Color.blurple(),
        )
        pages.append(e4)

        # Page 5 — Economy / trader / minigames
        e5 = discord.Embed(
            title="📘 Tutorial (5/5) — Economy & minigames",
            description=(
                "**Shops:**\n"
                f"• `{prefix}m` (marketplace — people sell weapons)\n"
                f"• `{prefix}trader` (rotating shop)\n"
                f"• `{prefix}store` (buy boosters)\n\n"
                "**Getting richer (casino & games):**\n"
                f"• `{prefix}slots` / `{prefix}slots takeseat <#>`\n"
                f"• `{prefix}bj <amount>` (blackjack)\n"
                f"• `{prefix}flip [heads/tails] [amount]`\n\n"
                "**Events / other fun:**\n"
                f"• `{prefix}hg` (Hunger Games)\n"
                f"• `{prefix}tournament`\n"
            ),
            color=discord.Color.blurple(),
        )
        pages.append(e5)

        await ctx.send(embed=pages[0], view=TutorialView(ctx, pages))


    @has_char()
    @commands.cooldown(1, 30, commands.BucketType.user)
    @commands.command(name="claimstarter", aliases=["claimsp"], brief=_("Claim the starter pack (once)."))
    @locale_doc
    async def claimstarter(self, ctx: commands.Context):
        user_id = ctx.author.id
        level = await self.get_level(ctx)

        if level > 10:
            return await ctx.send("⛔ Starter Pack is only claimable up to **level 10**.")

        antiscript = ctx.bot.get_cog("AntiScript")
        if antiscript:
            # NEW: once per Discord account
            if await antiscript.starterpack_already_claimed(user_id):
                return await ctx.send("✅ You already claimed your Starter Pack on this Discord account.")

        async with self.bot.pool.acquire() as conn:
            # OLD guard (character/profile-based) — keep for backward compatibility + auto-migration
            claimed_profile = await conn.fetchval('SELECT starter_pack_claimed FROM profile WHERE "user"=$1;', user_id)

            # If they had claimed in the old system, make sure new system also blocks them
            if claimed_profile:
                if antiscript:
                    await antiscript.mark_starterpack_claimed(user_id)
                return await ctx.send("✅ You already claimed your Starter Pack on this Discord account.")

            async with conn.transaction():
                await conn.execute(
                    """
                    UPDATE profile
                    SET starter_pack_claimed = true,
                        crates_starterpack = crates_starterpack + 1
                    WHERE "user"=$1;
                    """,
                    user_id,
                )

        # Mark claim in AntiScript table (the new source of truth)
        if antiscript:
            await antiscript.mark_starterpack_claimed(user_id)

        await ctx.send(f"🎒 Starter Pack claimed! Open it with `{ctx.clean_prefix}openstarterpack`.")


    @has_char()
    @commands.cooldown(1, 30, commands.BucketType.user)
    @commands.command(name="openstarterpack", aliases=["opensp"], brief=_("Open your starter pack."))
    @locale_doc
    async def openstarterpack(self, ctx: commands.Context):
        user_id = ctx.author.id
        prefix = ctx.clean_prefix

        packs = await self.packs_available(user_id)
        if packs < 1:
            return await ctx.send(f"You don't have a Starter Pack. Claim it with `{prefix}claimstarter` (level ≤ 10).")

        # Consume pack + grant base money + open crates (virtual) in ONE transaction/connection
        async with self.bot.pool.acquire() as conn:
            async with conn.transaction():
                # race-safe consume
                row = await conn.fetchrow(
                    """
                    UPDATE profile
                    SET crates_starterpack = crates_starterpack - 1,
                        money = money + $1
                    WHERE "user"=$2 AND crates_starterpack >= 1
                    RETURNING crates_starterpack;
                    """,
                    self.STARTER_MONEY,
                    user_id,
                )
                if not row:
                    return await ctx.send("⛔ You no longer have a Starter Pack to open.")

                all_items: list[dict] = []
                total_xp = 0
                total_money_from_fortune = 0

                # fixed opens
                all_items += await self.open_item_crates_virtual(ctx, conn, "divine", self.OPEN_DIVINE)
                all_items += await self.open_item_crates_virtual(ctx, conn, "rare", self.OPEN_RARE)

                # mystery -> roll -> apply
                rolled = self.roll_mystery_counts(self.OPEN_MYSTERY)
                for rarity, cnt in rolled.items():
                    if cnt <= 0:
                        continue
                    if rarity == "fortune":
                        xp, money = await self.open_fortune_virtual(ctx, conn, cnt)
                        total_xp += xp
                        total_money_from_fortune += money
                    else:
                        all_items += await self.open_item_crates_virtual(ctx, conn, rarity, cnt)

        # crafting resources (your helper uses its own conn; fine)
        await self.grant_crafting_resources(user_id)

        # Summary
        stats_raw = [(i["damage"] + i["armor"]) for i in all_items]
        top_stats = sorted(stats_raw, reverse=True)[:5]
        avg_stat = round(sum(stats_raw) / max(1, len(stats_raw)), 2)

        type_counts = Counter([i.get("type", "item") for i in all_items])
        type_lines = "\n".join([f"- {t}: x{c}" for t, c in type_counts.most_common()]) or "—"

        summary = discord.Embed(
            title="🎒 Starter Pack opened!",
            color=discord.Color.green(),
            description=(
                f"**You received:**\n"
                f"- 💰 **${self.STARTER_MONEY:,}** *(+ ${total_money_from_fortune:,} from Fortune, if any)*\n"
                f"- ✨ **{total_xp:,} XP** *(from Fortune, if any)*\n"
                f"- 🧪 Crafting resources: "
                f"{self.CRAFT_RESOURCES['dragon_scales']} Dragon Scales, "
                f"{self.CRAFT_RESOURCES['mystic_dust']} Mystic Dust, "
                f"{self.CRAFT_RESOURCES['harmony_stones']} Harmony Stones\n"
                f"- 🗡️ **{len(all_items)} items** from crate opens\n\n"
                f"Average item stat: **{avg_stat}**\n"
                f"Top stats: **{', '.join(map(str, top_stats)) or '—'}**"
            ),
        )
        summary.add_field(name="Item types", value=type_lines, inline=False)
        await ctx.send(embed=summary)

        # Pet pages with real stats + image
        pet_pages: list[discord.Embed] = []
        for idx, pet_name in enumerate(self.PET_CHOICES, start=1):
            preview = await self.get_pet_preview(pet_name)
            if not preview:
                e = discord.Embed(
                    title=f"🐾 Choose your starter pet ({idx}/{len(self.PET_CHOICES)})",
                    description=f"**{pet_name}**\n\n*(No preview found in splice_combinations)*",
                    color=discord.Color.blurple(),
                )
            else:
                e = discord.Embed(
                    title=f"🐾 Choose your starter pet ({idx}/{len(self.PET_CHOICES)})",
                    description=f"**{preview['name']}**",
                    color=discord.Color.blurple(),
                )
                e.add_field(name="Element", value=preview["element"], inline=True)
                e.add_field(name="HP", value=str(preview["hp"]), inline=True)
                e.add_field(name="ATK", value=str(preview["attack"]), inline=True)
                e.add_field(name="DEF", value=str(preview["defense"]), inline=True)
                if preview.get("url"):
                    e.set_thumbnail(url=preview["url"])
                e.set_footer(text="Click Choose to pick this pet.")
            pet_pages.append(e)

        async def on_choose(interaction: discord.Interaction, pet_name: str):
            try:
                granted = await self.grant_adult_pet(user_id, pet_name)
            except Exception as e:
                return await interaction.followup.send(
                    f"❌ Could not grant pet: `{type(e).__name__}: {e}`",
                    ephemeral=True,
                )

            # nice confirmation (public)
            msg = f"✅ You chose **{granted['name']}**! (Element: {granted['element']} | HP {granted['hp']} ATK {granted['attack']} DEF {granted['defense']})"
            await interaction.followup.send(msg, ephemeral=False)

        await ctx.send(embed=pet_pages[0], view=PetChoiceView(ctx, pet_pages, self.PET_CHOICES, on_choose))


async def setup(bot):
    await bot.add_cog(StarterPack(bot))
