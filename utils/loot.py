"""
Helpers for interactive loot actions.
"""
from __future__ import annotations

import asyncio
import secrets
from decimal import Decimal
from typing import Any, Awaitable, Callable, Iterable, Sequence

import discord

from classes.errors import NoChoice
from utils.i18n import _


LOOT_LOCK_TTL = 120
LOOT_LOCK_PREFIX = "loot-action-lock"
LOOT_ACTION_COOLDOWN = 180
LOOT_ACTION_COOLDOWN_KEY = "sacrificeexchange"
LOOT_PAGE_SIZE = 10
LOOT_DISPLAY_NAME_LIMIT = 80


def unique_loot_ids(loot_ids: Iterable[int]) -> list[int]:
    seen = set()
    unique = []
    for loot_id in loot_ids:
        if loot_id in seen:
            continue
        seen.add(loot_id)
        unique.append(int(loot_id))
    return unique


async def fetch_user_loot(conn, user_id: int, loot_ids: Sequence[int] | None = None):
    if loot_ids is None:
        return await conn.fetch(
            'SELECT * FROM loot WHERE "user"=$1 ORDER BY "value" DESC, "id" DESC;',
            user_id,
        )

    return await conn.fetch(
        'SELECT * FROM loot WHERE "id"=ANY($1) AND "user"=$2'
        ' ORDER BY "value" DESC, "id" DESC;',
        unique_loot_ids(loot_ids),
        user_id,
    )


async def delete_user_loot(conn, user_id: int, loot_ids: Sequence[int]):
    return await conn.fetch(
        'DELETE FROM loot WHERE "id"=ANY($1) AND "user"=$2'
        ' RETURNING "id", "value";',
        unique_loot_ids(loot_ids),
        user_id,
    )


async def acquire_loot_locks(bot, user_id: int, loot_ids: Sequence[int], action: str):
    token = f"{user_id}:{action}:{secrets.token_urlsafe(16)}"
    acquired = {}
    blocked = []

    for loot_id in sorted(unique_loot_ids(loot_ids)):
        key = f"{LOOT_LOCK_PREFIX}:{loot_id}"
        result = await bot.redis.execute_command(
            "SET", key, token, "EX", LOOT_LOCK_TTL, "NX"
        )
        if result:
            acquired[loot_id] = token
        else:
            blocked.append(loot_id)

    if blocked:
        await release_loot_locks(bot, acquired)
        return {}, blocked

    return acquired, []


async def release_loot_locks(bot, locks: dict[int, str]) -> None:
    script = (
        "if redis.call('GET', KEYS[1]) == ARGV[1] then "
        "return redis.call('DEL', KEYS[1]) "
        "else return 0 end"
    )
    for loot_id, token in locks.items():
        await bot.redis.execute_command(
            "EVAL", script, 1, f"{LOOT_LOCK_PREFIX}:{loot_id}", token
        )


async def reserve_loot_action_cooldown(bot, user_id: int) -> float:
    key = f"cd:{user_id}:{LOOT_ACTION_COOLDOWN_KEY}"
    reserved = await bot.redis.execute_command(
        "SET", key, LOOT_ACTION_COOLDOWN_KEY, "EX", LOOT_ACTION_COOLDOWN, "NX"
    )
    if reserved:
        return 0

    ttl = await bot.redis.execute_command("TTL", key)
    if ttl == -2:
        await bot.redis.execute_command(
            "SET", key, LOOT_ACTION_COOLDOWN_KEY, "EX", LOOT_ACTION_COOLDOWN
        )
        return 0

    if ttl == -1:
        await bot.redis.execute_command("EXPIRE", key, LOOT_ACTION_COOLDOWN)
        return float(LOOT_ACTION_COOLDOWN)

    return float(ttl)


async def reset_loot_action_cooldown(bot, ctx) -> None:
    await bot.reset_cooldown(ctx)
    await bot.redis.execute_command(
        "DEL", f"cd:{ctx.author.id}:{LOOT_ACTION_COOLDOWN_KEY}"
    )


