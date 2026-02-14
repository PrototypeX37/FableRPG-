import os
import json
import re
import unicodedata
import discord
from datetime import timedelta
from discord.ext import commands
import asyncpg
from typing import Optional, Dict, Any, List, Set, Tuple

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

    # --------------------
    # Base lookups (monsters.json only)
    # --------------------

    async def _get_base_by_default(self, name: str) -> Dict[str, Any]:
        return self._base_by_key.get(_key(name), {})

    async def _get_base_fuzzy_default(self, name: str) -> Dict[str, Any]:
        prefix = _key(name)
        if not prefix:
            return {}
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
            return {
                "name": _pretty_name_from_json(base, name),
                "element": base.get("element"),
                "url": base.get("url"),
                "kind": "Wild" if self._is_wild_base(base) else "Event",
            }
        sp = await self._get_splice_by_result(name)
        if sp:
            return {
                "name": sp.get("result_name") or name,
                "element": sp.get("element"),
                "url": sp.get("url"),
                "kind": "Spliced",
            }
        return {"name": name, "element": None, "url": None, "kind": "?"}

    async def _embed_for_splice_return_parents(self, sp: Dict[str, Any]) -> Tuple[discord.Embed, List[str]]:
        title = sp.get("result_name") or "?"
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
            title=f"📖 EchoesDex: {title}",
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
            title=f"📖 EchoesDex: {title}",
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

    # --------------------
    # Commands
    # --------------------

    @commands.group(name="echoesdex", invoke_without_command=True)
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
            name="🛠️ Debug",
            value="`echoesdex debug <name>` — Shows whether the name is found in JSON and/or splices + detected columns.",
            inline=False,
        )
        embed.set_footer(text="Tip: Entry embeds have Parent buttons + a Back button.")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(EchoesDex(bot))
