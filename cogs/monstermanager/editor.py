"""Private, interactive editor for the live ``monsters.json`` catalog."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Optional

import discord
from discord.ext import commands

from .catalog import (
    CORE_MONSTER_FIELDS,
    MAX_MONSTER_TIER,
    MIN_MONSTER_TIER,
    MonsterCatalogError,
    MonsterCatalogRepository,
    MonsterEntry,
    MonsterLocator,
    filter_entries,
    flatten_catalog,
    parse_extra_json,
    parse_image_url,
    parse_optional_bool,
    parse_required_text,
    parse_stat_triplet,
    validate_tier,
)


MONSTER_EDITOR_USER_ID = 295173706496475136
PROJECT_ROOT = Path(__file__).resolve().parents[2]
MONSTERS_PATH = PROJECT_ROOT / "monsters.json"
PAGE_SIZE = 5
EDITOR_COLOUR = 0x7557D5
SUCCESS_COLOUR = 0x49C47A
DANGER_COLOUR = 0xE05263


def _shorten(value, maximum: int) -> str:
    text = str(value or "")
    if len(text) <= maximum:
        return text
    return text[: max(0, maximum - 1)].rstrip() + "…"


def _truth_label(value) -> str:
    return "Yes" if value is True else "No"


def _entry_title(entry: MonsterEntry) -> str:
    public_icon = "🟢" if entry.monster.get("ispublic") is True else "🔒"
    name = _shorten(entry.monster.get("name") or "Unnamed Monster", 70)
    return f"{public_icon} T{entry.tier} · #{entry.index + 1} · {name}"


def _entry_value(entry: MonsterEntry) -> str:
    monster = entry.monster
    url = str(monster.get("url") or "").strip()
    artwork = f"[Open artwork]({url})" if url.startswith(("https://", "http://")) else "No artwork"
    frontier = ""
    if monster.get("frontier_only") is True:
        region = monster.get("frontier_region_id")
        frontier = f"\n🧭 Frontier only{f' · `{region}`' if region else ''}"
    extra_keys = sorted(set(monster).difference(CORE_MONSTER_FIELDS))
    extras = f"\n🧩 Extra: `{', '.join(extra_keys)}`" if extra_keys else ""
    return (
        f"❤️ **{monster.get('hp', '?')}**  ·  ⚔️ **{monster.get('attack', '?')}**  ·  "
        f"🛡️ **{monster.get('defense', '?')}**  ·  ✨ **{monster.get('element', '?')}**\n"
        f"{artwork}{frontier}{extras}"
    )


class MonsterSearchModal(discord.ui.Modal):
    def __init__(self, editor: "MonsterEditorView"):
        super().__init__(title="Search monsters")
        self.editor = editor
        self.query = discord.ui.TextInput(
            label="Any part of the monster name",
            placeholder="Example: dragon",
            default=editor.query,
            required=False,
            max_length=100,
        )
        self.add_item(self.query)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.editor.query = str(self.query.value or "").strip()
        self.editor.page = 0
        self.editor.selected = None
        await self.editor.render(interaction)


class MonsterPageModal(discord.ui.Modal):
    def __init__(self, editor: "MonsterEditorView"):
        super().__init__(title="Jump to catalog page")
        self.editor = editor
        self.page_number = discord.ui.TextInput(
            label=f"Page number (1-{editor.total_pages})",
            placeholder=str(editor.page + 1),
            default=str(editor.page + 1),
            min_length=1,
            max_length=4,
        )
        self.add_item(self.page_number)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            requested = int(str(self.page_number.value).strip())
        except ValueError:
            return await interaction.response.send_message(
                "Page must be a whole number.", ephemeral=True
            )
        if requested < 1 or requested > self.editor.total_pages:
            return await interaction.response.send_message(
                f"Choose a page from 1 to {self.editor.total_pages}.", ephemeral=True
            )
        self.editor.page = requested - 1
        self.editor.selected = None
        await self.editor.render(interaction)


class MonsterCoreModal(discord.ui.Modal):
    """Shared core fields for Add and Edit."""

    def __init__(
        self,
        editor: "MonsterEditorView",
        *,
        entry: Optional[MonsterEntry] = None,
    ):
        super().__init__(title="Add a monster" if entry is None else "Edit monster")
        self.editor = editor
        self.entry = entry
        monster = entry.monster if entry else {}
        self.name = discord.ui.TextInput(
            label="Monster name",
            placeholder="Example: Astral Dragon",
            default=str(monster.get("name") or ""),
            max_length=100,
        )
        self.tier = discord.ui.TextInput(
            label=f"Tier ({MIN_MONSTER_TIER}-{MAX_MONSTER_TIER})",
            placeholder="1",
            default=entry.tier if entry else "1",
            min_length=1,
            max_length=2,
        )
        self.stats = discord.ui.TextInput(
            label="HP, Attack, Defense",
            placeholder="500, 470, 460",
            default=(
                f"{monster.get('hp')}, {monster.get('attack')}, {monster.get('defense')}"
                if entry
                else ""
            ),
            max_length=100,
        )
        self.element = discord.ui.TextInput(
            label="Element",
            placeholder="Fire",
            default=str(monster.get("element") or ""),
            max_length=50,
        )
        self.url = discord.ui.TextInput(
            label="Image URL",
            placeholder="https://...",
            default=str(monster.get("url") or ""),
            style=discord.TextStyle.paragraph,
            max_length=1000,
        )
        for item in (self.name, self.tier, self.stats, self.element, self.url):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            tier = validate_tier(self.tier.value)
            hp, attack, defense = parse_stat_triplet(self.stats.value)
            record = dict(self.entry.monster) if self.entry else {}
            record.update(
                {
                    "name": parse_required_text(self.name.value, "Name", maximum=100),
                    "hp": hp,
                    "attack": attack,
                    "defense": defense,
                    "element": parse_required_text(
                        self.element.value, "Element", maximum=50
                    ),
                    "url": parse_image_url(self.url.value),
                }
            )
            if self.entry is None:
                record["ispublic"] = True
                saved = await self.editor.repository.add(tier, record)
                status = (
                    f"Added **{saved.monster['name']}** to tier **{saved.tier}**. "
                    "It defaults to public; use **Settings** for visibility or Frontier fields."
                )
            else:
                saved = await self.editor.repository.update(
                    self.entry.locator, tier, record
                )
                status = f"Saved changes to **{saved.monster['name']}**."
        except MonsterCatalogError as error:
            return await interaction.response.send_message(
                f"❌ {error}", ephemeral=True
            )
        except Exception as error:
            return await interaction.response.send_message(
                f"❌ Could not save the monster: {error}", ephemeral=True
            )

        self.editor.query = ""
        self.editor.tier_filter = None
        self.editor.selected = saved.locator
        await self.editor.refresh_runtime_caches()
        await self.editor.render(interaction, status=status, success=True)


class MonsterSettingsModal(discord.ui.Modal):
    def __init__(self, editor: "MonsterEditorView", entry: MonsterEntry):
        super().__init__(title="Monster settings and extra fields")
        self.editor = editor
        self.entry = entry
        monster = entry.monster
        extra = {
            key: value
            for key, value in monster.items()
            if key not in CORE_MONSTER_FIELDS
        }
        self.public = discord.ui.TextInput(
            label="Public (true or false)",
            default="true" if monster.get("ispublic") is True else "false",
            max_length=5,
        )
        self.frontier_only = discord.ui.TextInput(
            label="Frontier only (blank removes field)",
            default=(
                "true"
                if monster.get("frontier_only") is True
                else "false" if "frontier_only" in monster else ""
            ),
            required=False,
            max_length=5,
        )
        self.frontier_region = discord.ui.TextInput(
            label="Frontier region ID (blank removes)",
            default=str(monster.get("frontier_region_id") or ""),
            required=False,
            max_length=100,
        )
        self.extra_json = discord.ui.TextInput(
            label="Other fields as JSON",
            placeholder='{"event_only": true}',
            default=json.dumps(extra, ensure_ascii=False, indent=2) if extra else "",
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=4000,
        )
        for item in (
            self.public,
            self.frontier_only,
            self.frontier_region,
            self.extra_json,
        ):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            is_public = parse_optional_bool(self.public.value, "Public")
            if is_public is None:
                raise MonsterCatalogError("Public must be `true` or `false`.")
            frontier_only = parse_optional_bool(
                self.frontier_only.value, "Frontier only"
            )
            frontier_region = str(self.frontier_region.value or "").strip()
            extras = parse_extra_json(self.extra_json.value)

            record = {
                key: value
                for key, value in self.entry.monster.items()
                if key in CORE_MONSTER_FIELDS
            }
            record["ispublic"] = is_public
            if frontier_only is None:
                record.pop("frontier_only", None)
            else:
                record["frontier_only"] = frontier_only
            if frontier_region:
                record["frontier_region_id"] = parse_required_text(
                    frontier_region, "Frontier region", maximum=100
                )
            else:
                record.pop("frontier_region_id", None)
            record.update(extras)
            saved = await self.editor.repository.update(
                self.entry.locator, self.entry.tier, record
            )
        except MonsterCatalogError as error:
            return await interaction.response.send_message(
                f"❌ {error}", ephemeral=True
            )
        except Exception as error:
            return await interaction.response.send_message(
                f"❌ Could not save settings: {error}", ephemeral=True
            )

        self.editor.selected = saved.locator
        await self.editor.refresh_runtime_caches()
        await self.editor.render(
            interaction,
            status=f"Saved settings for **{saved.monster['name']}**.",
            success=True,
        )


class DeleteMonsterConfirmation(discord.ui.View):
    def __init__(self, editor: "MonsterEditorView", entry: MonsterEntry):
        super().__init__(timeout=90)
        self.editor = editor
        self.entry = entry

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) == MONSTER_EDITOR_USER_ID:
            return True
        await interaction.response.send_message("This confirmation is not yours.", ephemeral=True)
        return False

    @discord.ui.button(label="Delete permanently", style=discord.ButtonStyle.danger)
    async def confirm(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        try:
            deleted = await self.editor.repository.delete(self.entry.locator)
        except MonsterCatalogError as error:
            return await interaction.response.edit_message(
                content=f"❌ {error}", embed=None, view=None
            )
        except Exception as error:
            return await interaction.response.edit_message(
                content=f"❌ Could not delete the monster: {error}", embed=None, view=None
            )

        await interaction.response.edit_message(
            content=(
                f"🗑️ Deleted **{deleted.monster.get('name', 'Unnamed Monster')}** from tier "
                f"**{deleted.tier}**. The previous file is in `monsters.json.bak`."
            ),
            embed=None,
            view=None,
        )
        self.editor.selected = None
        await self.editor.refresh_runtime_caches()
        await self.editor.render(
            status=f"Deleted **{deleted.monster.get('name', 'Unnamed Monster')}**.",
            success=True,
        )
        self.stop()

    @discord.ui.button(label="Keep monster", style=discord.ButtonStyle.secondary)
    async def cancel(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ) -> None:
        await interaction.response.edit_message(
            content="Deletion cancelled.", embed=None, view=None
        )
        self.stop()


class MonsterEditorView(discord.ui.View):
    def __init__(
        self,
        ctx: commands.Context,
        repository: MonsterCatalogRepository,
        *,
        query: str = "",
    ):
        super().__init__(timeout=900)
        self.ctx = ctx
        self.repository = repository
        self.query = str(query or "").strip()[:100]
        self.tier_filter: Optional[str] = None
        self.page = 0
        self.selected: Optional[MonsterLocator] = None
        self.data: dict = {}
        self.entries: list[MonsterEntry] = []
        self.filtered: list[MonsterEntry] = []
        self.message: Optional[discord.Message] = None

    @property
    def total_pages(self) -> int:
        return max(1, math.ceil(len(self.filtered) / PAGE_SIZE))

    @property
    def current_entries(self) -> list[MonsterEntry]:
        start = self.page * PAGE_SIZE
        return self.filtered[start:start + PAGE_SIZE]

    def selected_entry(self) -> Optional[MonsterEntry]:
        if self.selected is None:
            return None
        return next(
            (
                entry
                for entry in self.entries
                if entry.locator == self.selected
            ),
            None,
        )

    def _page_for_locator(self, locator: MonsterLocator) -> int:
        for index, entry in enumerate(self.filtered):
            if entry.locator == locator:
                return index // PAGE_SIZE
        return 0

    async def reload(self) -> None:
        self.data = await self.repository.read()
        self.entries = flatten_catalog(self.data)
        self.filtered = filter_entries(
            self.entries, query=self.query, tier=self.tier_filter
        )
        self.page = max(0, min(self.page, self.total_pages - 1))
        if self.selected is not None:
            selected = self.selected_entry()
            if selected is None:
                self.selected = None
            else:
                self.selected = selected.locator
                self.page = self._page_for_locator(selected.locator)

    async def refresh_runtime_caches(self) -> None:
        """Keep loaded readers in this bot process in sync with the saved file."""
        try:
            data = await self.repository.read()
            battles = self.ctx.bot.get_cog("Battles")
            if battles is not None:
                battles.monsters_data = data

            assistant = self.ctx.bot.get_cog("FableAssistant")
            if assistant is not None:
                assistant.monsters_json = data
                monster_data = {}
                for tier, monsters in data.items():
                    for monster in monsters:
                        if "name" not in monster or not monster.get("ispublic", True):
                            continue
                        monster_copy = dict(monster)
                        monster_copy["level"] = int(tier) if str(tier).isdigit() else tier
                        monster_data[monster["name"]] = monster_copy
                assistant.monster_data = monster_data
                assistant.elements = sorted(
                    {
                        str(monster.get("element")).casefold()
                        for monster in monster_data.values()
                        if monster.get("element")
                    }
                )
        except Exception as error:
            logger = getattr(self.ctx.bot, "logger", None)
            if logger is not None:
                logger.warning("Could not refresh monster catalog runtime caches: %s", error)

    def build_embed(self, *, status: str = "", success: bool = False) -> discord.Embed:
        colour = SUCCESS_COLOUR if success else EDITOR_COLOUR
        title = "🧬 Monster Catalog Studio"
        filters = []
        if self.query:
            filters.append(f"name contains `{_shorten(self.query, 45)}`")
        if self.tier_filter:
            filters.append(f"tier `{self.tier_filter}`")
        filter_text = " · ".join(filters) if filters else "All monsters"
        description = (
            f"**{len(self.filtered)}** shown · **{len(self.entries)}** total · {filter_text}\n"
            "Select a monster below, then use **Edit**, **Settings**, or **Delete**."
        )
        if status:
            description = f"✅ {status}\n\n{description}"
        embed = discord.Embed(title=title, description=description, colour=colour)

        selected = self.selected_entry()
        if selected is not None:
            embed.add_field(
                name=f"Selected · {_entry_title(selected)}",
                value=_entry_value(selected),
                inline=False,
            )
            selected_url = str(selected.monster.get("url") or "").strip()
            if selected_url.startswith(("https://", "http://")):
                embed.set_thumbnail(url=selected_url)

        if not self.current_entries:
            embed.add_field(
                name="No matches",
                value="Try a shorter name search, clear the tier filter, or add a monster.",
                inline=False,
            )
        else:
            for entry in self.current_entries:
                embed.add_field(
                    name=_entry_title(entry), value=_entry_value(entry), inline=False
                )

        tier_counts = []
        for tier in sorted(self.data, key=lambda value: int(value) if str(value).isdigit() else 999):
            monsters = self.data.get(tier)
            if isinstance(monsters, list):
                tier_counts.append(f"T{tier}:{len(monsters)}")
        embed.add_field(
            name="Catalog health",
            value=" · ".join(tier_counts) or "No tiers found",
            inline=False,
        )
        embed.set_footer(
            text=(
                f"Page {self.page + 1}/{self.total_pages} · {PAGE_SIZE} per page · "
                "Every save creates monsters.json.bak"
            )
        )
        return embed

    def sync_components(self) -> None:
        self.clear_items()

        page_entries = self.current_entries
        if page_entries:
            monster_options = []
            selected = self.selected_entry()
            for offset, entry in enumerate(page_entries):
                monster = entry.monster
                monster_options.append(
                    discord.SelectOption(
                        label=_shorten(
                            f"T{entry.tier} #{entry.index + 1} · {monster.get('name', 'Unnamed')}",
                            100,
                        ),
                        value=str(offset),
                        description=_shorten(
                            f"{monster.get('element', '?')} · HP {monster.get('hp', '?')} · "
                            f"ATK {monster.get('attack', '?')} · DEF {monster.get('defense', '?')}",
                            100,
                        ),
                        default=(
                            selected is not None
                            and selected.locator == entry.locator
                        ),
                    )
                )
            monster_select = discord.ui.Select(
                placeholder="Select a monster on this page",
                min_values=1,
                max_values=1,
                options=monster_options,
                row=0,
            )

            async def select_monster(interaction: discord.Interaction) -> None:
                entry = self.current_entries[int(interaction.data["values"][0])]
                self.selected = entry.locator
                await self.render(interaction)

            monster_select.callback = select_monster
            self.add_item(monster_select)

        tier_options = [
            discord.SelectOption(
                label="All tiers",
                value="all",
                default=self.tier_filter is None,
                emoji="🌐",
            )
        ]
        for tier in range(MIN_MONSTER_TIER, MAX_MONSTER_TIER + 1):
            tier_options.append(
                discord.SelectOption(
                    label=f"Tier {tier}",
                    value=str(tier),
                    default=self.tier_filter == str(tier),
                )
            )
        tier_select = discord.ui.Select(
            placeholder="Filter by tier",
            options=tier_options,
            row=1,
        )

        async def select_tier(interaction: discord.Interaction) -> None:
            selected_tier = interaction.data["values"][0]
            self.tier_filter = None if selected_tier == "all" else selected_tier
            self.page = 0
            self.selected = None
            await self.render(interaction)

        tier_select.callback = select_tier
        self.add_item(tier_select)

        previous = discord.ui.Button(
            label="Previous",
            emoji="◀️",
            style=discord.ButtonStyle.secondary,
            disabled=self.page <= 0,
            row=2,
        )
        next_page = discord.ui.Button(
            label="Next",
            emoji="▶️",
            style=discord.ButtonStyle.secondary,
            disabled=self.page >= self.total_pages - 1,
            row=2,
        )
        search = discord.ui.Button(
            label="Search",
            emoji="🔎",
            style=discord.ButtonStyle.primary,
            row=2,
        )
        clear = discord.ui.Button(
            label="Clear",
            style=discord.ButtonStyle.secondary,
            disabled=not self.query and self.tier_filter is None,
            row=2,
        )
        jump = discord.ui.Button(
            label="Jump",
            emoji="↗️",
            style=discord.ButtonStyle.secondary,
            disabled=self.total_pages <= 1,
            row=2,
        )

        async def go_previous(interaction: discord.Interaction) -> None:
            self.page = max(0, self.page - 1)
            self.selected = None
            await self.render(interaction)

        async def go_next(interaction: discord.Interaction) -> None:
            self.page = min(self.total_pages - 1, self.page + 1)
            self.selected = None
            await self.render(interaction)

        async def open_search(interaction: discord.Interaction) -> None:
            await interaction.response.send_modal(MonsterSearchModal(self))

        async def clear_filters(interaction: discord.Interaction) -> None:
            self.query = ""
            self.tier_filter = None
            self.page = 0
            self.selected = None
            await self.render(interaction)

        async def jump_to_page(interaction: discord.Interaction) -> None:
            await interaction.response.send_modal(MonsterPageModal(self))

        previous.callback = go_previous
        next_page.callback = go_next
        search.callback = open_search
        clear.callback = clear_filters
        jump.callback = jump_to_page
        for item in (previous, next_page, search, clear, jump):
            self.add_item(item)

        selected = self.selected_entry()
        add = discord.ui.Button(
            label="Add", emoji="➕", style=discord.ButtonStyle.success, row=3
        )
        edit = discord.ui.Button(
            label="Edit",
            emoji="✏️",
            style=discord.ButtonStyle.primary,
            disabled=selected is None,
            row=3,
        )
        settings = discord.ui.Button(
            label="Settings",
            emoji="⚙️",
            style=discord.ButtonStyle.secondary,
            disabled=selected is None,
            row=3,
        )
        delete = discord.ui.Button(
            label="Delete",
            emoji="🗑️",
            style=discord.ButtonStyle.danger,
            disabled=selected is None,
            row=3,
        )
        close = discord.ui.Button(
            label="Close", style=discord.ButtonStyle.secondary, row=3
        )

        async def add_monster(interaction: discord.Interaction) -> None:
            await interaction.response.send_modal(MonsterCoreModal(self))

        async def edit_monster(interaction: discord.Interaction) -> None:
            entry = self.selected_entry()
            if entry is None:
                return await interaction.response.send_message(
                    "Select a monster first.", ephemeral=True
                )
            await interaction.response.send_modal(MonsterCoreModal(self, entry=entry))

        async def edit_settings(interaction: discord.Interaction) -> None:
            entry = self.selected_entry()
            if entry is None:
                return await interaction.response.send_message(
                    "Select a monster first.", ephemeral=True
                )
            await interaction.response.send_modal(MonsterSettingsModal(self, entry))

        async def delete_monster(interaction: discord.Interaction) -> None:
            entry = self.selected_entry()
            if entry is None:
                return await interaction.response.send_message(
                    "Select a monster first.", ephemeral=True
                )
            embed = discord.Embed(
                title="Delete this monster?",
                description=(
                    f"**{entry.monster.get('name', 'Unnamed Monster')}** · Tier {entry.tier}\n\n"
                    "This removes the exact selected JSON entry. A one-step backup is created first."
                ),
                colour=DANGER_COLOUR,
            )
            url = str(entry.monster.get("url") or "").strip()
            if url.startswith(("https://", "http://")):
                embed.set_thumbnail(url=url)
            await interaction.response.send_message(
                embed=embed,
                view=DeleteMonsterConfirmation(self, entry),
                ephemeral=True,
            )

        async def close_editor(interaction: discord.Interaction) -> None:
            await interaction.response.edit_message(
                embed=discord.Embed(
                    title="Monster Catalog Studio closed",
                    description="Run the command again whenever you want to edit monsters.json.",
                    colour=EDITOR_COLOUR,
                ),
                view=None,
            )
            self.stop()

        add.callback = add_monster
        edit.callback = edit_monster
        settings.callback = edit_settings
        delete.callback = delete_monster
        close.callback = close_editor
        for item in (add, edit, settings, delete, close):
            self.add_item(item)

    async def render(
        self,
        interaction: Optional[discord.Interaction] = None,
        *,
        status: str = "",
        success: bool = False,
    ) -> None:
        try:
            await self.reload()
        except MonsterCatalogError as error:
            if interaction is not None and not interaction.response.is_done():
                await interaction.response.send_message(f"❌ {error}", ephemeral=True)
            elif self.message is not None:
                await self.message.edit(content=f"❌ {error}", embed=None, view=None)
            return
        self.sync_components()
        embed = self.build_embed(status=status, success=success)
        if interaction is not None and not interaction.response.is_done():
            await interaction.response.edit_message(content=None, embed=embed, view=self)
        elif self.message is not None:
            await self.message.edit(content=None, embed=embed, view=self)

    async def start(self) -> discord.Message:
        await self.reload()
        self.sync_components()
        self.message = await self.ctx.send(embed=self.build_embed(), view=self)
        return self.message

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if (
            int(interaction.user.id) == MONSTER_EDITOR_USER_ID
            and int(interaction.user.id) == int(self.ctx.author.id)
        ):
            return True
        await interaction.response.send_message(
            "This private monster editor is not available to you.", ephemeral=True
        )
        return False

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class MonsterCatalogEditor(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.repository = MonsterCatalogRepository(MONSTERS_PATH)

    @commands.command(
        name="monstereditor",
        aliases=["monsteradmin", "monstercatalogeditor", "editmonsters"],
        hidden=True,
    )
    async def monster_editor(self, ctx: commands.Context, *, search: str = ""):
        """Open the private interactive monsters.json editor."""
        if int(ctx.author.id) != MONSTER_EDITOR_USER_ID:
            return
        view = MonsterEditorView(ctx, self.repository, query=search)
        try:
            await view.start()
        except MonsterCatalogError as error:
            await ctx.send(f"❌ Could not open the monster catalog: {error}")
        except Exception as error:
            await ctx.send(f"❌ Could not start the monster editor: {error}")
