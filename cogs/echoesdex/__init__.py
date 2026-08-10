import os
import json
import re
import random
import asyncio
import unicodedata
from io import BytesIO
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import discord
from datetime import timedelta
from discord.ext import commands
import asyncpg
from typing import Optional, Dict, Any, List, Set, Tuple, Union
from PIL import Image, ImageDraw, ImageFont, ImageOps
from utils.divine_familiars import DIVINE_FAMILIARS, resolve_familiar_key

# --------------------
# Helpers
# --------------------

def _rec(rec: Optional[asyncpg.Record]) -> Dict[str, Any]:
    return dict(rec) if rec is not None else {}

_QUOTE_CHARS = "\"'“”‘’`"


def _strip_outer_quotes(s: str) -> str:
    s = (s or "").strip()
    while len(s) >= 2 and s[0] in _QUOTE_CHARS and s[-1] in _QUOTE_CHARS:
        s = s[1:-1].strip()
    return s


def _key(s: Optional[str]) -> str:
    """Strong search key."""
    s = _strip_outer_quotes((s or "").strip())
    if not s:
        return ""

    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()
    s = re.sub(r"[^a-z0-9\s]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _pretty_name_from_json(m: Dict[str, Any], fallback: str) -> str:
    return (m.get("default_name") or m.get("name") or fallback or "?").strip()


def _display_name_with_cosmetic(value: Optional[str]) -> str:
    raw_name = (value or "").strip()
    if not raw_name:
        return raw_name
    familiar_key = resolve_familiar_key(raw_name)
    if not familiar_key:
        return raw_name
    familiar = DIVINE_FAMILIARS.get(familiar_key)
    if not familiar:
        return raw_name

    canonical_name = str(familiar.get("name") or raw_name).strip()
    cosmetic_name = str(familiar.get("cosmetic_name") or "").strip()
    if cosmetic_name:
        return f"{cosmetic_name} ({canonical_name})"
    return canonical_name


def _truthy(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "y", "t")
    return False


def _as_mention_if_int(v: Any) -> str:
    """Format as Discord mention if it looks like an int snowflake; else str(v)."""
    if v is None:
        return ""
    try:
        n = int(v)
        if n > 0:
            return f"<@{n}>"
    except Exception:
        pass
    return str(v)


def _is_valid_button_url(value: Optional[str]) -> bool:
    """Validate URLs used in Discord link buttons."""
    raw = (value or "").strip()
    if not raw or " " in raw:
        return False

    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https", "discord"):
        return False

    if parsed.scheme in ("http", "https") and not parsed.netloc:
        return False

    return True


# --------------------
# Simple embed paginator (Prev/Next arrows)
# --------------------

class PagedEmbeds(discord.ui.View):
    """Reusable Prev/Next paginator for a list of embeds in one message."""

    def __init__(self, embeds: List[discord.Embed], *, timeout: int = 180):
        super().__init__(timeout=timeout)
        self.embeds = embeds
        self.i = 0

        self.prev_btn = discord.ui.Button(label="◀", style=discord.ButtonStyle.secondary)
        self.next_btn = discord.ui.Button(label="▶", style=discord.ButtonStyle.secondary)

        self.prev_btn.callback = self.prev
        self.next_btn.callback = self.next

        self.add_item(self.prev_btn)
        self.add_item(self.next_btn)
        self._sync()

    def _sync(self):
        self.prev_btn.disabled = (self.i <= 0)
        self.next_btn.disabled = (self.i >= len(self.embeds) - 1)

    async def prev(self, inter: discord.Interaction):
        if self.i > 0:
            self.i -= 1
            self._sync()
            if inter.response.is_done():
                await inter.message.edit(embed=self.embeds[self.i], view=self)
            else:
                await inter.response.edit_message(embed=self.embeds[self.i], view=self)

    async def next(self, inter: discord.Interaction):
        if self.i < len(self.embeds) - 1:
            self.i += 1
            self._sync()
            if inter.response.is_done():
                await inter.message.edit(embed=self.embeds[self.i], view=self)
            else:
                await inter.response.edit_message(embed=self.embeds[self.i], view=self)


class UserSpliceListView(discord.ui.View):
    """Dropdown selector for a user's splice list (alphabetical, paged)."""

    def __init__(
        self,
        cog: "EchoesDex",
        ctx: commands.Context,
        target_user: discord.User,
        splice_names: List[str],
        *,
        timeout: int = 180,
    ):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.ctx = ctx
        self.target_user = target_user
        self.splice_names = splice_names
        self.per_page = 25
        self.page = 0
        self.message: Optional[discord.Message] = None

        self.select = discord.ui.Select(
            placeholder="Choose a splice result...",
            min_values=1,
            max_values=1,
        )
        self.select.callback = self._on_select
        self.add_item(self.select)

        self.prev_btn = discord.ui.Button(label="◀", style=discord.ButtonStyle.secondary)
        self.next_btn = discord.ui.Button(label="▶", style=discord.ButtonStyle.secondary)
        self.list_btn = discord.ui.Button(label="📋 List", style=discord.ButtonStyle.secondary)

        self.prev_btn.callback = self._on_prev
        self.next_btn.callback = self._on_next
        self.list_btn.callback = self._on_list

        self.add_item(self.prev_btn)
        self.add_item(self.next_btn)
        self.add_item(self.list_btn)

        self._sync_components()

    @property
    def total_pages(self) -> int:
        return max(1, (len(self.splice_names) + self.per_page - 1) // self.per_page)

    def _page_slice(self) -> Tuple[int, int]:
        start = self.page * self.per_page
        end = min(len(self.splice_names), start + self.per_page)
        return start, end

    def _sync_components(self) -> None:
        start, end = self._page_slice()
        options: List[discord.SelectOption] = []

        for idx in range(start, end):
            nm = self.splice_names[idx]
            options.append(
                discord.SelectOption(
                    label=nm[:100],
                    value=str(idx),
                    description=f"#{idx + 1}",
                )
            )

        self.select.options = options
        self.select.placeholder = f"Choose splice ({self.page + 1}/{self.total_pages})"

        self.prev_btn.disabled = self.page <= 0
        self.next_btn.disabled = self.page >= self.total_pages - 1

    def _build_list_embed(self) -> discord.Embed:
        start, end = self._page_slice()
        names = self.splice_names[start:end]

        embed = discord.Embed(
            title=f"🧬 EchoesDex Splice List — {self.target_user.display_name}",
            color=discord.Color.blurple(),
            description=(
                "\n".join(f"{i + 1}. **{n}**" for i, n in enumerate(names, start=start))
                or "—"
            ),
        )
        embed.set_footer(
            text=(
                f"{len(self.splice_names)} total splice result(s) • "
                f"Page {self.page + 1}/{self.total_pages}"
            )
        )
        return embed

    async def _on_prev(self, interaction: discord.Interaction):
        if self.page > 0:
            self.page -= 1
            self._sync_components()
        await interaction.response.edit_message(embed=self._build_list_embed(), view=self)

    async def _on_next(self, interaction: discord.Interaction):
        if self.page < self.total_pages - 1:
            self.page += 1
            self._sync_components()
        await interaction.response.edit_message(embed=self._build_list_embed(), view=self)

    async def _on_list(self, interaction: discord.Interaction):
        self._sync_components()
        await interaction.response.edit_message(embed=self._build_list_embed(), view=self)

    async def _on_select(self, interaction: discord.Interaction):
        try:
            idx = int(self.select.values[0])
        except Exception:
            return await interaction.response.send_message("Invalid splice selection.", ephemeral=True)

        if idx < 0 or idx >= len(self.splice_names):
            return await interaction.response.send_message("Invalid splice selection.", ephemeral=True)

        selected_name = self.splice_names[idx]
        sp = await self.cog._get_splice_by_result(selected_name)
        if not sp:
            sp = await self.cog._get_splice_fuzzy(selected_name)
        if not sp:
            return await interaction.response.send_message(
                f"Could not open **{selected_name}** right now.",
                ephemeral=True,
            )

        embed, _ = await self.cog._embed_for_splice_return_parents(sp)
        prefix = self.ctx.clean_prefix
        result_name = sp.get("result_name") or selected_name
        embed.add_field(
            name="EchoesDex Entry",
            value=f"Use `{prefix}echoesdex \"{result_name}\"` to open this directly.",
            inline=False,
        )
        embed.set_footer(
            text=(
                f"{self.target_user.display_name}'s splice list • "
                f"Page {self.page + 1}/{self.total_pages}"
            )
        )
        await interaction.response.edit_message(embed=embed, view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.ctx.author.id:
            return True
        await interaction.response.send_message("This splice list is not yours.", ephemeral=True)
        return False

    async def on_timeout(self):
        if self.message is None:
            return
        for child in self.children:
            child.disabled = True
        try:
            await self.message.edit(view=self)
        except Exception:
            pass

    async def start(self):
        self.message = await self.ctx.send(embed=self._build_list_embed(), view=self)
        return self.message


class SplicerBackgroundPickerView(discord.ui.View):
    """Interactive picker for Splicer Card backgrounds."""

    def __init__(
        self,
        cog: "EchoesDex",
        ctx: commands.Context,
        target_user: discord.User,
        entries: List[Dict[str, str]],
        current_key: str,
        *,
        timeout: int = 60,
    ):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.ctx = ctx
        self.target_user = target_user
        self.entries = entries
        self.current_index = 0
        self.message: Optional[discord.Message] = None
        self._saved: bool = False

        for i, entry in enumerate(self.entries):
            if entry["key"] == current_key:
                self.current_index = i
                break

        self.select = discord.ui.Select(
            placeholder="Choose a background...",
            min_values=1,
            max_values=1,
        )
        self.select.callback = self._on_select
        self.add_item(self.select)

        self.prev_btn = discord.ui.Button(label="◀", style=discord.ButtonStyle.secondary)
        self.next_btn = discord.ui.Button(label="▶", style=discord.ButtonStyle.secondary)
        self.use_btn = discord.ui.Button(label="Use This", emoji="✅", style=discord.ButtonStyle.success)

        self.prev_btn.callback = self._on_prev
        self.next_btn.callback = self._on_next
        self.use_btn.callback = self._on_use_this

        self.add_item(self.prev_btn)
        self.add_item(self.next_btn)
        self.add_item(self.use_btn)

        self._sync_components()

    def _sync_components(self) -> None:
        options: List[discord.SelectOption] = []
        for i, entry in enumerate(self.entries[:25]):
            options.append(
                discord.SelectOption(
                    label=entry["label"][:100],
                    value=entry["key"],
                    description=(entry["key"][:80] or "theme"),
                    default=(i == self.current_index),
                )
            )
        self.select.options = options
        self.prev_btn.disabled = self.current_index <= 0
        self.next_btn.disabled = self.current_index >= len(self.entries) - 1

    def _current(self) -> Dict[str, str]:
        return self.entries[self.current_index]

    def _build_embed(self, *, saved_note: Optional[str] = None) -> discord.Embed:
        cur = self._current()
        embed = discord.Embed(
            title="🎨 Splicer Card Background",
            color=discord.Color.gold(),
            description=(
                f"Theme: **{cur['label']}** (`{cur['key']}`)\n"
                f"Use arrows to browse, dropdown to jump, and **Use This** to save for {self.target_user.mention}."
            ),
        )
        if saved_note:
            embed.add_field(name="Saved", value=saved_note, inline=False)

        source = cur.get("source") or ""
        if source and _is_valid_button_url(source):
            embed.set_image(url=source)
        else:
            embed.add_field(name="Preview", value="No remote preview available for this theme.", inline=False)

        embed.set_footer(text=f"Theme {self.current_index + 1}/{len(self.entries)}")
        return embed

    async def _save_current(self) -> None:
        cur = self._current()
        await self.cog._set_user_splicer_background_key(self.target_user.id, cur["key"])
        self._saved = True

    async def _on_prev(self, interaction: discord.Interaction):
        if self.current_index > 0:
            self.current_index -= 1
            self._sync_components()
        await interaction.response.edit_message(embed=self._build_embed(), view=self)

    async def _on_next(self, interaction: discord.Interaction):
        if self.current_index < len(self.entries) - 1:
            self.current_index += 1
            self._sync_components()
        await interaction.response.edit_message(embed=self._build_embed(), view=self)

    async def _on_select(self, interaction: discord.Interaction):
        chosen = self.select.values[0]
        for i, entry in enumerate(self.entries):
            if entry["key"] == chosen:
                self.current_index = i
                break
        self._sync_components()
        await self._save_current()
        await interaction.response.edit_message(
            embed=self._build_embed(saved_note=f"Background set to `{self._current()['key']}`."),
            view=self,
        )

    async def _on_use_this(self, interaction: discord.Interaction):
        await self._save_current()
        await interaction.response.edit_message(
            embed=self._build_embed(saved_note=f"Background set to `{self._current()['key']}`."),
            view=self,
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.ctx.author.id:
            return True
        await interaction.response.send_message("This picker is not yours.", ephemeral=True)
        return False

    async def on_timeout(self):
        if not self.message:
            return
        for child in self.children:
            child.disabled = True
        try:
            await self.message.edit(view=self)
        except Exception:
            pass
        if not self._saved:
            try:
                await self.ctx.send("you didn't chose anything")
            except Exception:
                pass

    async def start(self):
        self.message = await self.ctx.send(embed=self._build_embed(), view=self)
        return self.message


# --------------------
# Navigation View with Back (Entry navigation)
# --------------------

class DexNav(discord.ui.View):
    """Parent jump buttons + ◀ Back history inside the same message."""

    def __init__(self, cog: "EchoesDex", current_name: str, parents: List[str], *, timeout: int = 180):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.stack: List[str] = []
        self.current_name: str = current_name
        self.parents: List[str] = parents[:2] if parents else []

        self.back_btn = discord.ui.Button(label="◀ Back", style=discord.ButtonStyle.secondary)
        self.p1_btn = discord.ui.Button(label="Parent 1", style=discord.ButtonStyle.secondary)
        self.p2_btn = discord.ui.Button(label="Parent 2", style=discord.ButtonStyle.secondary)

        self.back_btn.callback = self._on_back
        self.p1_btn.callback = self._on_parent1
        self.p2_btn.callback = self._on_parent2

        self.add_item(self.back_btn)
        self.add_item(self.p1_btn)
        self.add_item(self.p2_btn)

        self._sync_buttons()

    def _sync_buttons(self):
        self.back_btn.disabled = len(self.stack) == 0

        if len(self.parents) >= 1 and self.parents[0]:
            self.p1_btn.label = self.parents[0]
            self.p1_btn.disabled = False
        else:
            self.p1_btn.label = "Parent 1"
            self.p1_btn.disabled = True

        if len(self.parents) >= 2 and self.parents[1]:
            self.p2_btn.label = self.parents[1]
            self.p2_btn.disabled = False
        else:
            self.p2_btn.label = "Parent 2"
            self.p2_btn.disabled = True

    async def _on_back(self, interaction: discord.Interaction):
        if not self.stack:
            return await interaction.response.send_message("No previous entry.", ephemeral=True)
        prev = self.stack.pop()
        await self._go_to(interaction, prev, push=False)

    async def _on_parent1(self, interaction: discord.Interaction):
        if len(self.parents) >= 1 and self.parents[0]:
            self.stack.append(self.current_name)
            await self._go_to(interaction, self.parents[0])

    async def _on_parent2(self, interaction: discord.Interaction):
        if len(self.parents) >= 2 and self.parents[1]:
            self.stack.append(self.current_name)
            await self._go_to(interaction, self.parents[1])

    async def _go_to(self, interaction: discord.Interaction, name: str, *, push: bool = True):
        sp = await self.cog._get_splice_by_result(name)
        if sp:
            embed, parents = await self.cog._embed_for_splice_return_parents(sp)
            self.current_name = sp.get("result_name") or name
            self.parents = parents[:2]
            self._sync_buttons()
            if interaction.response.is_done():
                await interaction.message.edit(embed=embed, view=self)
            else:
                await interaction.response.edit_message(embed=embed, view=self)
            return

        b = await self.cog._get_base_by_default(name)
        if b:
            embed = await self.cog._embed_for_base(b)
            self.current_name = _pretty_name_from_json(b, name)
            self.parents = []
            self._sync_buttons()
            if interaction.response.is_done():
                await interaction.message.edit(embed=embed, view=self)
            else:
                await interaction.response.edit_message(embed=embed, view=self)
            return

        await interaction.response.send_message(f"No entry found for {name}.", ephemeral=True)


# --------------------
# EchoesDex Cog
# --------------------

class EchoesDex(commands.Cog):
    """EchoesDex: view base pets from JSON and spliced pets from DB."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # --- JSON base pets ---
        # Default expects monsters.json at project root (matches your actual path)
        env_path = os.getenv("MONSTERS_JSON_PATH") or os.getenv("MONSTER_JSON_PATH")

        # If you store the cog elsewhere, env var wins.
        self.monster_json_path = env_path or "/home/ubuntu/FableRPG-FINAL/Fable/monsters.json"

        self._base_list: List[Dict[str, Any]] = []
        self._base_by_key: Dict[str, Dict[str, Any]] = {}
        self._last_json_error: Optional[str] = None
        self._load_monsters_json()

        # Cache whether splice_combinations has a creator/discoverer column
        self._splice_creator_col: Optional[str] = None  # None => unknown, "" => not present, else => column name

        # Cache monster_pets columns needed for "first owner" creator
        self._mp_cols_cached: bool = False
        self._mp_owner_col: str = ""
        self._mp_name_col: str = ""
        self._mp_order_col: str = ""

    # --------------------
    # JSON loader
    # --------------------

    def _normalize_monsters_payload(self, data: Any) -> List[Dict[str, Any]]:
        """
        Supports these formats:
        - [ {...}, {...} ]
        - {"monsters": [ ... ]}
        - {"1": [ ... ], "2": [ ... ]}  (your current dataset)
        """
        # wrapper objects
        if isinstance(data, dict):
            # common wrappers
            for k in ("monsters", "monster", "pets", "data", "items"):
                if k in data and isinstance(data[k], list):
                    data = data[k]
                    break

        # tiered dict: {"1": [..], "2": [..]}
        if isinstance(data, dict):
            out: List[Dict[str, Any]] = []
            # numeric-ish keys first
            keys = list(data.keys())
            def _k_sort(x: str):
                try:
                    return (0, int(x))
                except Exception:
                    return (1, x)
            for kk in sorted(keys, key=_k_sort):
                v = data.get(kk)
                if isinstance(v, list):
                    for item in v:
                        if isinstance(item, dict):
                            # preserve tier if helpful later
                            if "tier" not in item:
                                item = dict(item)
                                item["tier"] = kk
                            out.append(item)
            return out

        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]

        return []

    def _load_monsters_json(self) -> None:
        self._last_json_error = None
        self._base_list = []
        self._base_by_key = {}

        try:
            if not os.path.exists(self.monster_json_path):
                self._last_json_error = f"File not found: {self.monster_json_path}"
                return

            with open(self.monster_json_path, "r", encoding="utf-8") as f:
                raw = json.load(f)

            data = self._normalize_monsters_payload(raw)
            if not data:
                self._last_json_error = "No valid monsters list found in JSON."
                return

            self._base_list = data

            for m in self._base_list:
                nm = (m.get("default_name") or m.get("name") or "").strip()
                if not nm:
                    continue
                self._base_by_key[_key(nm)] = m

            print(f"[EchoesDex] Loaded monsters.json: {len(self._base_by_key)} base pets from {self.monster_json_path}")

        except Exception as e:
            self._last_json_error = f"{type(e).__name__}: {e}"
            print(f"[EchoesDex] Failed to load monsters.json ({self.monster_json_path}): {self._last_json_error}")

    # --------------------
    # Base availability (Wild vs Event/Special)
    # --------------------

    def _is_wild_base(self, m: Dict[str, Any]) -> bool:
        """
        Uses your dataset structure: monsters.json entries include `ispublic`.
        - ispublic: true  => Wild (PvE drop)
        - ispublic: false/missing => Event/Special (cannot be found)

        Also supports a few alternative keys in case you add them later.
        """
        if "ispublic" in m:
            return _truthy(m.get("ispublic"))

        # Future-proof fallback keys
        if "in_wild" in m:
            return _truthy(m.get("in_wild"))
        if "event_only" in m:
            return not _truthy(m.get("event_only"))
        if "not_in_wild" in m:
            return not _truthy(m.get("not_in_wild"))
        if "obtainable" in m:
            return str(m.get("obtainable")).lower() in ("wild", "pve", "drop")

        # Default: be conservative (special) if not explicit
        return False

    # --------------------
    # Creator column auto-detect (splice_combinations)
    # --------------------

    async def _detect_splice_creator_col(self) -> str:
        if self._splice_creator_col is not None:
            return self._splice_creator_col

        candidates = ["creator", "created_by", "discoverer", "discovered_by", "first_splicer", "made_by"]
        found = ""

        try:
            async with self.bot.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema='public' AND table_name='splice_combinations'
                    """
                )
            cols = {r["column_name"] for r in rows}
            for c in candidates:
                if c in cols:
                    found = c
                    break
        except Exception:
            found = ""

        self._splice_creator_col = found
        return found

    # --------------------
    # monster_pets column detect (for first-owner spliced creator)
    # --------------------

    async def _detect_monster_pets_cols(self) -> None:
        if self._mp_cols_cached:
            return

        owner_candidates = ["owner_id", "user_id", "discord_id", "player_id"]
        name_candidates = ["default_name", "pet_default", "monster_default", "name", "pet_name", "monster_name"]
        order_candidates = ["created_at", "obtained_at", "timestamp", "time", "id"]

        try:
            async with self.bot.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema='public' AND table_name='monster_pets'
                    """
                )
            cols = {r["column_name"] for r in rows}

            self._mp_owner_col = next((c for c in owner_candidates if c in cols), "")
            self._mp_name_col = next((c for c in name_candidates if c in cols), "")
            self._mp_order_col = next((c for c in order_candidates if c in cols), "")

            # If we have owner+name but no order, fall back to id if present
            if not self._mp_order_col and "id" in cols:
                self._mp_order_col = "id"

        except Exception:
            self._mp_owner_col = ""
            self._mp_name_col = ""
            self._mp_order_col = ""

        self._mp_cols_cached = True

    async def _get_first_owner_id_for_pet(self, pet_name: str) -> Optional[int]:
        """Returns the first owner's discord id for this pet from monster_pets."""
        await self._detect_monster_pets_cols()
        if not (self._mp_owner_col and self._mp_name_col and self._mp_order_col):
            return None

        raw = _strip_outer_quotes(pet_name).strip().lower()
        async with self.bot.pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT {self._mp_owner_col} AS owner
                FROM monster_pets
                WHERE LOWER({self._mp_name_col}) = $1
                  AND {self._mp_owner_col} IS NOT NULL
                  AND {self._mp_owner_col} <> 0
                ORDER BY {self._mp_order_col} ASC
                LIMIT 1
                """,
                raw,
            )
        if not row or row["owner"] is None:
            return None
        try:
            return int(row["owner"])
        except Exception:
            return None

    async def _count_captured_pets(self, pet_name: str) -> Optional[int]:
        """How many of this pet exist in monster_pets (i.e., captured by players)."""
        await self._detect_monster_pets_cols()
        if not self._mp_name_col:
            return None

        raw = _strip_outer_quotes(pet_name).strip().lower()
        try:
            async with self.bot.pool.acquire() as conn:
                n = await conn.fetchval(
                    f"""
                    SELECT COUNT(*)::bigint
                    FROM monster_pets
                    WHERE LOWER({self._mp_name_col}) = $1
                    """,
                    raw,
                )
            return int(n) if n is not None else 0
        except Exception:
            return None

    async def _count_incubating_eggs(self, pet_name: str) -> Optional[int]:
        """How many eggs of this pet are currently incubating (monster_eggs)."""
        raw = _strip_outer_quotes(pet_name).strip().lower()
        try:
            async with self.bot.pool.acquire() as conn:
                # Try common column names dynamically
                cols = await conn.fetch(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema='public' AND table_name='monster_eggs'
                    """
                )
                colset = {r['column_name'] for r in cols}

                name_col = next((c for c in ["pet_default", "monster_default", "name"] if c in colset), None)
                if not name_col:
                    return None

                n = await conn.fetchval(
                    f"""
                    SELECT COUNT(*)::bigint
                    FROM monster_eggs
                    WHERE LOWER({name_col}) = $1
                    """,
                    raw,
                )
            return int(n) if n is not None else 0
        except Exception:
            return None

        raw = _strip_outer_quotes(pet_name).strip().lower()
        try:
            async with self.bot.pool.acquire() as conn:
                n = await conn.fetchval(
                    f"""
                    SELECT COUNT(*)::bigint
                    FROM monster_pets
                    WHERE LOWER({self._mp_name_col}) = $1
                    """,
                    raw,
                )
            return int(n) if n is not None else 0
        except Exception:
            return None

        raw = _strip_outer_quotes(pet_name).strip().lower()
        async with self.bot.pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT {self._mp_owner_col} AS owner
                FROM monster_pets
                WHERE LOWER({self._mp_name_col}) = $1
                ORDER BY {self._mp_order_col} ASC
                LIMIT 1
                """,
                raw,
            )
        if not row or row["owner"] is None:
            return None
        try:
            return int(row["owner"])
        except Exception:
            return None
    # --------------------
    # DB lookups + creator inference (splice_combinations + splice_requests)
    # --------------------

    async def _infer_creator_from_splice_requests(self, sp: Dict[str, Any]) -> Optional[int]:
        """Infer creator from splice_requests without modifying DB.

        Why your previous match often returns None:
        - splice_requests doesn't store the final result_name; temp_name is user-chosen and often != result.
        - parent strings may include bracket tags like "[UNSTABLE]" or extra whitespace.

        Matching strategy (no DB changes):
        1) Normalize parent defaults by stripping bracket-tags like "[UNSTABLE]" and trimming.
        2) Find the earliest *non-pending* request with matching parents (either order).
           That earliest successful request is considered the "creator" (first splicer).
        3) If none exist, optionally fall back to the earliest pending request (still useful).

        Returns user_id or None.
        """

        def norm(s: str) -> str:
            s = (s or "").strip()
            # Remove bracket tags like " [UNSTABLE]" / "[SPECIAL]" etc.
            s = re.sub(r"\s*\[[^\]]+\]", "", s)
            s = re.sub(r"\s+", " ", s)
            return s.strip().lower()

        p1 = norm(sp.get("pet1_default") or "")
        p2 = norm(sp.get("pet2_default") or "")
        if not (p1 and p2):
            return None

        # tiny in-memory cache to avoid repeated DB hits
        if not hasattr(self, "_creator_cache"):
            self._creator_cache = {}  # type: ignore[attr-defined]
        cache_key = (p1, p2)
        if cache_key in self._creator_cache:  # type: ignore[attr-defined]
            return self._creator_cache[cache_key]  # type: ignore[attr-defined]

        async with self.bot.pool.acquire() as conn:
            # 1) Earliest non-pending match
            row = await conn.fetchrow(
                """
                SELECT user_id
                FROM splice_requests
                WHERE user_id IS NOT NULL
                  AND user_id <> 0
                  AND COALESCE(status, 'pending') <> 'pending'
                  AND (
                        (LOWER(REGEXP_REPLACE(pet1_default, '\s*\[[^\]]+\]', '', 'g')) = $1
                         AND LOWER(REGEXP_REPLACE(pet2_default, '\s*\[[^\]]+\]', '', 'g')) = $2)
                     OR (LOWER(REGEXP_REPLACE(pet1_default, '\s*\[[^\]]+\]', '', 'g')) = $2
                         AND LOWER(REGEXP_REPLACE(pet2_default, '\s*\[[^\]]+\]', '', 'g')) = $1)
                  )
                ORDER BY created_at ASC
                LIMIT 1
                """,
                p1,
                p2,
            )
            if row and row.get("user_id"):
                uid = int(row["user_id"])
                self._creator_cache[cache_key] = uid  # type: ignore[attr-defined]
                return uid

            # 2) Fallback: earliest pending match (still gives you "who attempted first")
            row2 = await conn.fetchrow(
                """
                SELECT user_id
                FROM splice_requests
                WHERE user_id IS NOT NULL
                  AND user_id <> 0
                  AND COALESCE(status, 'pending') = 'pending'
                  AND (
                        (LOWER(REGEXP_REPLACE(pet1_default, '\s*\[[^\]]+\]', '', 'g')) = $1
                         AND LOWER(REGEXP_REPLACE(pet2_default, '\s*\[[^\]]+\]', '', 'g')) = $2)
                     OR (LOWER(REGEXP_REPLACE(pet1_default, '\s*\[[^\]]+\]', '', 'g')) = $2
                         AND LOWER(REGEXP_REPLACE(pet2_default, '\s*\[[^\]]+\]', '', 'g')) = $1)
                  )
                ORDER BY created_at ASC
                LIMIT 1
                """,
                p1,
                p2,
            )
            if row2 and row2.get("user_id"):
                uid = int(row2["user_id"])
                self._creator_cache[cache_key] = uid  # type: ignore[attr-defined]
                return uid

        self._creator_cache[cache_key] = None  # type: ignore[attr-defined]
        return None

        rn = result_name.lower()
        p1l, p2l = p1.lower(), p2.lower()

        # tiny in-memory cache to avoid repeated DB hits
        if not hasattr(self, "_creator_cache"):
            self._creator_cache = {}  # type: ignore[attr-defined]
        cache_key = (rn, p1l, p2l)
        if cache_key in self._creator_cache:  # type: ignore[attr-defined]
            return self._creator_cache[cache_key]  # type: ignore[attr-defined]

        async with self.bot.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT user_id
                FROM splice_requests
                WHERE user_id IS NOT NULL
                  AND user_id <> 0
                  AND status <> 'pending'
                  AND temp_name IS NOT NULL
                  AND LOWER(temp_name) = $1
                  AND (
                        (LOWER(pet1_default) = $2 AND LOWER(pet2_default) = $3)
                     OR (LOWER(pet1_default) = $3 AND LOWER(pet2_default) = $2)
                  )
                ORDER BY created_at ASC
                LIMIT 1
                """,
                rn,
                p1l,
                p2l,
            )
            if row and row.get("user_id"):
                uid = int(row["user_id"])
                self._creator_cache[cache_key] = uid  # type: ignore[attr-defined]
                return uid

            sc_created = sp.get("created_at")
            if sc_created is not None:
                row2 = await conn.fetchrow(
                    """
                    SELECT user_id
                    FROM splice_requests
                    WHERE user_id IS NOT NULL
                      AND user_id <> 0
                      AND status <> 'pending'
                      AND (
                            (LOWER(pet1_default) = $1 AND LOWER(pet2_default) = $2)
                         OR (LOWER(pet1_default) = $2 AND LOWER(pet2_default) = $1)
                      )
                      AND created_at BETWEEN $3 AND $4
                    ORDER BY ABS(EXTRACT(EPOCH FROM (created_at - $5))) ASC
                    LIMIT 1
                    """,
                    p1l,
                    p2l,
                    (sc_created - timedelta(hours=6)),
                    (sc_created + timedelta(hours=6)),
                    sc_created,
                )
                if row2 and row2.get("user_id"):
                    uid = int(row2["user_id"])
                    self._creator_cache[cache_key] = uid  # type: ignore[attr-defined]
                    return uid

        self._creator_cache[cache_key] = None  # type: ignore[attr-defined]
        return None

    # --------------------
    # DB lookups (splice_combinations only)
    # --------------------

    async def _get_splice_by_result(self, name: str) -> Dict[str, Any]:
        creator_col = await self._detect_splice_creator_col()
        creator_select = f", {creator_col} AS creator" if creator_col else ""

        raw = _strip_outer_quotes(name).strip()

        async with self.bot.pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT result_name, pet1_default, pet2_default, hp, attack, defense, element, url, created_at
                {creator_select}
                FROM splice_combinations
                WHERE LOWER(result_name) = $1
                LIMIT 1
                """,
                raw.lower(),
            )
        return _rec(row)

    async def _get_splice_fuzzy(self, name: str) -> Dict[str, Any]:
        creator_col = await self._detect_splice_creator_col()
        creator_select = f", {creator_col} AS creator" if creator_col else ""

        raw = _strip_outer_quotes(name).strip()

        async with self.bot.pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT result_name, pet1_default, pet2_default, hp, attack, defense, element, url, created_at
                {creator_select}
                FROM splice_combinations
                WHERE result_name ILIKE ($1 || '%')
                ORDER BY result_name ASC
                LIMIT 1
                """,
                raw,
            )
        return _rec(row)

    async def _get_parents_for_result(self, name: str) -> List[str]:
        sp = await self._get_splice_by_result(name)
        if not sp:
            return []
        return [p for p in [sp.get("pet1_default"), sp.get("pet2_default")] if p]

    async def _load_splice_keys(self) -> Set[str]:
        async with self.bot.pool.acquire() as conn:
            rows = await conn.fetch("SELECT result_name FROM splice_combinations WHERE result_name IS NOT NULL")
        return {_key(r["result_name"]) for r in rows}

    async def _find_combine(self, p1: str, p2: str) -> Dict[str, Any]:
        creator_col = await self._detect_splice_creator_col()
        creator_select = f", {creator_col} AS creator" if creator_col else ""

        p1 = _strip_outer_quotes(p1).strip()
        p2 = _strip_outer_quotes(p2).strip()

        async with self.bot.pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT result_name, pet1_default, pet2_default, hp, attack, defense, element, url
                {creator_select}
                FROM splice_combinations
                WHERE (pet1_default ILIKE $1 AND pet2_default ILIKE $2)
                   OR (pet1_default ILIKE $2 AND pet2_default ILIKE $1)
                LIMIT 1
                """,
                p1,
                p2,
            )
        return _rec(row)

    async def _count_spliced_results(self) -> int:
        async with self.bot.pool.acquire() as conn:
            n = await conn.fetchval(
                """
                SELECT COUNT(*)::bigint
                FROM splice_combinations
                WHERE result_name IS NOT NULL
                """
            )
        return int(n or 0)

    async def _get_top_splices(self, limit: int) -> List[Dict[str, Any]]:
        """Return strongest unique splices ordered by total power."""
        lim = max(1, min(int(limit or 10), 100))
        async with self.bot.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                WITH ranked AS (
                    SELECT
                        TRIM(result_name) AS result_name,
                        COALESCE(hp, 0)::int AS hp,
                        COALESCE(attack, 0)::int AS attack,
                        COALESCE(defense, 0)::int AS defense,
                        COALESCE(element, 'Unknown') AS element,
                        url,
                        created_at,
                        (COALESCE(hp, 0) + COALESCE(attack, 0) + COALESCE(defense, 0))::int AS power,
                        ROW_NUMBER() OVER (
                            PARTITION BY LOWER(TRIM(result_name))
                            ORDER BY
                                (COALESCE(hp, 0) + COALESCE(attack, 0) + COALESCE(defense, 0)) DESC,
                                created_at DESC NULLS LAST
                        ) AS rn
                    FROM splice_combinations
                    WHERE COALESCE(result_name, '') <> ''
                )
                SELECT result_name, hp, attack, defense, element, url, power
                FROM ranked
                WHERE rn = 1
                ORDER BY power DESC, LOWER(result_name)
                LIMIT $1
                """,
                lim,
            )
        return [dict(r) for r in rows]

    async def _get_random_splice(self) -> Dict[str, Any]:
        creator_col = await self._detect_splice_creator_col()
        creator_select = f", {creator_col} AS creator" if creator_col else ""

        async with self.bot.pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT result_name, pet1_default, pet2_default, hp, attack, defense, element, url, created_at
                {creator_select}
                FROM splice_combinations
                WHERE result_name IS NOT NULL
                ORDER BY RANDOM()
                LIMIT 1
                """
            )
        return _rec(row)

    # --------------------
    # Base lookups (monsters.json only)
    # --------------------

    async def _get_base_by_default(self, name: str) -> Dict[str, Any]:
        direct = self._base_by_key.get(_key(name), {})
        if direct:
            return direct

        # Allow lookup by divine familiar cosmetic names (e.g. Μαγεία).
        familiar_key = resolve_familiar_key(name)
        if familiar_key and familiar_key in DIVINE_FAMILIARS:
            canonical_name = str(DIVINE_FAMILIARS[familiar_key].get("name", "")).strip()
            if canonical_name:
                return self._base_by_key.get(_key(canonical_name), {})
        return {}

    async def _get_base_fuzzy_default(self, name: str) -> Dict[str, Any]:
        prefix = _key(name)
        if not prefix:
            # Fallback for non-latin-only queries (e.g. cosmetic greek names).
            return await self._get_base_by_default(name)
        matches: List[Dict[str, Any]] = []
        for m in self._base_list:
            dn = (m.get("default_name") or m.get("name") or "").strip()
            if dn and _key(dn).startswith(prefix):
                matches.append(m)
        if not matches:
            return {}
        matches.sort(key=lambda x: (_pretty_name_from_json(x, "")).lower())
        return matches[0]

    # --------------------
    # Requirements + Tree (down to bases)
    # --------------------

    async def _collect_bases(self, name: str, splice_keys: Optional[Set[str]] = None,
                             visited: Optional[Set[str]] = None) -> Set[str]:
        if visited is None:
            visited = set()
        k = _key(name)
        if k in visited:
            return set()
        visited.add(k)

        if splice_keys is None:
            splice_keys = await self._load_splice_keys()

        if k not in splice_keys:
            b = await self._get_base_by_default(name)
            return {_pretty_name_from_json(b, name)} if b else set()

        sp = await self._get_splice_by_result(name)
        bases: Set[str] = set()
        for p in [sp.get("pet1_default"), sp.get("pet2_default")]:
            if not p:
                continue
            bases.update(await self._collect_bases(p, splice_keys, visited))
        return bases

    async def _build_tree_lines(self, name: str, splice_keys: Optional[Set[str]] = None,
                                visited: Optional[Set[str]] = None, pad: str = "") -> List[str]:
        if visited is None:
            visited = set()
        if splice_keys is None:
            splice_keys = await self._load_splice_keys()

        k = _key(name)
        if k in visited:
            return [f"{pad}• {name} (loop)"]
        visited.add(k)

        if k not in splice_keys:
            b = await self._get_base_by_default(name)
            if b:
                dn = _pretty_name_from_json(b, name)
                tag = "Wild" if self._is_wild_base(b) else "Event"
                return [f"{pad}• {dn} ({tag})"]
            return [f"{pad}• {name} (unknown)"]

        parents = await self._get_parents_for_result(name)
        lines: List[str] = [f"{pad}• {name}"]
        child_pad = pad + "   "

        for idx, p in enumerate(parents):
            branch = "├─" if idx == 0 and len(parents) > 1 else "└─"
            sub = await self._build_tree_lines(
                p, splice_keys, visited,
                pad=child_pad + ("│  " if branch == "├─" else "   ")
            )
            if sub:
                first = sub[0].strip()
                lines.append(f"{child_pad}{branch} {first}")
                lines.extend(sub[1:])
            else:
                lines.append(f"{child_pad}{branch} • {p}")
        return lines

    # --------------------
    # Reverse tree (descendants)
    # --------------------

    async def _load_children_map(self) -> Dict[str, List[str]]:
        async with self.bot.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT result_name, pet1_default, pet2_default
                FROM splice_combinations
                WHERE result_name IS NOT NULL
                """
            )

        children: Dict[str, List[str]] = {}
        for r in rows:
            child = r["result_name"]
            for p in (r["pet1_default"], r["pet2_default"]):
                if not p:
                    continue
                pk = _key(p)
                if not pk:
                    continue
                children.setdefault(pk, []).append(child)

        for pk in list(children.keys()):
            children[pk] = sorted(set(children[pk]), key=lambda x: (x or "").lower())
        return children

    async def _collect_descendants(self, root_name: str) -> List[str]:
        children_map = await self._load_children_map()
        root = _key(root_name)

        seen: Set[str] = set()
        out: List[str] = []

        stack = list(children_map.get(root, []))
        while stack:
            child = stack.pop()
            ck = _key(child)
            if ck in seen:
                continue
            seen.add(ck)
            out.append(child)
            stack.extend(children_map.get(ck, []))

        out.sort(key=lambda x: (x or "").lower())
        return out

    async def _build_reverse_tree_lines(self, root_name: str, max_nodes: int = 600) -> List[str]:
        children_map = await self._load_children_map()
        root = _strip_outer_quotes(root_name).strip()
        root_key = _key(root)

        lines: List[str] = [f"• {root}"]
        visited: Set[str] = {root_key}
        count = 0

        def walk(node_key: str, prefix: str):
            nonlocal count
            kids = children_map.get(node_key, [])
            if not kids:
                return

            for i, child in enumerate(kids):
                if count >= max_nodes:
                    lines.append(f"{prefix}└─ • … (truncated at {max_nodes} nodes)")
                    return

                last = (i == len(kids) - 1)
                branch = "└─" if last else "├─"
                child_key = _key(child)

                if child_key in visited:
                    lines.append(f"{prefix}{branch} • {child} (loop)")
                    continue

                lines.append(f"{prefix}{branch} • {child}")
                visited.add(child_key)
                count += 1

                next_prefix = prefix + ("   " if last else "│  ")
                walk(child_key, next_prefix)

        walk(root_key, prefix="   ")
        return lines

    # --------------------
    # Embeds
    # --------------------

    async def _get_parent_card(self, name: str) -> Dict[str, Any]:
        base = await self._get_base_by_default(name)
        if base:
            canonical = _pretty_name_from_json(base, name)
            return {
                "name": _display_name_with_cosmetic(canonical),
                "element": base.get("element"),
                "url": base.get("url"),
                "kind": "Wild" if self._is_wild_base(base) else "Event",
            }
        sp = await self._get_splice_by_result(name)
        if sp:
            result_name = sp.get("result_name") or name
            return {
                "name": _display_name_with_cosmetic(result_name),
                "element": sp.get("element"),
                "url": sp.get("url"),
                "kind": "Spliced",
            }
        return {"name": _display_name_with_cosmetic(name), "element": None, "url": None, "kind": "?"}

    async def _embed_for_splice_return_parents(self, sp: Dict[str, Any]) -> Tuple[discord.Embed, List[str]]:
        title = sp.get("result_name") or "?"
        display_title = _display_name_with_cosmetic(title)
        p1 = sp.get("pet1_default") or ""
        p2 = sp.get("pet2_default") or ""
        url = sp.get("url")
        element = sp.get("element") or "—"
        hp, atk, deff = sp.get("hp"), sp.get("attack"), sp.get("defense")

        # Creator logic (no DB changes):
        # 1) If splice_combinations has a creator-like column, use it
        # 2) Else, infer from splice_requests (most reliable for spliced pets)
        # 3) Else, fall back to first non-zero owner in monster_pets (best-effort)
        creator = sp.get("creator")
        if not creator:
            inferred = await self._infer_creator_from_splice_requests(sp)
            if inferred:
                creator = inferred

        if not creator:
            owner_id = await self._get_first_owner_id_for_pet(title)
            if owner_id:
                creator = owner_id

        embed = discord.Embed(
            title=f"📖 EchoesDex: {display_title}",
            color=discord.Color.purple(),
            description="🧬 Spliced Pet",
        )
        if url:
            embed.set_thumbnail(url=url)

        embed.add_field(name="Stats", value=f"HP: {hp} | ATK: {atk} | DEF: {deff}", inline=False)
        embed.add_field(name="Element", value=element, inline=True)

        captured = await self._count_captured_pets(title)
        if captured is not None:
            embed.add_field(name="Owned", value=str(captured), inline=True)

        incubating = await self._count_incubating_eggs(title)
        if incubating is not None:
            embed.add_field(name="Incubating", value=str(incubating), inline=True)

        if creator:
            embed.add_field(name="Creator", value=_as_mention_if_int(creator), inline=True)

        parents_list = [p for p in [p1, p2] if p]
        lines: List[str] = []
        for pname in parents_list:
            pc = await self._get_parent_card(pname)
            n = pc.get("name")
            el = pc.get("element") or "—"
            kind = pc.get("kind") or "—"
            img = pc.get("url")
            if img:
                lines.append(f"• **{n}** *(Element: {el} • {kind})* — [image]({img})")
            else:
                lines.append(f"• **{n}** *(Element: {el} • {kind})*")

        if lines:
            embed.add_field(name="Parents", value="\n".join(lines), inline=False)

        bases = await self._collect_bases(title)
        if bases:
            embed.add_field(name="Required base pets", value=", ".join(sorted(bases)), inline=False)

        return embed, parents_list

    async def _embed_for_base(self, b: Dict[str, Any]) -> discord.Embed:
        title = _pretty_name_from_json(b, "?")
        display_title = _display_name_with_cosmetic(title)
        hp = b.get("hp")
        atk = b.get("attack")
        deff = b.get("defense")
        element = b.get("element") or "—"
        url = b.get("url")

        is_wild = self._is_wild_base(b)
        if is_wild:
            desc = "🌿 Wild Pet — Random PvE drop"
            color = discord.Color.blue()
        else:
            desc = "🎟️ Event / Special Pet — cannot be found"
            color = discord.Color.orange()

        embed = discord.Embed(
            title=f"📖 EchoesDex: {display_title}",
            color=color,
            description=desc,
        )
        if url:
            embed.set_thumbnail(url=url)
        embed.add_field(name="Stats", value=f"HP: {hp} | ATK: {atk} | DEF: {deff}", inline=False)
        embed.add_field(name="Element", value=element, inline=True)

        captured = await self._count_captured_pets(title)
        if captured is not None:
            embed.add_field(name="Owned", value=str(captured), inline=True)

        incubating = await self._count_incubating_eggs(title)
        if incubating is not None:
            embed.add_field(name="Incubating", value=str(incubating), inline=True)

        return embed

    async def _count_user_completed_splices(self, user_id: int) -> int:
        async with self.bot.pool.acquire() as conn:
            n = await conn.fetchval(
                """
                SELECT COUNT(*)::bigint
                FROM splice_requests
                WHERE user_id = $1
                  AND COALESCE(status, 'pending') <> 'pending'
                """,
                user_id,
            )
        return int(n or 0)

    async def _get_user_resolved_splices(self, user_id: int) -> List[Dict[str, Any]]:
        """Completed user requests mapped to canonical splice combinations by parent pair."""
        async with self.bot.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                WITH sc_one AS (
                    SELECT DISTINCT ON (
                        LEAST(
                            LOWER(REGEXP_REPLACE(COALESCE(pet1_default, ''), '\\s*\\[[^\\]]+\\]', '', 'g')),
                            LOWER(REGEXP_REPLACE(COALESCE(pet2_default, ''), '\\s*\\[[^\\]]+\\]', '', 'g'))
                        ),
                        GREATEST(
                            LOWER(REGEXP_REPLACE(COALESCE(pet1_default, ''), '\\s*\\[[^\\]]+\\]', '', 'g')),
                            LOWER(REGEXP_REPLACE(COALESCE(pet2_default, ''), '\\s*\\[[^\\]]+\\]', '', 'g'))
                        )
                    )
                        LEAST(
                            LOWER(REGEXP_REPLACE(COALESCE(pet1_default, ''), '\\s*\\[[^\\]]+\\]', '', 'g')),
                            LOWER(REGEXP_REPLACE(COALESCE(pet2_default, ''), '\\s*\\[[^\\]]+\\]', '', 'g'))
                        ) AS p_lo,
                        GREATEST(
                            LOWER(REGEXP_REPLACE(COALESCE(pet1_default, ''), '\\s*\\[[^\\]]+\\]', '', 'g')),
                            LOWER(REGEXP_REPLACE(COALESCE(pet2_default, ''), '\\s*\\[[^\\]]+\\]', '', 'g'))
                        ) AS p_hi,
                        result_name, hp, attack, defense, element, url, created_at
                    FROM splice_combinations
                    WHERE result_name IS NOT NULL
                      AND COALESCE(pet1_default, '') <> ''
                      AND COALESCE(pet2_default, '') <> ''
                    ORDER BY
                        LEAST(
                            LOWER(REGEXP_REPLACE(COALESCE(pet1_default, ''), '\\s*\\[[^\\]]+\\]', '', 'g')),
                            LOWER(REGEXP_REPLACE(COALESCE(pet2_default, ''), '\\s*\\[[^\\]]+\\]', '', 'g'))
                        ),
                        GREATEST(
                            LOWER(REGEXP_REPLACE(COALESCE(pet1_default, ''), '\\s*\\[[^\\]]+\\]', '', 'g')),
                            LOWER(REGEXP_REPLACE(COALESCE(pet2_default, ''), '\\s*\\[[^\\]]+\\]', '', 'g'))
                        ),
                        created_at DESC NULLS LAST
                ),
                sr_done AS (
                    SELECT
                        id,
                        created_at,
                        LEAST(
                            LOWER(REGEXP_REPLACE(COALESCE(pet1_default, ''), '\\s*\\[[^\\]]+\\]', '', 'g')),
                            LOWER(REGEXP_REPLACE(COALESCE(pet2_default, ''), '\\s*\\[[^\\]]+\\]', '', 'g'))
                        ) AS p_lo,
                        GREATEST(
                            LOWER(REGEXP_REPLACE(COALESCE(pet1_default, ''), '\\s*\\[[^\\]]+\\]', '', 'g')),
                            LOWER(REGEXP_REPLACE(COALESCE(pet2_default, ''), '\\s*\\[[^\\]]+\\]', '', 'g'))
                        ) AS p_hi
                    FROM splice_requests
                    WHERE user_id = $1
                      AND COALESCE(status, 'pending') <> 'pending'
                      AND COALESCE(pet1_default, '') <> ''
                      AND COALESCE(pet2_default, '') <> ''
                )
                SELECT
                    sr.id AS request_id,
                    sr.created_at AS requested_at,
                    sc.result_name,
                    sc.hp,
                    sc.attack,
                    sc.defense,
                    sc.element,
                    sc.url,
                    (COALESCE(sc.hp, 0) + COALESCE(sc.attack, 0) + COALESCE(sc.defense, 0))::int AS power
                FROM sr_done sr
                JOIN sc_one sc
                  ON sr.p_lo = sc.p_lo
                 AND sr.p_hi = sc.p_hi
                ORDER BY sr.created_at ASC
                """,
                user_id,
            )
        return [dict(r) for r in rows]

    async def _count_first_discoveries_from_requests(self, user_id: int) -> int:
        """Fallback discovery metric: first completed requester for each parent pair."""
        async with self.bot.pool.acquire() as conn:
            n = await conn.fetchval(
                """
                WITH sr_done AS (
                    SELECT
                        user_id,
                        created_at,
                        LEAST(
                            LOWER(REGEXP_REPLACE(COALESCE(pet1_default, ''), '\\s*\\[[^\\]]+\\]', '', 'g')),
                            LOWER(REGEXP_REPLACE(COALESCE(pet2_default, ''), '\\s*\\[[^\\]]+\\]', '', 'g'))
                        ) AS p_lo,
                        GREATEST(
                            LOWER(REGEXP_REPLACE(COALESCE(pet1_default, ''), '\\s*\\[[^\\]]+\\]', '', 'g')),
                            LOWER(REGEXP_REPLACE(COALESCE(pet2_default, ''), '\\s*\\[[^\\]]+\\]', '', 'g'))
                        ) AS p_hi
                    FROM splice_requests
                    WHERE user_id IS NOT NULL
                      AND user_id <> 0
                      AND COALESCE(status, 'pending') <> 'pending'
                      AND COALESCE(pet1_default, '') <> ''
                      AND COALESCE(pet2_default, '') <> ''
                ),
                firsts AS (
                    SELECT DISTINCT ON (p_lo, p_hi)
                        user_id
                    FROM sr_done
                    ORDER BY p_lo, p_hi, created_at ASC
                )
                SELECT COUNT(*)::bigint
                FROM firsts
                WHERE user_id = $1
                """,
                user_id,
            )
        return int(n or 0)

    async def _count_new_combinations_found(self, user_id: int) -> int:
        """Best-effort discover count for a user."""
        creator_col = await self._detect_splice_creator_col()
        creator_count = 0

        if creator_col:
            async with self.bot.pool.acquire() as conn:
                n = await conn.fetchval(
                    f"""
                    SELECT COUNT(*)::bigint
                    FROM (
                        SELECT DISTINCT
                            LEAST(
                                LOWER(REGEXP_REPLACE(COALESCE(pet1_default, ''), '\\s*\\[[^\\]]+\\]', '', 'g')),
                                LOWER(REGEXP_REPLACE(COALESCE(pet2_default, ''), '\\s*\\[[^\\]]+\\]', '', 'g'))
                            ) AS p_lo,
                            GREATEST(
                                LOWER(REGEXP_REPLACE(COALESCE(pet1_default, ''), '\\s*\\[[^\\]]+\\]', '', 'g')),
                                LOWER(REGEXP_REPLACE(COALESCE(pet2_default, ''), '\\s*\\[[^\\]]+\\]', '', 'g'))
                            ) AS p_hi
                        FROM splice_combinations
                        WHERE {creator_col} = $1
                          AND result_name IS NOT NULL
                          AND COALESCE(pet1_default, '') <> ''
                          AND COALESCE(pet2_default, '') <> ''
                    ) AS t
                    """,
                    user_id,
                )
            creator_count = int(n or 0)

        if creator_count > 0:
            return creator_count
        return await self._count_first_discoveries_from_requests(user_id)

    def _splicer_title_for(self, total_new_splices: int) -> str:
        """Greek title ladder for Splicer Card progression."""
        n = int(total_new_splices or 0)
        if n >= 100:
            return "Μετενσάρκωση της Νόα"
        if n >= 50:
            return "Ειδήμων Σπλάισερ"
        if n >= 30:
            return "Γνώστης Σπλάισερ"
        if n >= 15:
            return "Μαθητής Σπλάισερ"
        if n >= 5:
            return "Νεόφυτος Σπλάισερ"
        return "Ανυποψίαστος Σπλάισερ"

    def _splicer_title_translation(self, greek_title: str) -> str:
        """English translation for Splicer titles shown in summary embed."""
        return {
            "Ανυποψίαστος Σπλάισερ": "Unaware Splicer",
            "Νεόφυτος Σπλάισερ": "Newbie Splicer",
            "Μαθητής Σπλάισερ": "Student Splicer",
            "Γνώστης Σπλάισερ": "Knowledgeable Splicer",
            "Ειδήμων Σπλάισερ": "Expert Splicer",
            "Μετενσάρκωση της Νόα": "Noa's Reincarnation",
        }.get(greek_title, greek_title)

    def _parse_splicer_subject(
        self,
        author: discord.abc.User,
        subject: Optional[Union[discord.User, str]],
        background: Optional[str],
    ) -> Tuple[discord.abc.User, Optional[str], bool]:
        """Resolve command inputs into target user + requested background."""
        target: discord.abc.User = author
        requested_bg: Optional[str] = background
        explicit_bg_request = isinstance(subject, str) or bool(background)
        if isinstance(subject, discord.User):
            target = subject
        elif isinstance(subject, str):
            requested_bg = f"{subject} {background}".strip() if background else subject
        return target, requested_bg, explicit_bg_request

    async def _resolve_requested_splicer_background(
        self,
        *,
        user_id: int,
        requested_bg: Optional[str],
        explicit_bg_request: bool,
    ) -> Tuple[Optional[str], str, List[str], Optional[str]]:
        """Resolve requested/saved background into a concrete key + source."""
        effective_request = requested_bg
        if not effective_request:
            effective_request = await self._get_user_splicer_background_key(user_id)

        bg_path, bg_key, bg_keys = self._resolve_splicer_background(effective_request)
        if effective_request and not bg_path and explicit_bg_request:
            choices = ", ".join(f"`{k}`" for k in bg_keys[:12]) if bg_keys else "No backgrounds installed yet."
            return (
                None,
                "",
                bg_keys,
                f"❌ Unknown Splicer Card background: `{effective_request}`.\nAvailable: {choices}",
            )

        if not bg_path:
            bg_path, bg_key, bg_keys = self._resolve_splicer_background(None)
        return bg_path, bg_key, bg_keys, None

    async def _build_splicer_profile(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Compute shared summary/card stats for a user."""
        total_splices = await self._count_user_completed_splices(user_id)
        if total_splices <= 0:
            return None

        resolved_rows = await self._get_user_resolved_splices(user_id)
        new_combos = await self._count_new_combinations_found(user_id)
        splicer_title = self._splicer_title_for(new_combos)
        splice_names = sorted(
            {
                (r.get("result_name") or "").strip()
                for r in resolved_rows
                if (r.get("result_name") or "").strip()
            },
            key=lambda x: x.lower(),
        )

        element_counts: Dict[str, int] = {}
        for row in resolved_rows:
            element = (row.get("element") or "Unknown").strip() or "Unknown"
            element_counts[element] = element_counts.get(element, 0) + 1

        best_element = "—"
        best_element_count = 0
        if element_counts:
            best_element, best_element_count = sorted(
                element_counts.items(),
                key=lambda x: (-x[1], x[0].lower()),
            )[0]

        combo_stats: Dict[str, Dict[str, Any]] = {}
        for row in resolved_rows:
            nm = (row.get("result_name") or "").strip()
            if not nm:
                continue

            hp = int(row.get("hp") or 0)
            atk = int(row.get("attack") or 0)
            deff = int(row.get("defense") or 0)
            power = int(row.get("power") or (hp + atk + deff))
            element = (row.get("element") or "Unknown").strip() or "Unknown"
            url = row.get("url")

            if nm not in combo_stats:
                combo_stats[nm] = {
                    "name": nm,
                    "times": 0,
                    "hp": hp,
                    "attack": atk,
                    "defense": deff,
                    "power": power,
                    "element": element,
                    "url": url,
                }

            combo_stats[nm]["times"] += 1
            if power > combo_stats[nm]["power"]:
                combo_stats[nm]["hp"] = hp
                combo_stats[nm]["attack"] = atk
                combo_stats[nm]["defense"] = deff
                combo_stats[nm]["power"] = power
                combo_stats[nm]["element"] = element
                combo_stats[nm]["url"] = url

        best_combo = None
        if combo_stats:
            best_combo = sorted(
                combo_stats.values(),
                key=lambda x: (-x["power"], -x["times"], x["name"].lower()),
            )[0]
            if not _is_valid_button_url(best_combo.get("url")):
                sp_row = await self._get_splice_by_result(best_combo["name"])
                sp_url = (sp_row or {}).get("url")
                if _is_valid_button_url(sp_url):
                    best_combo["url"] = sp_url

        return {
            "total_splices": total_splices,
            "resolved_rows": resolved_rows,
            "new_combos": new_combos,
            "splicer_title": splicer_title,
            "splice_names": splice_names,
            "best_element": best_element,
            "best_element_count": best_element_count,
            "best_combo": best_combo,
        }

    def _get_summary_font(self, size: int, *, title: bool = False) -> ImageFont.ImageFont:
        cache_key = (size, title)
        if not hasattr(self, "_summary_font_cache"):
            self._summary_font_cache = {}  # type: ignore[attr-defined]
        if cache_key in self._summary_font_cache:  # type: ignore[attr-defined]
            return self._summary_font_cache[cache_key]  # type: ignore[attr-defined]

        root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        if title:
            candidates = [
                os.path.join(root, "assets", "fonts", "CaesarDressing-Regular.ttf"),
                os.path.join(root, "assets", "fonts", "GFSDidot-Regular.ttf"),
            ]
        else:
            candidates = [
                os.path.join(root, "assets", "fonts", "GFSDidot-Regular.ttf"),
                os.path.join(root, "assets", "fonts", "CaesarDressing-Regular.ttf"),
            ]

        font: ImageFont.ImageFont
        for path in candidates:
            try:
                font = ImageFont.truetype(path, size=size)
                self._summary_font_cache[cache_key] = font  # type: ignore[attr-defined]
                return font
            except Exception:
                continue

        font = ImageFont.load_default()
        self._summary_font_cache[cache_key] = font  # type: ignore[attr-defined]
        return font

    def _normalize_bg_key(self, value: str) -> str:
        key = (value or "").strip().lower()
        key = re.sub(r"[^a-z0-9]+", "-", key)
        return key.strip("-")

    def _builtin_splicer_backgrounds(self) -> Dict[str, str]:
        """Built-in remote background catalog provided by design."""
        return {
            "1": "https://i.imgur.com/NJgETtd.png",
            "2": "https://i.imgur.com/16Rv0eT.png",
            "3": "https://i.imgur.com/Q4L5sBk.png",
            "4": "https://i.imgur.com/5HS1lJ4.png",
            "5": "https://i.imgur.com/Tr6uUGo.png",
            "6": "https://i.imgur.com/qZgwlmp.png",
            "7": "https://i.imgur.com/ci8P7MB.png",
            "8": "https://i.imgur.com/JRfRHe7.png",
            "9": "https://i.imgur.com/8eIcCFh.png",
            "10": "https://i.imgur.com/hWaGtog.png",
        }

    def _bg_alias_candidates(self, req: str) -> List[str]:
        out = [req]
        if req.startswith("bg") and req[2:].isdigit():
            out.append(req[2:])
        if req.startswith("background-") and req[11:].isdigit():
            out.append(req[11:])
        if req.isdigit():
            out.extend([f"bg{req}", f"background-{req}"])
        return out

    def _label_for_bg_key(self, key: str) -> str:
        if key.isdigit():
            return f"Background {key}"
        return key.replace("-", " ").title()

    def _get_splicer_backgrounds(self) -> Dict[str, str]:
        """Discover available Splicer Card backgrounds from assets."""
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        dirs = [
            os.path.join(root, "assets", "echoesdex", "splicer_card_backgrounds"),
            os.path.join(root, "assets", "echoesdex", "splicer_cards"),
            os.path.join(root, "assets", "echoesdex"),
        ]
        exts = {".png", ".jpg", ".jpeg", ".webp"}

        out: Dict[str, str] = dict(self._builtin_splicer_backgrounds())
        for d in dirs:
            if not os.path.isdir(d):
                continue
            try:
                for fname in sorted(os.listdir(d)):
                    path = os.path.join(d, fname)
                    if not os.path.isfile(path):
                        continue
                    stem, ext = os.path.splitext(fname)
                    if ext.lower() not in exts:
                        continue
                    key = self._normalize_bg_key(stem)
                    if key and key not in out:
                        out[key] = path
            except Exception:
                continue
        return out

    def _splicer_background_catalog(self) -> List[Dict[str, str]]:
        bgs = self._get_splicer_backgrounds()
        num_keys = sorted([k for k in bgs.keys() if k.isdigit()], key=lambda x: int(x))
        other_keys = sorted([k for k in bgs.keys() if not k.isdigit()])
        keys = num_keys + other_keys

        out: List[Dict[str, str]] = []
        for k in keys:
            out.append(
                {
                    "key": k,
                    "label": self._label_for_bg_key(k),
                    "source": bgs[k],
                }
            )
        return out

    def _resolve_splicer_background(self, requested: Optional[str]) -> Tuple[Optional[str], str, List[str]]:
        """Return (path, selected_key, available_keys)."""
        bgs = self._get_splicer_backgrounds()
        num_keys = sorted([k for k in bgs.keys() if k.isdigit()], key=lambda x: int(x))
        other_keys = sorted([k for k in bgs.keys() if not k.isdigit()])
        keys = num_keys + other_keys
        if not bgs:
            return None, "default", []

        if requested:
            req = self._normalize_bg_key(requested)
            for candidate in self._bg_alias_candidates(req):
                if candidate in bgs:
                    return bgs[candidate], candidate, keys

            prefix = next((k for k in keys if k.startswith(req)), None)
            if prefix:
                return bgs[prefix], prefix, keys

            return None, "", keys

        default_key = "1" if "1" in bgs else ("classic" if "classic" in bgs else keys[0])
        return bgs[default_key], default_key, keys

    async def _ensure_splicer_bg_table(self) -> None:
        if getattr(self, "_splicer_bg_table_ready", False):
            return
        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS echoesdex_profile_settings (
                    user_id BIGINT PRIMARY KEY,
                    splicer_background TEXT,
                    updated_at TIMESTAMP DEFAULT NOW()
                )
                """
            )
        self._splicer_bg_table_ready = True

    async def _get_user_splicer_background_key(self, user_id: int) -> Optional[str]:
        await self._ensure_splicer_bg_table()
        async with self.bot.pool.acquire() as conn:
            val = await conn.fetchval(
                """
                SELECT splicer_background
                FROM echoesdex_profile_settings
                WHERE user_id = $1
                """,
                user_id,
            )
        if not val:
            return None
        return self._normalize_bg_key(str(val))

    async def _set_user_splicer_background_key(self, user_id: int, key: str) -> None:
        await self._ensure_splicer_bg_table()
        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO echoesdex_profile_settings (user_id, splicer_background, updated_at)
                VALUES ($1, $2, NOW())
                ON CONFLICT (user_id)
                DO UPDATE SET splicer_background = EXCLUDED.splicer_background, updated_at = NOW()
                """,
                user_id,
                self._normalize_bg_key(key),
            )

    async def _open_splicer_background_picker(self, ctx: commands.Context, user: discord.User) -> None:
        entries = self._splicer_background_catalog()
        if not entries:
            await ctx.send("❌ No Splicer Card backgrounds are available.")
            return

        saved = await self._get_user_splicer_background_key(user.id)
        if not saved:
            _src, saved, _keys = self._resolve_splicer_background(None)

        view = SplicerBackgroundPickerView(self, ctx, user, entries, saved or "1", timeout=60)
        await view.start()

    def _fit_text(self, draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> str:
        text = (text or "").strip()
        if not text:
            return ""
        if draw.textlength(text, font=font) <= max_width:
            return text

        trimmed = text
        while len(trimmed) > 1 and draw.textlength(f"{trimmed}…", font=font) > max_width:
            trimmed = trimmed[:-1]
        return f"{trimmed}…" if trimmed else "…"

    async def _fetch_image_bytes(self, url: Optional[str], *, max_bytes: int = 8_000_000) -> Optional[bytes]:
        if not url or not _is_valid_button_url(url):
            return None

        timeout = 12
        headers = {
            "User-Agent": "EchoesDex/1.0",
            "Accept": "image/*,*/*;q=0.8",
        }

        async def _fetch_with_session(session: Any) -> Optional[bytes]:
            try:
                async with session.get(url, timeout=timeout, headers=headers) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.read()
            except Exception:
                return None
            if not data or len(data) > max_bytes:
                return None
            return data

        session = getattr(self.bot, "session", None)
        if session is not None:
            data = await _fetch_with_session(session)
            if data is not None:
                return data

        def _fetch_with_urllib() -> Optional[bytes]:
            try:
                req = Request(url, headers=headers)
                with urlopen(req, timeout=timeout) as resp:
                    data = resp.read(max_bytes + 1)
            except Exception:
                return None
            if not data or len(data) > max_bytes:
                return None
            return data

        try:
            return await asyncio.to_thread(_fetch_with_urllib)
        except Exception:
            return None

    def _paste_fitted(
        self,
        card: Image.Image,
        data: bytes,
        box: Tuple[int, int, int, int],
        *,
        rounded: int = 0,
        circle: bool = False,
    ) -> bool:
        try:
            with Image.open(BytesIO(data)) as im:
                im = im.convert("RGB")
                target_w = box[2] - box[0]
                target_h = box[3] - box[1]
                if target_w <= 0 or target_h <= 0:
                    return False

                fitted = ImageOps.fit(im, (target_w, target_h), method=Image.Resampling.LANCZOS)

            if circle:
                mask = Image.new("L", (target_w, target_h), 0)
                md = ImageDraw.Draw(mask)
                md.ellipse((0, 0, target_w - 1, target_h - 1), fill=255)
                card.paste(fitted, box[:2], mask)
                return True

            if rounded > 0:
                mask = Image.new("L", (target_w, target_h), 0)
                md = ImageDraw.Draw(mask)
                md.rounded_rectangle((0, 0, target_w - 1, target_h - 1), radius=rounded, fill=255)
                card.paste(fitted, box[:2], mask)
                return True

            card.paste(fitted, box[:2])
            return True
        except Exception:
            return False

    async def _render_summary_card(
        self,
        *,
        target: discord.User,
        total_new_splices: int,
        splicer_title: str,
        best_element: str,
        best_element_count: int,
        best_combo: Optional[Dict[str, Any]],
        background_path: Optional[str] = None,
        background_label: str = "default",
    ) -> Optional[BytesIO]:
        try:
            w, h = 1080, 1760
            card = Image.new("RGBA", (w, h), (225, 221, 214, 255))
            draw = ImageDraw.Draw(card)
            custom_background_loaded = False

            # Load selected background template if provided.
            if background_path and os.path.isfile(background_path):
                try:
                    with Image.open(background_path) as bg:
                        bg = bg.convert("RGBA")
                        bg = ImageOps.fit(bg, (w, h), method=Image.Resampling.LANCZOS)
                        card = bg
                        draw = ImageDraw.Draw(card)
                        custom_background_loaded = True
                except Exception:
                    pass
            elif background_path and _is_valid_button_url(background_path):
                bg_bytes = await self._fetch_image_bytes(background_path, max_bytes=30_000_000)
                if bg_bytes:
                    try:
                        with Image.open(BytesIO(bg_bytes)) as bg:
                            bg = bg.convert("RGBA")
                            bg = ImageOps.fit(bg, (w, h), method=Image.Resampling.LANCZOS)
                            card = bg
                            draw = ImageDraw.Draw(card)
                            custom_background_loaded = True
                    except Exception:
                        pass
            else:
                # Fallback plain template with gold frame.
                draw.rectangle((0, 0, w, h), fill=(228, 225, 218, 255))
                draw.rectangle((40, 40, w - 40, h - 40), outline=(214, 160, 23, 255), width=8)
                draw.rounded_rectangle((70, 70, w - 70, h - 70), radius=20, fill=(244, 241, 234, 200))

            # Unified typography: one heading size + one content size.
            heading_size = 40
            content_size = 30
            title_font = self._get_summary_font(heading_size, title=True)
            heading_font = self._get_summary_font(heading_size)
            content_font = self._get_summary_font(content_size)

            # Soft readability panel only for fallback template.
            if not custom_background_loaded:
                draw.rounded_rectangle((84, 120, w - 84, h - 120), radius=24, fill=(248, 246, 239, 150))

            draw.text((150, 72), "SPLICER CARD", font=title_font, fill=(167, 118, 10, 255))
            draw.text((w - 112, 82), str(background_label).upper(), font=content_font, fill=(141, 98, 10, 240), anchor="ra")

            # Avatar.
            avatar_url = str(target.display_avatar.url)
            avatar_data = await self._fetch_image_bytes(avatar_url)
            avatar_ok = False
            if avatar_data:
                avatar_ok = self._paste_fitted(card, avatar_data, (158, 168, 370, 380), circle=True)
            if not avatar_ok:
                draw.ellipse((158, 168, 370, 380), fill=(128, 132, 140, 255), outline=(197, 154, 53, 120), width=3)
                initial = (target.display_name or "?")[:1].upper()
                draw.text((264, 274), initial, font=content_font, fill=(245, 246, 252, 240), anchor="mm")

            # Two-column info layout:
            # left: Trainer + Splice Record, right: Splicer Title + Most Spliced Element.
            left_x = 110
            right_x = 560
            row1_y = 430
            row2_y = 640

            display_name = target.display_name or str(target)
            dn = self._fit_text(draw, display_name, content_font, 400)
            draw.text((left_x, row1_y), "TRAINER", font=heading_font, fill=(166, 121, 21, 255))
            draw.text((left_x, row1_y + 48), dn, font=content_font, fill=(44, 47, 58, 255))
            draw.text((left_x, row1_y + 104), f"ID #{target.id}", font=content_font, fill=(74, 77, 89, 255))

            draw.text((left_x, row2_y), "SPLICE RECORD", font=heading_font, fill=(166, 121, 21, 255))
            draw.text((left_x + 2, row2_y + 56), "Total New Splices", font=heading_font, fill=(58, 64, 84, 255))
            draw.text((left_x + 2, row2_y + 108), f"{total_new_splices:,}", font=content_font, fill=(27, 31, 41, 255))

            draw.text((right_x, row1_y), "Splicer Title", font=heading_font, fill=(58, 64, 84, 255))
            draw.text(
                (right_x, row1_y + 48),
                self._fit_text(draw, splicer_title, content_font, 400),
                font=content_font,
                fill=(34, 38, 47, 255),
            )

            best_el = best_element if (best_element or "").strip() else "Unknown"
            draw.text((right_x, row2_y), "Most Spliced Element", font=heading_font, fill=(58, 64, 84, 255))
            draw.text((right_x, row2_y + 56), self._fit_text(draw, best_el, content_font, 400), font=content_font, fill=(34, 38, 47, 255))
            draw.text(
                (right_x, row2_y + 108),
                f"{best_element_count:,} splice{'s' if best_element_count != 1 else ''}",
                font=content_font,
                fill=(74, 77, 89, 255),
            )

            # Combo panel.
            draw.text((110, 920), "CURRENT BEST COMBINATION", font=heading_font, fill=(166, 121, 21, 255))
            if best_combo:
                combo_name = self._fit_text(draw, str(best_combo.get("name") or "Unknown"), content_font, 560)
                power = int(best_combo.get("power") or 0)
                hp = int(best_combo.get("hp") or 0)
                atk = int(best_combo.get("attack") or 0)
                deff = int(best_combo.get("defense") or 0)
                times = int(best_combo.get("times") or 0)
                combo_element = str(best_combo.get("element") or "Unknown")

                draw.text((110, 970), combo_name, font=content_font, fill=(28, 32, 40, 255))
                draw.text((110, 1022), f"Power {power:,}", font=content_font, fill=(26, 28, 38, 255))
                draw.text((110, 1074), f"HP {hp}  |  ATK {atk}  |  DEF {deff}", font=content_font, fill=(58, 64, 84, 255))
                draw.text((110, 1116), f"Element: {combo_element}", font=content_font, fill=(74, 77, 89, 255))
                draw.text((110, 1150), f"Times Spliced: {times:,}", font=content_font, fill=(74, 77, 89, 255))

                img_box = (676, 978, 990, 1292)
                combo_ok = False
                combo_url = best_combo.get("url")
                if combo_url and _is_valid_button_url(combo_url):
                    combo_bytes = await self._fetch_image_bytes(combo_url)
                    if combo_bytes:
                        combo_ok = self._paste_fitted(card, combo_bytes, img_box, rounded=16)
                if not combo_ok:
                    draw.rounded_rectangle(img_box, radius=16, fill=(189, 186, 181, 255), outline=(159, 117, 21, 95), width=2)
                    draw.text((833, 1135), "No Image", font=content_font, fill=(84, 88, 97, 255), anchor="mm")
            else:
                draw.text((110, 990), "No resolved combo found yet.", font=content_font, fill=(64, 67, 79, 255))

            draw.text((110, h - 150), "Echoes of Olympus · EchoesDex", font=content_font, fill=(108, 86, 26, 220))

            out = BytesIO()
            card.convert("RGB").save(out, format="PNG", optimize=True)
            out.seek(0)
            return out
        except Exception:
            return None

    # --------------------
    # Commands
    # --------------------

    @commands.group(name="echoesdex", aliases=["ed"], invoke_without_command=True)
    async def echoesdex(self, ctx: commands.Context, *, name: Optional[str] = None):
        if not name:
            return await ctx.send("❌ Please provide a name, or use `echoesdex help`.")

        try:
            name = _strip_outer_quotes(name).strip()

            # exact splice
            sp = await self._get_splice_by_result(name)
            if sp:
                embed, parents = await self._embed_for_splice_return_parents(sp)
                view = DexNav(self, sp.get("result_name") or name, parents)
                return await ctx.send(embed=embed, view=view)

            # exact base (json)
            b = await self._get_base_by_default(name)
            if b:
                embed = await self._embed_for_base(b)
                return await ctx.send(embed=embed)

            # fuzzy splice
            sp = await self._get_splice_fuzzy(name)
            if sp:
                embed, parents = await self._embed_for_splice_return_parents(sp)
                view = DexNav(self, sp.get("result_name") or name, parents)
                return await ctx.send(embed=embed, view=view)

            # fuzzy base
            b = await self._get_base_fuzzy_default(name)
            if b:
                embed = await self._embed_for_base(b)
                return await ctx.send(embed=embed)

            # two tokens => try combine
            parts = name.split()
            if len(parts) == 2 and all(" " not in p for p in parts):
                comb = await self._find_combine(parts[0], parts[1])
                if comb:
                    embed, parents = await self._embed_for_splice_return_parents(comb)
                    view = DexNav(self, comb.get("result_name") or "Splice", parents)
                    return await ctx.send(embed=embed, view=view)

            # if JSON failed to load, tell you
            if not self._base_by_key and self._last_json_error:
                return await ctx.send(
                    f"❌ No EchoesDex entry found for **{name}**.\n"
                    f"⚠️ monsters.json error: `{self._last_json_error}`"
                )

            return await ctx.send(f"❌ No EchoesDex entry found for **{name}**.")

        except Exception as e:
            return await ctx.send(f"❌ EchoesDex error: {type(e).__name__}: {e}")

    @echoesdex.command(name="element")
    async def echoesdex_element(self, ctx: commands.Context, *, element: str):
        """Browse by element (base + spliced), paginated with arrows."""
        try:
            el = _strip_outer_quotes(element).strip()
            el_k = _key(el)

            async with self.bot.pool.acquire() as conn:
                rows_s = await conn.fetch(
                    """
                    SELECT DISTINCT result_name
                    FROM splice_combinations
                    WHERE element ILIKE ($1 || '%')
                    ORDER BY result_name
                    """,
                    el,
                )
            s_names = [r["result_name"] for r in rows_s]

            # Base pets split into Wild vs Event/Special
            wild_b = sorted(
                {
                    _pretty_name_from_json(m, "")
                    for m in self._base_list
                    if (m.get("default_name") or m.get("name"))
                    and _key(m.get("element")).startswith(el_k)
                    and self._is_wild_base(m)
                }
            )
            special_b = sorted(
                {
                    _pretty_name_from_json(m, "")
                    for m in self._base_list
                    if (m.get("default_name") or m.get("name"))
                    and _key(m.get("element")).startswith(el_k)
                    and (not self._is_wild_base(m))
                }
            )

            def chunks(lst: List[str], n: int):
                for i in range(0, len(lst), n):
                    yield lst[i: i + n]

            pages: List[discord.Embed] = []
            s_chunks = list(chunks(s_names, 20))
            w_chunks = list(chunks(wild_b, 20))
            sp_chunks = list(chunks(special_b, 20))
            max_pages = max(len(s_chunks), len(w_chunks), len(sp_chunks), 1)

            for i in range(max_pages):
                s_part = s_chunks[i] if i < len(s_chunks) else []
                w_part = w_chunks[i] if i < len(w_chunks) else []
                sp_part = sp_chunks[i] if i < len(sp_chunks) else []

                embed = discord.Embed(
                    title=f"📚 EchoesDex — Element: {el} (Page {i+1}/{max_pages})",
                    color=discord.Color.gold(),
                )
                embed.add_field(
                    name=f"Spliced ({len(s_names)})",
                    value=("\n".join(f"• {n}" for n in s_part) or "—"),
                    inline=False,
                )
                embed.add_field(
                    name=f"Wild/Base ({len(wild_b)})",
                    value=("\n".join(f"• {n}" for n in w_part) or "—"),
                    inline=False,
                )
                embed.add_field(
                    name=f"Event/Special Base ({len(special_b)})",
                    value=("\n".join(f"• {n}" for n in sp_part) or "—"),
                    inline=False,
                )
                embed.set_footer(text="Use `echoesdex <name>` to open an entry.")
                pages.append(embed)

            if len(pages) == 1:
                return await ctx.send(embed=pages[0])

            view = PagedEmbeds(pages)
            await ctx.send(embed=pages[0], view=view)

        except Exception as e:
            return await ctx.send(f"❌ EchoesDex error: {type(e).__name__}: {e}")

    @echoesdex.command(name="byparent")
    async def echoesdex_byparent(self, ctx: commands.Context, *, parent_name: str):
        """Show all splice results where this pet is one of the parents (paginated with arrows)."""
        try:
            p = _strip_outer_quotes(parent_name).strip()

            async with self.bot.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT DISTINCT result_name
                    FROM splice_combinations
                    WHERE pet1_default ILIKE $1 OR pet2_default ILIKE $1
                    ORDER BY result_name
                    """,
                    p,
                )
            names = [r["result_name"] for r in rows]
            if not names:
                return await ctx.send(f"No splice results found where **{parent_name}** is a parent.")

            def chunks(lst: List[str], n: int):
                for i in range(0, len(lst), n):
                    yield lst[i: i + n]

            pages: List[discord.Embed] = []
            chunk_size = 25
            for part in chunks(names, chunk_size):
                embed = discord.Embed(
                    title=f"👪 EchoesDex — Children of {p}",
                    color=discord.Color.blurple(),
                )
                embed.description = "\n".join(f"• {n}" for n in part)
                embed.set_footer(text="Use `echoesdex <name>` to open an entry.")
                pages.append(embed)

            if len(pages) == 1:
                return await ctx.send(embed=pages[0])

            view = PagedEmbeds(pages)
            await ctx.send(embed=pages[0], view=view)

        except Exception as e:
            return await ctx.send(f"❌ EchoesDex error: {type(e).__name__}: {e}")

    @echoesdex.command(name="splice", aliases=["combine"])
    async def echoesdex_splice(self, ctx: commands.Context, parent1: str, parent2: str):
        """Directly look up the splice result of two parents."""
        try:
            comb = await self._find_combine(parent1, parent2)
            if not comb:
                return await ctx.send(f"❌ No splice result found for **{parent1} + {parent2}**.")
            embed, parents = await self._embed_for_splice_return_parents(comb)
            view = DexNav(self, comb.get("result_name") or "Splice", parents)
            await ctx.send(embed=embed, view=view)
        except Exception as e:
            return await ctx.send(f"❌ EchoesDex error: {type(e).__name__}: {e}")

    @echoesdex.command(name="random")
    async def echoesdex_random(self, ctx: commands.Context):
        """Show a random pet from EchoesDex sources (spliced DB + base JSON)."""
        try:
            base_candidates = [m for m in self._base_list if (m.get("default_name") or m.get("name"))]
            base_count = len(base_candidates)
            splice_count = await self._count_spliced_results()
            total = base_count + splice_count

            if total == 0:
                if not self._base_by_key and self._last_json_error:
                    return await ctx.send(
                        "❌ EchoesDex has no entries loaded.\n"
                        f"⚠️ monsters.json error: `{self._last_json_error}`"
                    )
                return await ctx.send("❌ EchoesDex has no entries to choose from.")

            pick_splice = splice_count > 0 and random.randrange(total) < splice_count

            if pick_splice:
                sp = await self._get_random_splice()
                if sp:
                    embed, parents = await self._embed_for_splice_return_parents(sp)
                    view = DexNav(self, sp.get("result_name") or "Splice", parents)
                    return await ctx.send(embed=embed, view=view)

            if base_count > 0:
                b = random.choice(base_candidates)
                embed = await self._embed_for_base(b)
                return await ctx.send(embed=embed)

            sp = await self._get_random_splice()
            if sp:
                embed, parents = await self._embed_for_splice_return_parents(sp)
                view = DexNav(self, sp.get("result_name") or "Splice", parents)
                return await ctx.send(embed=embed, view=view)

            return await ctx.send("❌ Could not pick a random EchoesDex entry right now.")

        except Exception as e:
            return await ctx.send(f"❌ EchoesDex error: {type(e).__name__}: {e}")

    @echoesdex.command(name="rank", aliases=["ranking", "top"])
    async def echoesdex_rank(self, ctx: commands.Context, top_n: Optional[int] = 10):
        """Show top N strongest unique splices by power (HP+ATK+DEF)."""
        try:
            n = int(top_n or 10)
            if n <= 0:
                return await ctx.send("❌ Please provide a positive number, e.g. `ed rank 10`.")
            n = min(n, 100)

            rows = await self._get_top_splices(n)
            if not rows:
                return await ctx.send("❌ No spliced results available yet.")

            lines: List[str] = []
            for i, row in enumerate(rows, start=1):
                name = str(row.get("result_name") or "?").strip()
                display_name = _display_name_with_cosmetic(name)
                power = int(row.get("power") or 0)
                hp = int(row.get("hp") or 0)
                atk = int(row.get("attack") or 0)
                deff = int(row.get("defense") or 0)
                element = str(row.get("element") or "Unknown").strip() or "Unknown"
                lines.append(
                    f"`#{i:>2}` **{display_name}** — Power **{power:,}** "
                    f"(HP {hp} / ATK {atk} / DEF {deff}) · {element}"
                )

            pages: List[discord.Embed] = []
            chunk = 10
            total_pages = (len(lines) + chunk - 1) // chunk
            for page_i in range(total_pages):
                part = lines[page_i * chunk:(page_i + 1) * chunk]
                embed = discord.Embed(
                    title=f"🏆 EchoesDex Top {len(rows)} Splices",
                    description="\n".join(part),
                    color=discord.Color.gold(),
                )
                top_row = rows[0] if rows else {}
                top_img = top_row.get("url")
                if page_i == 0 and _is_valid_button_url(top_img):
                    embed.set_thumbnail(url=top_img)
                embed.set_footer(text=f"Ranked by power (HP+ATK+DEF) • Page {page_i + 1}/{total_pages}")
                pages.append(embed)

            if len(pages) == 1:
                view = discord.ui.View()
                if _is_valid_button_url(rows[0].get("url")):
                    view.add_item(discord.ui.Button(label="Open #1 Image", url=rows[0]["url"]))
                if view.children:
                    return await ctx.send(embed=pages[0], view=view)
                return await ctx.send(embed=pages[0])

            view = PagedEmbeds(pages)
            await ctx.send(embed=pages[0], view=view)

        except Exception as e:
            return await ctx.send(f"❌ EchoesDex error: {type(e).__name__}: {e}")

    @echoesdex.command(name="splicelist")
    async def echoesdex_splicelist(self, ctx: commands.Context, user: Optional[discord.User] = None):
        """List a user's completed splice results alphabetically with a dropdown selector."""
        try:
            target = user or ctx.author
            rows = await self._get_user_resolved_splices(target.id)
            names = sorted(
                {
                    (r.get("result_name") or "").strip()
                    for r in rows
                    if (r.get("result_name") or "").strip()
                },
                key=lambda x: x.lower(),
            )

            if not names:
                return await ctx.send(f"❌ No resolved splice entries found for {target.mention}.")

            view = UserSpliceListView(self, ctx, target, names)
            await view.start()

        except Exception as e:
            return await ctx.send(f"❌ EchoesDex error: {type(e).__name__}: {e}")

    @echoesdex.command(name="background", aliases=["bg", "theme", "cardbg", "splicerbg"])
    async def echoesdex_background(self, ctx: commands.Context):
        """Choose your Splicer Card background with arrows + dropdown."""
        try:
            await self._open_splicer_background_picker(ctx, ctx.author)

        except Exception as e:
            return await ctx.send(f"❌ EchoesDex error: {type(e).__name__}: {e}")

    @echoesdex.command(name="summary")
    async def echoesdex_summary(
        self,
        ctx: commands.Context,
        subject: Optional[Union[discord.User, str]] = None,
        background: Optional[str] = None,
    ):
        """Show an embed-only Splicer summary with avatar + best pet image."""
        try:
            target, requested_bg, explicit_bg_request = self._parse_splicer_subject(ctx.author, subject, background)
            bg_path, bg_key, _bg_keys, bg_error = await self._resolve_requested_splicer_background(
                user_id=target.id,
                requested_bg=requested_bg,
                explicit_bg_request=explicit_bg_request,
            )
            if bg_error:
                return await ctx.send(bg_error)

            profile = await self._build_splicer_profile(target.id)
            if not profile:
                return await ctx.send(f"❌ No completed splices found for {target.mention}.")

            new_combos = int(profile.get("new_combos") or 0)
            splicer_title = str(profile.get("splicer_title") or "—")
            splice_names = list(profile.get("splice_names") or [])
            best_element = str(profile.get("best_element") or "—")
            best_element_count = int(profile.get("best_element_count") or 0)
            best_combo = profile.get("best_combo")

            embed = discord.Embed(
                title="📟 Splicer Summary",
                description=f"Splicer profile for {target.mention}",
                color=discord.Color.gold(),
            )
            embed.set_author(name=str(target), icon_url=target.display_avatar.url)
            embed.set_thumbnail(url=target.display_avatar.url)

            embed.add_field(
                name="Splice Record",
                value=f"Total New Splices: **{new_combos:,}**",
                inline=False,
            )
            translated_title = self._splicer_title_translation(splicer_title)
            title_value = splicer_title
            if translated_title and translated_title != splicer_title:
                title_value = f"{splicer_title}\n({translated_title})"
            embed.add_field(name="Splicer Title", value=title_value, inline=False)
            embed.add_field(name="Card Theme", value=f"`{bg_key}`", inline=True)

            if best_element_count > 0:
                embed.add_field(
                    name="Most Spliced Element",
                    value=f"**{best_element}** ({best_element_count:,} splice{'s' if best_element_count != 1 else ''})",
                    inline=True,
                )
            else:
                embed.add_field(name="Most Spliced Element", value="—", inline=True)

            if best_combo:
                combo_name = best_combo["name"]
                prefix = ctx.clean_prefix
                dex_cmd = f'{prefix}echoesdex "{combo_name}"'

                combo_text = (
                    f"**{combo_name}**\n"
                    f"Power: **{best_combo['power']:,}**"
                    f" (HP {best_combo['hp']} / ATK {best_combo['attack']} / DEF {best_combo['defense']})\n"
                    f"Times Spliced: **{best_combo['times']:,}**\n"
                    f"EchoesDex: `{dex_cmd}`"
                )
                if best_combo.get("url") and _is_valid_button_url(best_combo["url"]):
                    combo_text += f"\n[Image Link]({best_combo['url']})"
                    embed.set_image(url=best_combo["url"])

                embed.add_field(
                    name="Current Best Splice Combination",
                    value=combo_text,
                    inline=False,
                )
            else:
                embed.add_field(
                    name="Current Best Splice Combination",
                    value="No resolved splice combination available yet.",
                    inline=False,
                )

            embed.set_footer(text="Splicer Summary")

            view = discord.ui.View()
            if splice_names:
                list_label = f"{target.display_name}'s Splice List"
                if len(list_label) > 80:
                    list_label = list_label[:79]
                list_btn = discord.ui.Button(
                    label=f"Open {list_label}",
                    emoji="🧾",
                    style=discord.ButtonStyle.primary,
                )

                async def list_btn_callback(interaction: discord.Interaction):
                    if interaction.user.id != ctx.author.id:
                        return await interaction.response.send_message("This card is not yours.", ephemeral=True)
                    list_view = UserSpliceListView(self, ctx, target, splice_names)
                    list_view.message = interaction.message
                    await interaction.response.edit_message(embed=list_view._build_list_embed(), view=list_view)

                list_btn.callback = list_btn_callback
                view.add_item(list_btn)

            base_url = str(getattr(self.bot, "BASE_URL", "") or "").strip().rstrip("/")
            if base_url and not _is_valid_button_url(base_url):
                candidate = f"https://{base_url}"
                if _is_valid_button_url(candidate):
                    base_url = candidate
                else:
                    base_url = ""

            if base_url and _is_valid_button_url(base_url):
                view.add_item(discord.ui.Button(label="Open EchoesDex Page", url=base_url))
            if best_combo and best_combo.get("url") and _is_valid_button_url(best_combo["url"]):
                view.add_item(discord.ui.Button(label="Open Best Pet Image", url=best_combo["url"]))

            if view.children:
                await ctx.send(embed=embed, view=view)
            else:
                await ctx.send(embed=embed)

        except Exception as e:
            return await ctx.send(f"❌ EchoesDex error: {type(e).__name__}: {e}")

    @echoesdex.group(name="card", aliases=["splicercard"], invoke_without_command=True)
    async def echoesdex_card(
        self,
        ctx: commands.Context,
        subject: Optional[Union[discord.User, str]] = None,
        background: Optional[str] = None,
    ):
        """Show only the Pillow-rendered Splicer Card image."""
        try:
            target, requested_bg, explicit_bg_request = self._parse_splicer_subject(ctx.author, subject, background)
            bg_path, bg_key, _bg_keys, bg_error = await self._resolve_requested_splicer_background(
                user_id=target.id,
                requested_bg=requested_bg,
                explicit_bg_request=explicit_bg_request,
            )
            if bg_error:
                return await ctx.send(bg_error)

            profile = await self._build_splicer_profile(target.id)
            if not profile:
                return await ctx.send(f"❌ No completed splices found for {target.mention}.")

            summary_card = await self._render_summary_card(
                target=target,
                total_new_splices=int(profile.get("new_combos") or 0),
                splicer_title=str(profile.get("splicer_title") or "—"),
                best_element=str(profile.get("best_element") or "—"),
                best_element_count=int(profile.get("best_element_count") or 0),
                best_combo=profile.get("best_combo"),
                background_path=bg_path,
                background_label=bg_key,
            )
            if summary_card is None:
                return await ctx.send("❌ Could not render Splicer Card right now.")

            summary_file = discord.File(fp=summary_card, filename="echoesdex-splicer-card.png")
            await ctx.send(file=summary_file)

        except Exception as e:
            return await ctx.send(f"❌ EchoesDex error: {type(e).__name__}: {e}")

    @echoesdex_card.command(name="bg", aliases=["background", "theme"])
    async def echoesdex_card_bg(self, ctx: commands.Context):
        """Open the card background picker (1-minute timeout)."""
        try:
            await self._open_splicer_background_picker(ctx, ctx.author)
        except Exception as e:
            return await ctx.send(f"❌ EchoesDex error: {type(e).__name__}: {e}")

    @echoesdex.command(name="tree")
    async def echoesdex_tree(self, ctx: commands.Context, *, name: str):
        """Show a unicode lineage tree from the target down to base pets."""
        try:
            name = _strip_outer_quotes(name).strip()
            lines = await self._build_tree_lines(name)
            if not lines:
                return await ctx.send("❌ No lineage found.")

            pages: List[discord.Embed] = []
            buf: List[str] = []
            cur = 0
            for line in lines:
                if cur + len(line) + 1 > 1900:
                    pages.append(
                        discord.Embed(
                            title=f"🌳 EchoesDex Lineage: {name}",
                            description="\n".join(buf),
                            color=discord.Color.green(),
                        )
                    )
                    buf = []
                    cur = 0
                buf.append(line)
                cur += len(line) + 1

            if buf:
                pages.append(
                    discord.Embed(
                        title=f"🌳 EchoesDex Lineage: {name}",
                        description="\n".join(buf),
                        color=discord.Color.green(),
                    )
                )

            if len(pages) == 1:
                return await ctx.send(embed=pages[0])

            view = PagedEmbeds(pages)
            await ctx.send(embed=pages[0], view=view)

        except Exception as e:
            return await ctx.send(f"❌ EchoesDex error: {type(e).__name__}: {e}")

    @echoesdex.command(name="reversetree", aliases=["descendantstree", "familytree"])
    async def echoesdex_reversetree(self, ctx: commands.Context, *, name: str):
        """Show a unicode descendants tree: everything descended from a pet."""
        try:
            name = _strip_outer_quotes(name).strip()

            # Use canonical casing from JSON if base exists
            b = await self._get_base_by_default(name)
            if b:
                name = _pretty_name_from_json(b, name)

            lines = await self._build_reverse_tree_lines(name, max_nodes=600)
            if not lines or len(lines) == 1:
                return await ctx.send(f"❌ No descendants found for **{name}**.")

            pages: List[discord.Embed] = []
            buf: List[str] = []
            cur = 0
            for line in lines:
                if cur + len(line) + 1 > 1900:
                    pages.append(
                        discord.Embed(
                            title=f"🧬 EchoesDex Descendants Tree: {name}",
                            description="\n".join(buf),
                            color=discord.Color.dark_teal(),
                        )
                    )
                    buf = []
                    cur = 0
                buf.append(line)
                cur += len(line) + 1

            if buf:
                pages.append(
                    discord.Embed(
                        title=f"🧬 EchoesDex Descendants Tree: {name}",
                        description="\n".join(buf),
                        color=discord.Color.dark_teal(),
                    )
                )

            if len(pages) == 1:
                return await ctx.send(embed=pages[0])

            view = PagedEmbeds(pages)
            await ctx.send(embed=pages[0], view=view)

        except Exception as e:
            return await ctx.send(f"❌ EchoesDex error: {type(e).__name__}: {e}")

    @echoesdex.command(name="descendants", aliases=["childrenof", "familyof"])
    async def echoesdex_descendants(self, ctx: commands.Context, *, name: str):
        """Flat list of all descendants (paginated)."""
        try:
            name = _strip_outer_quotes(name).strip()

            b = await self._get_base_by_default(name)
            if b:
                name = _pretty_name_from_json(b, name)

            desc = await self._collect_descendants(name)
            if not desc:
                return await ctx.send(f"❌ No descendants found for **{name}**.")

            chunk = 30
            pages: List[discord.Embed] = []
            for i in range(0, len(desc), chunk):
                part = desc[i: i + chunk]
                embed = discord.Embed(
                    title=f"🧬 EchoesDex — Descendants of {name} ({len(desc)})",
                    description="\n".join(f"• {n}" for n in part),
                    color=discord.Color.dark_teal(),
                )
                embed.set_footer(text="Use `echoesdex <name>` to open an entry.")
                pages.append(embed)

            if len(pages) == 1:
                return await ctx.send(embed=pages[0])

            view = PagedEmbeds(pages)
            await ctx.send(embed=pages[0], view=view)

        except Exception as e:
            return await ctx.send(f"❌ EchoesDex error: {type(e).__name__}: {e}")

    # --------- Debug / maintenance ---------

    @echoesdex.command(name="debug")
    async def echoesdex_debug(self, ctx: commands.Context, *, name: str):
        """Debug a lookup: tells you whether the name exists in JSON and/or splices."""
        name = _strip_outer_quotes(name).strip()
        k = _key(name)

        base = await self._get_base_by_default(name)
        sp = await self._get_splice_by_result(name)

        embed = discord.Embed(title="🛠️ EchoesDex Debug", color=discord.Color.orange())
        embed.add_field(name="Input", value=f"`{name}`", inline=False)
        embed.add_field(name="Key", value=f"`{k}`", inline=False)
        embed.add_field(name="monsters.json path", value=f"`{self.monster_json_path}`", inline=False)
        embed.add_field(name="Loaded base pets", value=str(len(self._base_by_key)), inline=True)
        embed.add_field(name="JSON error", value=(self._last_json_error or "—"), inline=True)

        embed.add_field(name="Base match", value=("✅ yes" if base else "❌ no"), inline=True)
        embed.add_field(name="Splice match", value=("✅ yes" if sp else "❌ no"), inline=True)

        if base:
            embed.add_field(name="Base name found", value=_pretty_name_from_json(base, name), inline=False)
            embed.add_field(name="Base type", value=("Wild" if self._is_wild_base(base) else "Event/Special"), inline=False)
        if sp:
            embed.add_field(name="Splice result found", value=sp.get("result_name") or "?", inline=False)

        # monster_pets detection status
        await self._detect_monster_pets_cols()
        embed.add_field(
            name="monster_pets columns",
            value=f"owner={self._mp_owner_col or '—'}, name={self._mp_name_col or '—'}, order={self._mp_order_col or '—'}",
            inline=False,
        )

        await ctx.send(embed=embed)

    @echoesdex.command(name="reloadjson")
    @commands.is_owner()
    async def echoesdex_reloadjson(self, ctx: commands.Context):
        """Reload monsters.json from disk."""
        self._load_monsters_json()
        if self._last_json_error:
            return await ctx.send(f"⚠️ Reloaded, but monsters.json error: `{self._last_json_error}`")
        await ctx.send(f"✅ Reloaded monsters.json ({len(self._base_by_key)} base pets).")

    # --------- Help ---------

    @echoesdex.command(name="help")
    async def echoesdex_help(self, ctx: commands.Context):
        embed = discord.Embed(
            title="📚 EchoesDex — Help",
            description="Commands:",
            color=discord.Color.teal(),
        )
        embed.add_field(
            name="🔍 Lookup",
            value=(
                "`echoesdex <name>` — Show info about a pet (base from monsters.json or spliced from DB).\n"
                "If you type two names, it tries to look up the splice result."
            ),
            inline=False,
        )
        embed.add_field(
            name="✨ Element",
            value=(
                "`echoesdex element <element>` — Browse pets by element (spliced + wild base + event/special base), with ◀ ▶ pages."
            ),
            inline=False,
        )
        embed.add_field(
            name="👪 By Parent",
            value="`echoesdex byparent <name>` — Show splice results where this pet is a parent, with ◀ ▶ pages.",
            inline=False,
        )
        embed.add_field(
            name="⚗️ Splice",
            value=(
                "`echoesdex splice <parent1> <parent2>` — Look up result of splicing two parents.\n"
                "Alias: `echoesdex combine`."
            ),
            inline=False,
        )
        embed.add_field(
            name="🎲 Random",
            value="`echoesdex random` — Show a random pet from EchoesDex.",
            inline=False,
        )
        embed.add_field(
            name="🏆 Rank",
            value="`echoesdex rank [n]` — Show top N strongest splices (example: `ed rank 10`).",
            inline=False,
        )
        embed.add_field(
            name="🌳 Lineage Tree",
            value="`echoesdex tree <name>` — Tree from target down to required base pets (tags: Wild/Event).",
            inline=False,
        )
        embed.add_field(
            name="🧬 Reverse Tree",
            value="`echoesdex reversetree <name>` — Tree of everything descended from a pet.",
            inline=False,
        )
        embed.add_field(
            name="📜 Descendants List",
            value="`echoesdex descendants <name>` — Flat list of all descendants (paginated).",
            inline=False,
        )
        embed.add_field(
            name="🧾 Splice List",
            value="`echoesdex splicelist [@user]` — Alphabetical splice list with a dropdown selector.",
            inline=False,
        )
        embed.add_field(
            name="🎨 Background",
            value=(
                "`echoesdex background` — Open the Splicer Card background picker "
                "(arrows + dropdown + save).\n"
                "`echoesdex card bg` — Same picker from the card command.\n"
                "Alias: `echoesdex splicercard bg`."
            ),
            inline=False,
        )
        embed.add_field(
            name="📟 Summary",
            value=(
                "`echoesdex summary [@user] [theme]` — Embed-only summary (avatar thumbnail + best pet image).\n"
                "`echoesdex card [@user] [theme]` — Pillow Splicer Card image using selected background theme.\n"
                "Alias: `echoesdex splicercard [@user] [theme]`.\n"
                "Drop backgrounds into `assets/echoesdex/splicer_card_backgrounds/`."
            ),
            inline=False,
        )
        embed.add_field(
            name="🛠️ Debug",
            value="`echoesdex debug <name>` — Shows whether the name is found in JSON and/or splices + detected columns.",
            inline=False,
        )
        embed.set_footer(text="Tip: `ed` is an alias for `echoesdex`.")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(EchoesDex(bot))