def loot_value(rows) -> Decimal:
    return sum((Decimal(row["value"]) for row in rows), Decimal(0))


def loot_page_count(rows: Sequence) -> int:
    return max(1, (len(rows) + LOOT_PAGE_SIZE - 1) // LOOT_PAGE_SIZE)


def loot_page_rows(rows: Sequence, page: int) -> list:
    start = page * LOOT_PAGE_SIZE
    return list(rows[start:start + LOOT_PAGE_SIZE])


def build_loot_options(rows: Sequence, all_description: str) -> list[discord.SelectOption]:
    options = [
        discord.SelectOption(
            label=_("All loot items"),
            value="all",
            description=all_description,
        )
    ]
    for item in rows:
        label = f"#{item['id']} {item['name']}"
        options.append(
            discord.SelectOption(
                label=label[:100],
                value=str(item["id"]),
                description=_("Value: {value}").format(value=item["value"])[:100],
            )
        )
    return options


def format_loot_line(item) -> str:
    name = str(item["name"])
    if len(name) > LOOT_DISPLAY_NAME_LIMIT:
        name = f"{name[:LOOT_DISPLAY_NAME_LIMIT - 3]}..."
    return f"`{item['id']}` - {name} ({item['value']})"


class LootItemSelect(discord.ui.Select):
    def __init__(self, view: "LootSelectionView"):
        self.loot_view = view
        options = view.build_options()

        super().__init__(
            placeholder=view.select_placeholder(),
            min_values=1,
            max_values=len(options),
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if "all" in self.values:
            selected = [int(item["id"]) for item in self.loot_view.rows]
        else:
            selected = [int(value) for value in self.values]

        if not self.loot_view.future.done():
            self.loot_view.future.set_result(selected)

        self.loot_view.stop()
        await interaction.response.edit_message(view=discord.ui.View())


class LootSelectionView(discord.ui.View):
    def __init__(
        self,
        ctx,
        rows,
        *,
        title: str,
        placeholder: str,
        timeout: int = 60,
    ):
        super().__init__(timeout=timeout)
        self.ctx = ctx
        self.rows = list(rows)
        self.title = title
        self.placeholder = placeholder
        self.current_page = 0
        self.future: asyncio.Future[list[int] | None] = asyncio.Future()
        self.select = LootItemSelect(self)
        self.add_item(self.select)

    @property
    def max_page(self) -> int:
        return loot_page_count(self.rows) - 1

    def current_page_rows(self) -> list:
        return loot_page_rows(self.rows, self.current_page)

    def select_placeholder(self) -> str:
        return _("{placeholder} - Page {page}/{pages}").format(
            placeholder=self.placeholder,
            page=self.current_page + 1,
            pages=self.max_page + 1,
        )

    def build_options(self) -> list[discord.SelectOption]:
        return build_loot_options(
            self.current_page_rows(),
            _("Use every loot item in this action."),
        )

    def refresh_components(self) -> None:
        options = self.build_options()
        self.select.options = options
        self.select.max_values = len(options)
        self.select.placeholder = self.select_placeholder()
        self.previous_page.disabled = self.current_page <= 0
        self.next_page.disabled = self.current_page >= self.max_page

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.ctx.author.id:
            return True

        asyncio.create_task(
            interaction.response.send_message(
                _("This command was not initiated by you."), ephemeral=True
            )
        )
        return False

    async def on_timeout(self) -> None:
        if not self.future.done():
            self.future.set_exception(NoChoice(_("You didn't choose anything.")))

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, row=1)
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not self.future.done():
            self.future.set_result(None)
        self.stop()
        await interaction.response.edit_message(view=discord.ui.View())

    @discord.ui.button(emoji="⬅️", style=discord.ButtonStyle.primary, row=2)
    async def previous_page(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if self.current_page <= 0:
            await interaction.response.defer()
            return
        self.current_page -= 1
        self.refresh_components()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(emoji="➡️", style=discord.ButtonStyle.primary, row=2)
    async def next_page(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if self.current_page >= self.max_page:
            await interaction.response.defer()
            return
        self.current_page += 1
        self.refresh_components()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    def embed(self) -> discord.Embed:
        total_value = int(loot_value(self.rows))
        page_rows = self.current_page_rows()
        shown = "\n".join(format_loot_line(item) for item in page_rows)

        embed = discord.Embed(
            title=_("{title} - Page {page}/{pages}").format(
                title=self.title,
                page=self.current_page + 1,
                pages=self.max_page + 1,
            ),
            description=shown,
            colour=self.ctx.bot.config.game.primary_colour,
        )
        embed.set_footer(
            text=_("{count} loot item(s), total value {value}").format(
                count=len(self.rows), value=total_value
            )
        )
        return embed

    async def prompt(self) -> list[int] | None:
        self.refresh_components()
        await self.ctx.send(embed=self.embed(), view=self)
        return await self.future


class LootRewardSelect(discord.ui.Select):
    def __init__(self, view: "LootRewardView"):
        self.reward_view = view
        super().__init__(
            placeholder=_("Select a reward"),
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label=f"${view.money_value}", value="money"),
                discord.SelectOption(label=f"{view.xp_value} XP", value="xp"),
            ],
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not self.reward_view.future.done():
            self.reward_view.future.set_result(self.values[0])

        self.reward_view.stop()
        await interaction.response.edit_message(view=discord.ui.View())


class LootRewardView(discord.ui.View):
    def __init__(self, ctx, *, item_count: int, money_value: int, timeout: int = 60):
        super().__init__(timeout=timeout)
        self.ctx = ctx
        self.item_count = item_count
        self.money_value = money_value
        self.xp_value = money_value // 4
        self.future: asyncio.Future[str | None] = asyncio.Future()
        self.add_item(LootRewardSelect(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.ctx.author.id:
            return True

        asyncio.create_task(
            interaction.response.send_message(
                _("This command was not initiated by you."), ephemeral=True
            )
        )
        return False

    async def on_timeout(self) -> None:
        if not self.future.done():
            self.future.set_exception(NoChoice(_("You didn't choose anything.")))

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, row=1)
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not self.future.done():
            self.future.set_result(None)
        self.stop()
        await interaction.response.edit_message(view=discord.ui.View())

    def embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=_("Select a reward for the {count} items").format(
                count=self.item_count
            ),
            description=(
                f"- **${self.money_value}**\n"
                + _("- **{value} XP**").format(value=self.xp_value)
            ),
            colour=self.ctx.bot.config.game.primary_colour,
        )
        embed.set_footer(
            text=_("The selected loot is locked until you choose or cancel.")
        )
        return embed

    async def prompt(self) -> str | None:
        await self.ctx.send(embed=self.embed(), view=self)
        return await self.future


class LootManagerSelect(discord.ui.Select):
    def __init__(self, manager_view: "LootManagerView"):
        self.manager_view = manager_view
        options = manager_view.build_options()

        super().__init__(
            placeholder=manager_view.select_placeholder(),
            min_values=1,
            max_values=len(options),
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if "all" in self.values:
            self.manager_view.selected_loot_ids = [
                int(item["id"]) for item in self.manager_view.rows
            ]
            selected_count = len(self.manager_view.selected_loot_ids)
        else:
            self.manager_view.selected_loot_ids = [int(value) for value in self.values]
            selected_count = len(self.manager_view.selected_loot_ids)

        await interaction.response.send_message(
            _("Selected {count} loot item(s).").format(count=selected_count),
            ephemeral=True,
        )


class LootManagerView(discord.ui.View):
    def __init__(
        self,
        ctx,
        rows,
        *,
        exchange_callback: Callable[[Any, list[int], int, bool], Awaitable[None]],
        sacrifice_callback: Callable[[Any, list[int], int, bool], Awaitable[None]],
        timeout: int = 120,
    ):
        super().__init__(timeout=timeout)
        self.ctx = ctx
        self.rows = list(rows)
        self.exchange_callback = exchange_callback
        self.sacrifice_callback = sacrifice_callback
        self.selected_loot_ids: list[int] = []
        self.message: discord.Message | None = None
        self.current_page = 0
        self.select = LootManagerSelect(self)
        self.add_item(self.select)

    @property
    def max_page(self) -> int:
        return loot_page_count(self.rows) - 1

    def current_page_rows(self) -> list:
        return loot_page_rows(self.rows, self.current_page)

    def select_placeholder(self) -> str:
        return _("Choose loot items - Page {page}/{pages}").format(
            page=self.current_page + 1,
            pages=self.max_page + 1,
        )

    def build_options(self) -> list[discord.SelectOption]:
        return build_loot_options(
            self.current_page_rows(),
            _("Select every loot item."),
        )

    def refresh_components(self) -> None:
        options = self.build_options()
        self.select.options = options
        self.select.max_values = len(options)
        self.select.placeholder = self.select_placeholder()
        self.previous_page.disabled = self.current_page <= 0
        self.next_page.disabled = self.current_page >= self.max_page

    async def start(self) -> None:
        self.refresh_components()
        self.message = await self.ctx.send(embed=self.embed(), view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.ctx.author.id:
            return True

        asyncio.create_task(
            interaction.response.send_message(
                _("This command was not initiated by you."), ephemeral=True
            )
        )
        return False

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    def embed(self) -> discord.Embed:
        total_value = int(loot_value(self.rows))
        page_rows = self.current_page_rows()
        shown = "\n".join(format_loot_line(item) for item in page_rows)

        embed = discord.Embed(
            title=_("{user} has the following loot items. - Page {page}/{pages}").format(
                user=self.ctx.disp,
                page=self.current_page + 1,
                pages=self.max_page + 1,
            ),
            description=shown,
            colour=discord.Colour.blurple(),
        )
        embed.set_footer(
            text=_(
                "Select loot, then choose Exchange or Sacrifice. {count} item(s),"
                " total value {value}."
            ).format(count=len(self.rows), value=total_value)
        )
        return embed

    async def run_action(
        self,
        interaction: discord.Interaction,
        callback: Callable[[Any, list[int], int, bool], Awaitable[None]],
    ) -> None:
        if not self.selected_loot_ids:
            await interaction.response.send_message(
                _("Select at least one loot item first."), ephemeral=True
            )
            return

        await interaction.response.defer()
        for item in self.children:
            item.disabled = True
        if interaction.message:
            await interaction.message.edit(view=self)

        selected_loot_ids = list(self.selected_loot_ids)
        self.stop()
        await callback(self.ctx, selected_loot_ids, len(selected_loot_ids), True)

    @discord.ui.button(emoji="⬅️", style=discord.ButtonStyle.primary, row=2)
    async def previous_page(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if self.current_page <= 0:
            await interaction.response.defer()
            return
        self.current_page -= 1
        self.selected_loot_ids = []
        self.refresh_components()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(emoji="➡️", style=discord.ButtonStyle.primary, row=2)
    async def next_page(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if self.current_page >= self.max_page:
            await interaction.response.defer()
            return
        self.current_page += 1
        self.selected_loot_ids = []
        self.refresh_components()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="Exchange", style=discord.ButtonStyle.success, row=1)
    async def exchange(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self.run_action(interaction, self.exchange_callback)

    @discord.ui.button(label="Sacrifice", style=discord.ButtonStyle.danger, row=1)
    async def sacrifice(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self.run_action(interaction, self.sacrifice_callback)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.secondary, row=1)
    async def close(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.defer()
        for item in self.children:
            item.disabled = True
        if interaction.message:
            await interaction.message.edit(view=self)
        self.stop()
