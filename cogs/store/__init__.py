"""
The IdleRPG Discord Bot
Copyright (C) 2018-2021 Diniboy and Gelbpunkt
Copyright (C) 2023-2024 Lunar (PrototypeX37)

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

from classes.converters import IntGreaterThan
from utils.checks import has_char
from utils.i18n import _, locale_doc


BOOSTER_PRICES = {"time": 1000, "luck": 500, "money": 1000, "all": 2500}
BOOSTER_LABELS = {
    "time": "Time Booster",
    "luck": "Luck Booster",
    "money": "Money Booster",
    "all": "All Boosters",
}


class BoosterStoreSelect(discord.ui.Select):
    def __init__(self, view: "BoosterStoreView"):
        self.store_view = view
        options = [
            discord.SelectOption(
                label=BOOSTER_LABELS[key],
                description=f"${BOOSTER_PRICES[key]:,} each",
                value=key,
                emoji={"time": "⏱️", "luck": "🍀", "money": "💰", "all": "✨"}[key],
            )
            for key in ("time", "luck", "money", "all")
        ]
        super().__init__(
            placeholder="Choose boosters to buy...",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.store_view.ctx.author.id:
            return await interaction.response.send_message("This is not your store menu.", ephemeral=True)
        self.store_view.selected = self.values[0]
        await interaction.response.edit_message(embed=self.store_view.embed(), view=self.store_view)


class BoosterStoreView(discord.ui.View):
    def __init__(self, cog: "Store", ctx: commands.Context):
        super().__init__(timeout=90)
        self.cog = cog
        self.ctx = ctx
        self.selected = "time"
        self.add_item(BoosterStoreSelect(self))

    def embed(self) -> discord.Embed:
        shopembed = self.cog.store_embed(self.ctx)
        label = BOOSTER_LABELS[self.selected]
        price = BOOSTER_PRICES[self.selected]
        shopembed.add_field(
            name="Selected",
            value=f"**{label}** - ${price:,} each",
            inline=False,
        )
        return shopembed

    async def buy(self, interaction: discord.Interaction, amount: int):
        if interaction.user.id != self.ctx.author.id:
            return await interaction.response.send_message("This is not your store menu.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        _ok, message = await self.cog.buy_boosters(self.ctx, self.selected, amount)
        await interaction.followup.send(message, ephemeral=True)

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
            return await interaction.response.send_message("This is not your store menu.", ephemeral=True)
        await interaction.message.delete()
        self.stop()


class BoosterInventorySelect(discord.ui.Select):
    def __init__(self, view: "BoosterInventoryView"):
        self.inventory_view = view
        options = [
            discord.SelectOption(
                label=BOOSTER_LABELS[key],
                description="Activate or buy this booster",
                value=key,
                emoji={"time": "⏱️", "luck": "🍀", "money": "💰", "all": "✨"}[key],
            )
            for key in ("time", "luck", "money", "all")
        ]
        super().__init__(
            placeholder="Choose booster action...",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.inventory_view.ctx.author.id:
            return await interaction.response.send_message("This is not your booster menu.", ephemeral=True)
        self.inventory_view.selected = self.values[0]
        await self.inventory_view.refresh()
        await interaction.response.edit_message(embed=self.inventory_view.embed(), view=self.inventory_view)


class BoosterInventoryView(discord.ui.View):
    def __init__(self, cog: "Store", ctx: commands.Context, counts: dict[str, int], active: dict[str, object]):
        super().__init__(timeout=90)
        self.cog = cog
        self.ctx = ctx
        self.counts = counts
        self.active = active
        self.selected = "time"
        self.add_item(BoosterInventorySelect(self))

    async def refresh(self):
        self.counts = await self.cog.get_booster_counts(self.ctx.author.id)
        self.active = await self.cog.get_active_boosters(self.ctx)

    def embed(self) -> discord.Embed:
        return self.cog.boosters_embed(self.ctx, self.counts, self.active, selected=self.selected)

    async def refresh_message(self):
        await self.refresh()
        try:
            await self.message.edit(embed=self.embed(), view=self)  # type: ignore[attr-defined]
        except (discord.NotFound, discord.Forbidden, discord.HTTPException, AttributeError):
            pass

    async def buy(self, interaction: discord.Interaction, amount: int):
        if interaction.user.id != self.ctx.author.id:
            return await interaction.response.send_message("This is not your booster menu.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        _ok, message = await self.cog.buy_boosters(self.ctx, self.selected, amount)
        await self.refresh_message()
        await interaction.followup.send(message, ephemeral=True)

    @discord.ui.button(label="Activate", style=discord.ButtonStyle.green, emoji="⚡", row=1)
    async def activate(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            return await interaction.response.send_message("This is not your booster menu.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        _ok, message = await self.cog.activate_boosters_from_ui(self.ctx, self.selected)
        await self.refresh_message()
        await interaction.followup.send(message, ephemeral=True)

    @discord.ui.button(label="Buy x1", style=discord.ButtonStyle.primary, row=1)
    async def buy_one(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self.buy(interaction, 1)

    @discord.ui.button(label="Buy x5", style=discord.ButtonStyle.primary, row=1)
    async def buy_five(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self.buy(interaction, 5)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.red, row=1)
    async def close(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            return await interaction.response.send_message("This is not your booster menu.", ephemeral=True)
        await interaction.message.delete()
        self.stop()


class Store(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def store_embed(self, ctx) -> discord.Embed:
        shopembed = discord.Embed(
            title=_("Fable Store"),
            description=_(
                "Welcome! Choose a booster below, then buy with the buttons."
            ),
            colour=discord.Colour.blurple(),
        )
        shopembed.add_field(
            name=_("Boosters"),
            value=_(
                "`#1` Time Booster\t**$1000**\tBoosts adventure time by 50%\n`#2` Luck"
                " Booster\t**$500**\tBoosts adventure luck (not `{prefix}luck`) by"
                " 25%\n`#3` Money Booster\t**$1000**\tBoosts adventure money rewards"
                " by 25%"
            ).format(prefix=ctx.clean_prefix),
            inline=False,
        )
        shopembed.set_thumbnail(url=f"{self.bot.BASE_URL}/business.png")
        return shopembed

    async def get_booster_counts(self, user_id: int) -> dict[str, int]:
        row = await self.bot.pool.fetchrow(
            """
            SELECT
                COALESCE(time_booster, 0) AS time,
                COALESCE(luck_booster, 0) AS luck,
                COALESCE(money_booster, 0) AS money
            FROM profile
            WHERE "user" = $1
            """,
            user_id,
        )
        if not row:
            return {"time": 0, "luck": 0, "money": 0}
        return {key: int(row[key] or 0) for key in ("time", "luck", "money")}

    async def get_active_boosters(self, ctx) -> dict[str, object]:
        return {
            "time": await self.bot.get_booster(ctx.author, "time"),
            "luck": await self.bot.get_booster(ctx.author, "luck"),
            "money": await self.bot.get_booster(ctx.author, "money"),
        }

    def boosters_embed(
        self,
        ctx,
        counts: dict[str, int],
        active: dict[str, object],
        *,
        selected: str = "time",
    ) -> discord.Embed:
        active_lines = []
        for key, value in active.items():
            if value:
                active_lines.append(_("{label} - {time}").format(label=BOOSTER_LABELS[key], time=str(value).split(".")[0]))
        desc = ""
        if active_lines:
            desc = f"**{_('Currently active')}**\n" + "\n".join(active_lines) + "\n\n"
        desc += (
            f"{_('Time Boosters')}: `{counts['time']}`\n"
            f"{_('Luck Boosters')}: `{counts['luck']}`\n"
            f"{_('Money Boosters')}: `{counts['money']}`"
        )
        if selected:
            desc += f"\n\nSelected: **{BOOSTER_LABELS[selected]}**"
        return discord.Embed(title=_("Your Boosters"), description=desc, colour=discord.Colour.blurple())

    async def buy_boosters(self, ctx, booster: str, amount: int) -> tuple[bool, str]:
        if booster not in BOOSTER_PRICES:
            return False, _("Please either buy `time`, `luck` or `money`.")
        if amount <= 0:
            return False, _("The amount must be greater than 0.")
        price = BOOSTER_PRICES[booster] * amount
        async with self.bot.pool.acquire() as conn:
            money = await conn.fetchval('SELECT COALESCE("money", 0) FROM profile WHERE "user"=$1;', ctx.author.id)
            if money is None:
                return False, _("You need a character first.")
            if int(money) < price:
                return False, _("You're too poor.")
            if booster != "all":
                await conn.execute(
                    f"UPDATE profile SET {booster}_booster={booster}_booster+$1,"
                    ' "money"="money"-$2 WHERE "user"=$3;',
                    amount,
                    price,
                    ctx.author.id,
                )
            else:
                await conn.execute(
                    'UPDATE profile SET "time_booster"="time_booster"+$1,'
                    ' "luck_booster"="luck_booster"+$1,'
                    ' "money_booster"="money_booster"+$1, "money"="money"-$2 WHERE'
                    ' "user"=$3;',
                    amount,
                    price,
                    ctx.author.id,
                )
            await self.bot.log_transaction(
                ctx,
                from_=ctx.author.id,
                to=2,
                subject="buy boosters",
                data={"Gold": price},
                conn=conn,
            )
        return True, _(
            "Successfully bought **{amount}x** {booster} booster(s). Use"
            " `{prefix}boosters` to view your new boosters."
        ).format(amount=amount, booster=booster, prefix=ctx.clean_prefix)

    async def activate_boosters_from_ui(self, ctx, boostertype: str) -> tuple[bool, str]:
        if boostertype not in ["time", "luck", "money", "all"]:
            return False, _("That is not a valid booster type. Must be `time/luck/money/all`.")

        counts = await self.get_booster_counts(ctx.author.id)
        if boostertype != "all":
            if counts[boostertype] <= 0:
                return False, _("You don't have any of these boosters.")
            await self.bot.pool.execute(
                f'UPDATE profile SET "{boostertype}_booster"="{boostertype}_booster"-1'
                ' WHERE "user"=$1;',
                ctx.author.id,
            )
            await self.bot.activate_booster(ctx.author, boostertype)
            return True, _(
                "Successfully activated a **{booster} booster** for the next **24 hours**!"
            ).format(booster=boostertype.title())

        reducible = [key for key in ("time", "luck", "money") if counts[key] > 0]
        if not reducible:
            return False, _("Nothing to activate.")
        to_reduce = ", ".join([f'"{key}_booster"="{key}_booster"-1' for key in reducible])
        await self.bot.pool.execute(
            f'UPDATE profile SET {to_reduce} WHERE "user"=$1;',
            ctx.author.id,
        )
        for key in reducible:
            await self.bot.activate_booster(ctx.author, key)
        return True, _("Successfully activated {types} for the next **24 hours**!").format(types=", ".join(reducible))

    @commands.command(brief=_("Show the booster store"))
    @locale_doc
    async def store(self, ctx):
        _(
            """Show the booster store. For a detailed explanation what the boosters do, check `{prefix}help boosters`."""
        )
        view = BoosterStoreView(self, ctx)
        await ctx.send(embed=view.embed(), view=view)

    @has_char()
    @commands.command(brief=_("Buy some boosters"))
    @locale_doc
    async def purchase(self, ctx, booster: str.lower, amount: IntGreaterThan(0) = 1):
        _(
            """`<booster>` - The booster type to buy, can be time, luck, money or all
            `[amount]` - The amount of boosters to buy; defaults to 1

            Buy one or more booster from the store. For a detailed explanation what the boosters do, check `{prefix}help boosters`."""
        )
        _ok, message = await self.buy_boosters(ctx, booster, amount)
        await ctx.send(message)

    @has_char()
    @commands.command(aliases=["b"], brief=_("View your boosters"))
    @locale_doc
    async def boosters(self, ctx):
        _(
            """View your boosters and the active ones' status. Each one has a different effect.

              - Time boosters halve the adventures' times (must be active before starting an adventure)
              - Luck boosters increase your adventure chances by 25%
              - Money boosters increase the amount of gold gained from adventures by 25%

            Each booster lasts 24 hours after activation."""
        )
        counts = await self.get_booster_counts(ctx.author.id)
        active = await self.get_active_boosters(ctx)
        view = BoosterInventoryView(self, ctx, counts, active)
        message = await ctx.send(embed=view.embed(), view=view)
        view.message = message

    @has_char()
    @commands.command(brief=_("Activate a booster"))
    @locale_doc
    async def activate(self, ctx, boostertype: str.lower):
        _(
            """`<boostertype>` - The booster type to activate, can be time, luck, money or all

            Activate a booster. For a detailed explanation what the boosters do, check `{prefix}help boosters`."""
        )
        if boostertype not in ["time", "luck", "money", "all"]:
            return await ctx.send(
                _("That is not a valid booster type. Must be `time/luck/money/all`.")
            )
        if boostertype != "all":
            boosters = ctx.character_data[f"{boostertype}_booster"]
            if boosters <= 0:
                return await ctx.send(_("You don't have any of these boosters."))
            check = await self.bot.get_booster(ctx.author, boostertype)
            if check:
                if not await ctx.confirm(
                    _(
                        "This booster is already running. Do you want to refresh it"
                        " anyways?"
                    )
                ):
                    return

            await self.bot.pool.execute(
                f'UPDATE profile SET "{boostertype}_booster"="{boostertype}_booster"-1'
                ' WHERE "user"=$1;',
                ctx.author.id,
            )
            await self.bot.activate_booster(ctx.author, boostertype)
            await ctx.send(
                _(
                    "Successfully activated a **{booster} booster** for the next **24"
                    " hours**!"
                ).format(booster=boostertype.title())
            )
        else:
            if not await ctx.confirm(
                _(
                    "This will overwrite all active boosters and refresh them. Are you"
                    " sure?"
                )
            ):
                return

            reducible = [
                i
                for i in ("time", "luck", "money")
                if ctx.character_data[f"{i}_booster"]
            ]

            if not reducible:
                return await ctx.send(_("Nothing to activate."))

            to_reduce = ", ".join([f'"{i}_booster"="{i}_booster"-1' for i in reducible])

            await self.bot.pool.execute(
                f'UPDATE profile SET {to_reduce} WHERE "user"=$1;', ctx.author.id
            )

            for i in reducible:
                await self.bot.activate_booster(ctx.author, i)
            await ctx.send(
                _("Successfully activated {types} for the next **24 hours**!").format(
                    types=", ".join(reducible)
                )
            )


async def setup(bot):
    await bot.add_cog(Store(bot))
