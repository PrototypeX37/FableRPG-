
"""GreekCard cog

What this cog provides
- $greekcard / $gc / $p [@user|id|discordtag] : renders the GreekCard image
- $gcbg <direct i.imgur.com link ending with .png/.jpg/.jpeg/.webp> : set background
- $gcbg (with image attachment) : set background from upload
- $gcbg view : show current background
- $gcbg remove : clear to default
- $gctext <color|reset> : set text color on your GreekCard
- $gcbadges [choices|reset] : view/set badges to display (max 4)

Requirements
- Table: greekcard_settings (created separately via psql)
- Fonts already downloaded on server:
  assets/fonts/CaesarDressing-Regular.ttf
  assets/fonts/GFSDidot-Regular.ttf
  assets/fonts/GFSDidot-Bold.ttf (optional but recommended)

Notes
- Background positions are NOT customizable; only background image is.
- Imgur links must be DIRECT: https://i.imgur.com/<id>.png|jpg|jpeg|webp
- Uses bot.trusted_session for HTTP (aiohttp session).

Fixes included
- URL sanitizing for Discord-formatted links (<...>)
- Background fetch validation by decoding bytes with PIL (not just HEAD)
- Adds browser-like headers for Imgur reliability
- Pet image aligned with avatar column (left), pet text shifted right for spacing
- Weapons display name + total (damage+armor)
"""

import io
import re
import time
from typing import Optional, Dict, List, Set, Tuple

import discord
from discord.ext import commands
from discord.ext.commands import BucketType
from classes.badges import Badge
from utils import misc as rpgtools
from utils import colors as color_utils


from PIL import Image, ImageDraw, ImageFont


# ============================
# USER CONFIG (EDIT HERE)
# ============================

# 1) Default background (DIRECT image URL). Must end with .png/.jpg/.jpeg/.webp
DEFAULT_BG_URL = "https://i.imgur.com/n1VMkAw.jpeg"

# 2) Card size (matches your layout). Make background images this size for best results.
CARD_W, CARD_H = 1100, 650

# 3) Local font paths
FONT_TITLE_PATH = "assets/fonts/CaesarDressing-Regular.ttf"
FONT_TEXT_PATH = "assets/fonts/GFSDidot-Regular.ttf"
FONT_TEXT_BOLD_PATH = "assets/fonts/GFSDidot-Bold.ttf"

# 4) Badge metadata
BADGE_DEFINITIONS = {
    "pvp": {
        "name": "Most PvP Wins",
        "icon": "https://i.imgur.com/RiZ8Xd3.png",
        "bit": 0,
    },
    "richest": {
        "name": "Richest",
        "icon": "https://i.imgur.com/mTil6hG.png",
        "bit": 1,
    },
    "splicer": {
        "name": "Master Splicer",
        "icon": "https://i.imgur.com/q8CyQLz.png",
        "bit": 2,
    },
    "lover": {
        "name": "The Lover",
        "icon": "https://i.imgur.com/tvbXKW3.png",
        "bit": 3,
    },
    "enigma_champion": {
        "name": "Apollo's Enigma Champion",
        "icon": "https://i.imgur.com/CDwnFiG.png",
        "bit": None,
        "profile_badge": "ENIGMA_CHAMPION",
    },
    "divine_favor": {
        "name": "Bearer of Divine Favor",
        "icon": "https://i.imgur.com/GGajR4O.png",
        "bit": None,
    },
    "agon_top1": {
        "name": "Agon Absolute #1",
        "icon": "https://i.imgur.com/QUuSrK8.png",
        "bit": None,
    },
    "agon_top2": {
        "name": "Agon Absolute #2",
        "icon": "https://i.imgur.com/DERvk1g.png",
        "bit": None,
    },
    "agon_top3": {
        "name": "Agon Absolute #3",
        "icon": "https://i.imgur.com/PGMsl0r.png",
        "bit": None,
    },
}
BADGE_ORDER = [
    "pvp",
    "richest",
    "splicer",
    "lover",
    "enigma_champion",
    "divine_favor",
    "agon_top1",
    "agon_top2",
    "agon_top3",
]

BADGE_ALIASES = {
    "1": "pvp",
    "pvp": "pvp",
    "pvpwins": "pvp",
    "arena": "pvp",
    "2": "richest",
    "richest": "richest",
    "rich": "richest",
    "money": "richest",
    "3": "splicer",
    "splicer": "splicer",
    "splice": "splicer",
    "4": "lover",
    "lover": "lover",
    "love": "lover",
    "lovescore": "lover",
    "5": "enigma_champion",
    "enigma": "enigma_champion",
    "enigmachampion": "enigma_champion",
    "apolloenigma": "enigma_champion",
    "apolloenigmachampion": "enigma_champion",
    "6": "divine_favor",
    "divinefavor": "divine_favor",
    "divinefavour": "divine_favor",
    "divine": "divine_favor",
    "bearerofdivinefavor": "divine_favor",
    "bearerofdivinefavour": "divine_favor",
    "favor": "divine_favor",
    "favour": "divine_favor",
    "6": "agon_top1",
    "agontop1": "agon_top1",
    "agon1": "agon_top1",
    "7": "agon_top2",
    "agontop2": "agon_top2",
    "agon2": "agon_top2",
    "8": "agon_top3",
    "agontop3": "agon_top3",
    "agon3": "agon_top3",
}

AGON_POOL_LABELS = {
    1: "Chrysos (Pool A)",
    2: "Argyros (Pool B)",
    3: "Chalkos (Pool C)",
}

# 5) Pet element frames
PET_FRAMES = {
    "Water": "https://i.imgur.com/tfvhd1N.png",
    "Nature": "https://i.imgur.com/ezWMVBR.png",
    "Wind": "https://i.imgur.com/nRQbcro.png",
    "Dark": "https://i.imgur.com/gaTlyXl.png",
    "Corrupted": "https://i.imgur.com/lsiTAO8.png",
    "Light": "https://i.imgur.com/PkjFBGc.png",
    "Electric": "https://i.imgur.com/wTpbykO.png",
    "Fire": "https://i.imgur.com/3tKBarP.png",
}


# ============================
# INTERNAL HELPERS
# ============================

IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp")
DIRECT_IMGUR = re.compile(r"^https://i\.imgur\.com/[A-Za-z0-9]+\.(png|jpg|jpeg|webp)$", re.I)
ID_PATTERN = re.compile(r"^\d{17,19}$")
MENTION_PATTERN = re.compile(r"<@!?(\d{17,19})>")

# Browser-like headers (helps with CDNs / bot filtering)
HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
}


def clean_url(url: str) -> str:
    # handles <https://...> pasted from Discord
    return (url or "").strip().strip("<>").strip()


def _strip_query(url: str) -> str:
    return clean_url(url).split("?", 1)[0]


def is_direct_imgur(url: str) -> bool:
    url = clean_url(url)
    return bool(DIRECT_IMGUR.match(url))


def is_direct_image_link(url: str) -> bool:
    """Accept direct image URLs even if they have query params."""
    u = _strip_query(url)
    return bool(u) and u.lower().endswith(IMG_EXTS)


def normalize_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def safe_int(v, default=0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def bit_is_set(bitstring_val, bit_index: int) -> bool:
    """profile.badges is bit varying(16). asyncpg often returns it as a string like '0101...'."""
    if bitstring_val is None:
        return False
    if isinstance(bitstring_val, str):
        s = bitstring_val.strip()
        if bit_index < 0 or bit_index >= len(s):
            return False
        return s[bit_index] == "1"
    return False


class GreekCard(commands.Cog):
    """GreekCard rendering and background management."""

    def __init__(self, bot):
        self.bot = bot
        self._fonts = None
        self._img_cache: Dict[str, Tuple[float, Image.Image]] = {}
        self._cache_ttl = 60 * 20  # 20 minutes
        self._settings_schema_ready = False
        self._settings_extended_schema = False

    # -----------------
    # Fonts
    # -----------------
    def _get_fonts(self):
        if self._fonts:
            return self._fonts

        title_xl = ImageFont.truetype(FONT_TITLE_PATH, 44)
        title_h = ImageFont.truetype(FONT_TITLE_PATH, 26)

        text = ImageFont.truetype(FONT_TEXT_PATH, 24)
        small = ImageFont.truetype(FONT_TEXT_PATH, 18)

        try:
            text_bold = ImageFont.truetype(FONT_TEXT_BOLD_PATH, 24)
        except Exception:
            text_bold = text

        self._fonts = {
            "title_xl": title_xl,
            "title_h": title_h,
            "text": text,
            "text_bold": text_bold,
            "small": small,
        }
        return self._fonts

    # -----------------
    # Image fetching + caching
    # -----------------
    def _now(self) -> float:
        return time.time()

    def _cache_get(self, url: str) -> Optional[Image.Image]:
        ent = self._img_cache.get(url)
        if not ent:
            return None
        ts, img = ent
        if self._now() - ts > self._cache_ttl:
            self._img_cache.pop(url, None)
            return None
        return img.copy()

    def _cache_set(self, url: str, img: Image.Image):
        self._img_cache[url] = (self._now(), img.copy())

    async def _fetch_bytes(self, url: str) -> Optional[bytes]:
        url = clean_url(url)
        if not url:
            return None
        try:
            async with self.bot.trusted_session.get(
                url,
                allow_redirects=True,
                timeout=20,
                headers=HTTP_HEADERS,
            ) as r:
                if r.status != 200:
                    return None
                return await r.read()
        except Exception:
            return None

    async def _load_image_url(self, url: str) -> Optional[Image.Image]:
        url = clean_url(url)
        if not url:
            return None

        cached = self._cache_get(url)
        if cached:
            return cached

        data = await self._fetch_bytes(url)
        if not data:
            return None

        # Validate by decoding (avoid false negatives)
        try:
            img = Image.open(io.BytesIO(data)).convert("RGBA")
        except Exception:
            return None

        self._cache_set(url, img)
        return img.copy()

    async def _validate_image_url(self, url: str) -> bool:
        """True only if we can GET and decode it as an image."""
        if not url:
            return False
        data = await self._fetch_bytes(url)
        if not data:
            return False
        try:
            Image.open(io.BytesIO(data)).verify()
        except Exception:
            return False
        return True

    # -----------------
    # Target resolution
    # -----------------
    async def _resolve_target_user(self, ctx, target: Optional[str]) -> discord.User:
        """Accepts mention, id, or profile.discordtag."""
        if not target:
            return ctx.author

        raw = target.split()[0]

        m = MENTION_PATTERN.match(raw)
        if m:
            uid = int(m.group(1))
            return await self.bot.fetch_user(uid)

        if ID_PATTERN.match(raw):
            return await self.bot.fetch_user(int(raw))

        async with self.bot.pool.acquire() as conn:
            uid = await conn.fetchval('SELECT "user" FROM profile WHERE discordtag=$1', raw)
        if not uid:
            raise ValueError("Unknown user")
        return await self.bot.fetch_user(int(uid))

    # -----------------
    # DB helpers
    # -----------------
    async def _ensure_settings_schema(self, conn):
        if self._settings_schema_ready:
            return

        has_text_color = await conn.fetchval(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_name='greekcard_settings' AND column_name='text_color'
            """
        )
        has_visible_badges = await conn.fetchval(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_name='greekcard_settings' AND column_name='visible_badges'
            """
        )

        if has_text_color and has_visible_badges:
            self._settings_extended_schema = True
            self._settings_schema_ready = True
            return

        try:
            await conn.execute(
                """
                ALTER TABLE greekcard_settings
                ADD COLUMN IF NOT EXISTS text_color character varying(32),
                ADD COLUMN IF NOT EXISTS visible_badges text[] DEFAULT ARRAY[]::text[]
                """
            )
            self._settings_extended_schema = True
        except Exception:
            self._settings_extended_schema = False
        self._settings_schema_ready = True

    async def _ensure_settings_row(self, conn, user_id: int):
        await self._ensure_settings_schema(conn)
        await conn.execute(
            """
            INSERT INTO greekcard_settings (user_id)
            VALUES ($1)
            ON CONFLICT (user_id) DO NOTHING
            """,
            user_id,
        )

    async def _get_settings(self, conn, user_id: int):
        await self._ensure_settings_row(conn, user_id)
        return await conn.fetchrow("SELECT * FROM greekcard_settings WHERE user_id=$1", user_id)

    def _badge_key_from_token(self, token: str) -> Optional[str]:
        normalized = normalize_token(token)
        if not normalized:
            return None
        if normalized in BADGE_ALIASES:
            return BADGE_ALIASES[normalized]
        for key, badge in BADGE_DEFINITIONS.items():
            if normalized in {normalize_token(key), normalize_token(badge["name"])}:
                return key
        return None

    def _selected_badges_from_settings(self, settings) -> List[str]:
        if not settings:
            return []
        values = settings.get("visible_badges") or []
        if isinstance(values, str):
            values = [values]

        selected: List[str] = []
        for entry in values:
            key = self._badge_key_from_token(str(entry))
            if key and key not in selected:
                selected.append(key)
        return selected[:4]

    def _get_unlocked_badges(self, profile, badge_top: Dict[str, Set[int]], user_id: int) -> List[str]:
        unlocked: List[str] = []
        for key in BADGE_ORDER:
            badge = BADGE_DEFINITIONS[key]
            bit = badge.get("bit")
            profile_badge = badge.get("profile_badge")
            has_top = user_id in badge_top.get(key, set())
            has_bit = bit_is_set(profile.get("badges"), bit) if bit is not None else False
            has_profile_badge = False
            if profile_badge is not None:
                try:
                    profile_badge_value = getattr(Badge, str(profile_badge), None)
                    has_profile_badge = bool(
                        profile_badge_value
                        and Badge.from_db(profile.get("badges")) & profile_badge_value
                    )
                except Exception:
                    has_profile_badge = False
            if has_top or has_bit or has_profile_badge:
                unlocked.append(key)
        return unlocked

    def _get_text_fill(self, settings) -> Tuple[int, int, int, int]:
        raw = settings.get("text_color") if settings else None
        if raw:
            try:
                rgba = color_utils.parse(str(raw))
                return (rgba.red, rgba.green, rgba.blue, 255)
            except Exception:
                pass
        return (255, 255, 255, 255)

    async def _get_badge_top_users(self, conn) -> Dict[str, Set[int]]:
        """Compute top users for each dynamic badge category."""
        top: Dict[str, Set[int]] = {key: set() for key in BADGE_ORDER}

        top_pvp = await conn.fetchval(
            'SELECT "user" FROM profile ORDER BY pvpwins DESC NULLS LAST, xp DESC NULLS LAST LIMIT 1'
        )
        if top_pvp is not None:
            top["pvp"].add(int(top_pvp))

        top_richest = await conn.fetchval(
            'SELECT "user" FROM profile ORDER BY money DESC NULLS LAST, xp DESC NULLS LAST LIMIT 1'
        )
        if top_richest is not None:
            top["richest"].add(int(top_richest))

        top_lover = await conn.fetchval(
            'SELECT "user" FROM profile ORDER BY lovescore DESC NULLS LAST, xp DESC NULLS LAST LIMIT 1'
        )
        if top_lover is not None:
            top["lover"].add(int(top_lover))

        splicer_user = None
        for col in ("user_id", "user", "requester", "requester_id", "author_id"):
            try:
                splicer_user = await conn.fetchval(
                    f"SELECT {col} FROM splice_requests GROUP BY {col} ORDER BY COUNT(*) DESC LIMIT 1"
                )
                if splicer_user is not None:
                    break
            except Exception:
                continue
        if splicer_user is not None:
            top["splicer"].add(int(splicer_user))

        try:
            divine_rows = await conn.fetch(
                """
                WITH favor_ranked AS (
                    SELECT
                        "god",
                        "user",
                        "favor",
                        ROW_NUMBER() OVER (
                            PARTITION BY "god"
                            ORDER BY "favor" DESC NULLS LAST, "xp" DESC NULLS LAST, "user" ASC
                        ) AS pos
                    FROM profile
                    WHERE COALESCE(TRIM("god"), '') <> ''
                )
                SELECT "user"
                FROM favor_ranked
                WHERE pos = 1
                ORDER BY "favor" DESC NULLS LAST
                LIMIT 4
                """
            )
            for row in divine_rows:
                uid = row.get("user")
                if uid is not None:
                    top["divine_favor"].add(int(uid))
        except Exception:
            pass

        # Agon historical badges: unlocked if player ever finished absolute top 1/2/3.
        try:
            agon_rows = await conn.fetch(
                """
                SELECT user_id, final_rank
                FROM agon_results
                WHERE pool_tier = 1
                  AND final_rank IN (1, 2, 3)
                """
            )
            for row in agon_rows:
                uid = int(row["user_id"])
                final_rank = int(row["final_rank"])
                if final_rank == 1:
                    top["agon_top1"].add(uid)
                elif final_rank == 2:
                    top["agon_top2"].add(uid)
                elif final_rank == 3:
                    top["agon_top3"].add(uid)
        except Exception:
            pass

        return top

    def _parse_badge_selection_input(self, raw: str) -> Tuple[List[str], List[str]]:
        raw = (raw or "").strip()
        if not raw:
            return [], []

        whole = self._badge_key_from_token(raw)
        if whole:
            return [whole], []

        parts = [p.strip() for p in raw.split(",")] if "," in raw else raw.split()
        selected: List[str] = []
        invalid: List[str] = []
        for part in parts:
            if not part:
                continue
            key = self._badge_key_from_token(part)
            if key is None:
                invalid.append(part)
                continue
            if key not in selected:
                selected.append(key)
        return selected, invalid

    async def _send_gc_help(self, ctx):
        badge_lines = []
        for idx, key in enumerate(BADGE_ORDER, start=1):
            badge_lines.append(f"{idx}. {BADGE_DEFINITIONS[key]['name']} (`{key}`)")

        msg = (
            "**GreekCard Help**\n"
            "`$gc [@user|id|discordtag]` - View your (or another player's) GreekCard\n"
            "`$gc help` - Show this help\n"
            "`$gcbg <direct i.imgur.com link>` - Set card background\n"
            "`$gcbg` with image attachment - Set background from upload\n"
            "`$gcbg view` / `$gcbg remove` - View/remove background\n"
            "`$gctext <color>` - Set GreekCard text color (`#RRGGBB`, `rgb(...)`, css name)\n"
            "`$gctext reset` - Reset text color\n"
            "`$gcbadges` - Show unlocked badges + current badge picks\n"
            "`$gcbadges <choices>` - Pick badges to show (max 4)\n"
            "`$gcbadges reset` - Auto-show first 4 unlocked badges\n\n"
            "**Badge choices**\n"
            + "\n".join(badge_lines)
            + "\n\nExample: `$gcbadges 1,3,5` or `$gcbadges pvp,splicer,divine_favor`"
        )
        await ctx.send(msg)

    # ============================
    # COMMANDS
    # ============================

    @commands.command(name="gcbg")
    async def gcbg(self, ctx, *, arg: str = None):
        """Set/remove GreekCard background.

        - $gcbg <direct i.imgur.com link>
        - $gcbg (with an attachment)
        - $gcbg view
        - $gcbg remove
        """
        attachment_url = None
        if ctx.message.attachments:
            a = ctx.message.attachments[0]
            if a.filename and a.filename.lower().endswith(IMG_EXTS) and ((a.content_type or "").startswith("image/")):
                attachment_url = a.url

        arg = clean_url(arg)

        async with self.bot.pool.acquire() as conn:
            await self._ensure_settings_row(conn, ctx.author.id)

            if (arg or "").lower() in ("remove", "clear", "reset", "none", "off"):
                await conn.execute(
                    "UPDATE greekcard_settings SET background=NULL, updated_at=NOW() WHERE user_id=$1",
                    ctx.author.id,
                )
                return await ctx.send("✅ GreekCard background removed (default will be used).")

            if (arg or "").lower() == "view":
                current = await conn.fetchval(
                    "SELECT background FROM greekcard_settings WHERE user_id=$1",
                    ctx.author.id,
                )
                return await ctx.send(f"Current GreekCard background: {current or 'Default'}")

            if attachment_url:
                await conn.execute(
                    "UPDATE greekcard_settings SET background=$1, updated_at=NOW() WHERE user_id=$2",
                    attachment_url,
                    ctx.author.id,
                )
                return await ctx.send("✅ GreekCard background set from your upload!")

            if not arg:
                return await ctx.send(
                    "Usage: `$gcbg <direct i.imgur.com link>` OR upload an image with `$gcbg`.\n"
                    "Also: `$gcbg view` / `$gcbg remove`."
                )

            # URL mode: strict direct imgur
            if not is_direct_imgur(arg):
                return await ctx.send(
                    "❌ Invalid link. Use a **direct** Imgur image link like `https://i.imgur.com/xxxxx.png` "
                    "(must end with .png/.jpg/.jpeg/.webp). Album links (`imgur.com/a/...`) won’t work."
                )

            # Strong validation: must be downloadable + decodable
            ok = await self._validate_image_url(arg)
            if not ok:
                return await ctx.send("❌ I can't access that image from the server (dead/private/unreadable link).")

            await conn.execute(
                "UPDATE greekcard_settings SET background=$1, updated_at=NOW() WHERE user_id=$2",
                arg,
                ctx.author.id,
            )

        await ctx.send("✅ GreekCard background set!")

    @commands.command(name="gctext", aliases=["gctextcolor", "gctextcolour"])
    async def gctext(self, ctx, *, colour: str = None):
        """Set the GreekCard text color, or reset it."""
        if not colour:
            return await ctx.send(
                "Usage: `$gctext <color>` (examples: `#ffd700`, `rgb(255,215,0)`, `gold`) or `$gctext reset`."
            )

        colour = (colour or "").strip()
        lowered = colour.lower()

        async with self.bot.pool.acquire() as conn:
            await self._ensure_settings_row(conn, ctx.author.id)
            if not self._settings_extended_schema:
                return await ctx.send(
                    "❌ GreekCard text colors require DB columns `text_color` and `visible_badges` on `greekcard_settings`."
                )

            if lowered in {"reset", "clear", "default", "none", "off"}:
                await conn.execute(
                    "UPDATE greekcard_settings SET text_color=NULL, updated_at=NOW() WHERE user_id=$1",
                    ctx.author.id,
                )
                return await ctx.send("✅ GreekCard text color reset to default.")

            try:
                rgba = color_utils.parse(colour)
            except Exception:
                return await ctx.send(
                    "❌ Invalid color. Use `#RGB`, `#RRGGBB`, `rgb(r,g,b)`, `rgba(r,g,b,a)` or a CSS color name."
                )

            hex_colour = f"#{rgba.red:02x}{rgba.green:02x}{rgba.blue:02x}"
            await conn.execute(
                "UPDATE greekcard_settings SET text_color=$1, updated_at=NOW() WHERE user_id=$2",
                hex_colour,
                ctx.author.id,
            )

        await ctx.send(f"✅ GreekCard text color set to `{hex_colour}`.")

    @commands.command(name="gcbadges", aliases=["gcbadge"])
    async def gcbadges(self, ctx, *, picks: str = None):
        """View or set which badges appear on your GreekCard (max 4)."""
        async with self.bot.pool.acquire() as conn:
            profile = await conn.fetchrow('SELECT * FROM profile WHERE "user"=$1', ctx.author.id)
            if not profile:
                return await ctx.send("You do not have a character yet.")

            settings = await self._get_settings(conn, ctx.author.id)
            badge_top = await self._get_badge_top_users(conn)

            unlocked = self._get_unlocked_badges(profile, badge_top, ctx.author.id)
            selected = self._selected_badges_from_settings(settings)
            selected = [key for key in selected if key in unlocked][:4]

            if not picks:
                lines = []
                for idx, key in enumerate(BADGE_ORDER, start=1):
                    badge = BADGE_DEFINITIONS[key]
                    state = "Unlocked" if key in unlocked else "Locked"
                    selected_marker = " [Selected]" if key in selected else ""
                    lines.append(f"{idx}. {badge['name']} (`{key}`) - {state}{selected_marker}")

                active = selected if selected else unlocked[:4]
                active_names = (
                    ", ".join(BADGE_DEFINITIONS[key]["name"] for key in active)
                    if active
                    else "None unlocked yet"
                )
                return await ctx.send(
                    "GreekCard badges (max 4 shown):\n"
                    + "\n".join(lines)
                    + f"\n\nCurrently displayed: {active_names}\n"
                    "Set with: `$gcbadges 1,3,5` or `$gcbadges pvp,splicer,divine_favor`\n"
                    "Use `$gcbadges reset` to clear manual picks."
                )

            lowered = picks.strip().lower()
            if lowered in {"reset", "clear", "default", "auto", "none"}:
                if not self._settings_extended_schema:
                    return await ctx.send(
                        "❌ GreekCard badge picks require DB columns `text_color` and `visible_badges` on `greekcard_settings`."
                    )
                await conn.execute(
                    "UPDATE greekcard_settings SET visible_badges=ARRAY[]::text[], updated_at=NOW() WHERE user_id=$1",
                    ctx.author.id,
                )
                return await ctx.send("✅ Badge selection reset. GreekCard will auto-show up to 4 unlocked badges.")

            selected_keys, invalid = self._parse_badge_selection_input(picks)
            if invalid:
                return await ctx.send(
                    "❌ Unknown badge choice(s): "
                    + ", ".join(f"`{item}`" for item in invalid)
                    + ". Use `$gcbadges` to see valid options."
                )
            if not selected_keys:
                return await ctx.send("❌ No valid badges selected. Use `$gcbadges` to list options.")
            if len(selected_keys) > 4:
                return await ctx.send("❌ You can only select up to 4 badges.")

            locked = [key for key in selected_keys if key not in unlocked]
            if locked:
                locked_names = ", ".join(BADGE_DEFINITIONS[key]["name"] for key in locked)
                return await ctx.send(f"❌ You have not unlocked: {locked_names}.")

            if not self._settings_extended_schema:
                return await ctx.send(
                    "❌ GreekCard badge picks require DB columns `text_color` and `visible_badges` on `greekcard_settings`."
                )

            await conn.execute(
                "UPDATE greekcard_settings SET visible_badges=$1, updated_at=NOW() WHERE user_id=$2",
                selected_keys,
                ctx.author.id,
            )

        names = ", ".join(BADGE_DEFINITIONS[key]["name"] for key in selected_keys)
        await ctx.send(f"✅ GreekCard badges updated: {names}")

    @commands.command(name="greekcard", aliases=["gc", "p", "profile", "me"])
    @commands.cooldown(1, 30, BucketType.user)
    async def greekcard(self, ctx, *, target: str = None):
        """Render the GreekCard for yourself or someone else."""

        if target and target.strip().lower() in {"help", "h", "?"}:
            return await self._send_gc_help(ctx)

        try:
            user = await self._resolve_target_user(ctx, target)
        except Exception:
            return await ctx.send("Unknown User")

        fonts = self._get_fonts()
        agon_rank_text = "N/A"
        agon_streak_text = None

        async with self.bot.pool.acquire() as conn:
            profile = await conn.fetchrow('SELECT * FROM profile WHERE "user"=$1', user.id)
            if not profile:
                return await ctx.send(f"**{user.display_name}** does not have a character.")

            settings = await self._get_settings(conn, user.id)

            pet = await conn.fetchrow(
                "SELECT * FROM monster_pets WHERE user_id=$1 AND equipped=true ORDER BY id DESC LIMIT 1",
                user.id,
            )

            # Equipped weapons
            items = []
            try:
                items = await self.bot.get_equipped_items_for(user.id)
            except Exception:
                try:
                    rows = await conn.fetch(
                        """
                        SELECT ai.*, i.equipped
                        FROM allitems ai
                        JOIN inventory i ON (ai.id=i.item)
                        WHERE ai.owner=$1 AND i.equipped=true
                        ORDER BY (ai.damage+ai.armor) DESC
                        LIMIT 2
                        """,
                        user.id,
                    )
                    items = [dict(r) for r in rows]
                except Exception:
                    items = []

            amulet = await conn.fetchrow(
                "SELECT * FROM amulets WHERE user_id=$1 AND equipped=true ORDER BY id DESC LIMIT 1",
                user.id,
            )

            children_count = await conn.fetchval(
                "SELECT COUNT(*) FROM children WHERE mother=$1 OR father=$1",
                user.id,
            )

            # Rankings
            rank_rich = await conn.fetchval("SELECT COUNT(*)+1 FROM profile WHERE money > $1", profile["money"])
            rank_xp = await conn.fetchval("SELECT COUNT(*)+1 FROM profile WHERE xp > $1", profile["xp"])
            rank_love = await conn.fetchval("SELECT COUNT(*)+1 FROM profile WHERE lovescore > $1", profile["lovescore"])
            rank_pvp = await conn.fetchval("SELECT COUNT(*)+1 FROM profile WHERE pvpwins > $1", profile["pvpwins"])

            rank_bt = None
            rank_couples_bt = None
            try:
                bt_row = await conn.fetchrow(
                    "SELECT level, prestige FROM battletower WHERE id=$1",
                    user.id,
                )
                if bt_row:
                    rank_bt = await conn.fetchval(
                        """
                        SELECT COUNT(*) + 1
                        FROM battletower
                        WHERE (prestige > $1) OR (prestige = $1 AND level > $2)
                        """,
                        safe_int(bt_row.get("prestige"), 0),
                        safe_int(bt_row.get("level"), 0),
                    )
            except Exception:
                rank_bt = None

            try:
                couples_row = await conn.fetchrow(
                    """
                    SELECT current_level, prestige
                    FROM couples_battle_tower
                    WHERE partner1_id=$1 OR partner2_id=$1
                    ORDER BY prestige DESC NULLS LAST, current_level DESC NULLS LAST
                    LIMIT 1
                    """,
                    user.id,
                )
                if couples_row:
                    rank_couples_bt = await conn.fetchval(
                        """
                        SELECT COUNT(*) + 1
                        FROM couples_battle_tower
                        WHERE (prestige > $1) OR (prestige = $1 AND current_level > $2)
                        """,
                        safe_int(couples_row.get("prestige"), 0),
                        safe_int(couples_row.get("current_level"), 0),
                    )
            except Exception:
                rank_couples_bt = None

            try:
                agon_entry = await conn.fetchrow(
                    """
                    SELECT e.season_id,
                           e.pool_tier,
                           CASE
                               WHEN e.season_id = m.current_season_id
                                AND UPPER(COALESCE(m.phase, 'STOPPED')) = 'ACTIVE'
                                AND NOW() >= m.active_end - INTERVAL '2 hours'
                                AND NOW() < m.active_end
                               THEN COALESCE(e.veiled_rank, e.rank_pos)
                               ELSE e.rank_pos
                           END AS rank_pos
                    FROM agon_entries e
                    LEFT JOIN agon_meta m ON m.id = 1
                    WHERE e.user_id = $1
                    ORDER BY e.season_id DESC
                    LIMIT 1
                    """,
                    user.id,
                )
                agon_stats = await conn.fetchrow(
                    """
                    SELECT top_pool_top10_streak
                    FROM agon_user_stats
                    WHERE user_id = $1
                    """,
                    user.id,
                )
                streak = safe_int(agon_stats.get("top_pool_top10_streak"), 0) if agon_stats else 0

                if agon_entry:
                    pool_tier = safe_int(agon_entry.get("pool_tier"), 0)
                    rank_pos = safe_int(agon_entry.get("rank_pos"), 0)
                    pool_label = AGON_POOL_LABELS.get(pool_tier, f"Pool {pool_tier}")
                    agon_rank_text = f"Rank {rank_pos} in {pool_label}"
                    if pool_tier == 1 and rank_pos <= 10 and streak > 0:
                        agon_streak_text = f"Top 10 for {streak} season(s)"
                elif streak > 0:
                    agon_streak_text = f"Top 10 for {streak} season(s)"
            except Exception:
                pass

            badge_top = await self._get_badge_top_users(conn)

        unlocked_badges = self._get_unlocked_badges(profile, badge_top, user.id)
        selected_badges = self._selected_badges_from_settings(settings)
        selected_badges = [key for key in selected_badges if key in unlocked_badges][:4]
        visible_badges = selected_badges if selected_badges else unlocked_badges[:4]

        rank_bt_text = f"#{rank_bt}" if rank_bt else "N/A"
        rank_couples_bt_text = f"#{rank_couples_bt}" if rank_couples_bt else "N/A"

        # Background selection
        bg_url = clean_url(settings.get("background") if settings else None)

        card = None
        if bg_url and is_direct_image_link(bg_url):
            bg = await self._load_image_url(bg_url)
            if bg:
                card = bg.resize((CARD_W, CARD_H))

        if card is None and DEFAULT_BG_URL:
            if is_direct_image_link(DEFAULT_BG_URL):
                bg = await self._load_image_url(DEFAULT_BG_URL)
                if bg:
                    card = bg.resize((CARD_W, CARD_H))

        if card is None:
            card = Image.new("RGBA", (CARD_W, CARD_H), (18, 18, 22, 255))

        draw = ImageDraw.Draw(card)
        text_fill = self._get_text_fill(settings)

        def draw_text(xy, text, font):
            draw.text(xy, text, font=font, fill=text_fill)

        # -----------------
        # Layout
        # -----------------
        LEFT_X = 60
        TOP_Y = 80

        AVA_SIZE = 120
        AVA_X, AVA_Y = LEFT_X, TOP_Y

        NAME_X = LEFT_X + AVA_SIZE + 25
        NAME_Y = TOP_Y

        STATS_Y = TOP_Y + 120
        LOVE_Y = TOP_Y + 200

        # Pet block
        PET_IMG_X, PET_IMG_Y = AVA_X, 390
        PET_IMG_SIZE = (200,200)  # a bit bigger, looks nicer

        PET_TEXT_X = PET_IMG_X + PET_IMG_SIZE[0] + 25  # always to the right of the pet image
        PET_TEXT_Y = PET_IMG_Y + 15  

        EQUIP_X, EQUIP_Y = 650, TOP_Y
        RANK_X, RANK_Y = 650, 235
        BADGE_X, BADGE_Y = 650, 420

        PET_FRAME_SCALE = 1.515
        PET_FRAME_OFFSET = (0, 45)
        PET_IMAGE_OFFSET = (0, 82)
        PET_BLOCK_OFFSET = (0, -90)


        # Avatar
        avatar_url = str(getattr(user.display_avatar, "url", user.avatar.url if user.avatar else ""))
        avatar_img = await self._load_image_url(avatar_url)
        if avatar_img:
            avatar_img = avatar_img.resize((AVA_SIZE, AVA_SIZE))
            card.paste(avatar_img, (AVA_X, AVA_Y), avatar_img)

        # Name / Race / Class
        name_text = profile.get("name") or user.display_name
        race = profile.get("race") or "Unknown"
        classes = profile.get("class") or ["No Class", "No Class"]
        if isinstance(classes, str):
            classes = [classes]

        draw_text((NAME_X, NAME_Y), str(name_text), font=fonts["title_xl"])
        draw_text((NAME_X, NAME_Y + 55), f"Race: {race}", font=fonts["text"])
        draw_text((NAME_X, NAME_Y + 85), f"Class: {' / '.join(classes)}", font=fonts["text"])

        # Stats two columns
        level = rpgtools.xptolevel(profile["xp"])

        god = profile.get("god") or "None"
        atk = safe_int(profile.get("statatk"), 0)
        deff = safe_int(profile.get("statdef"), 0)

        col1_x = NAME_X
        col2_x = NAME_X + 210
        y = STATS_Y

        draw_text((col1_x, y), f"Level: {level}", font=fonts["text_bold"])
        draw_text((col1_x, y + 32), f"God: {god}", font=fonts["text"])

        draw_text((col2_x, y), f"ATK: {atk}", font=fonts["text_bold"])
        draw_text((col2_x, y + 32), f"DEF: {deff}", font=fonts["text_bold"])


        # Spouse / Love / Children
        spouse_id = safe_int(profile.get("marriage"), 0)
        spouse_name = "None"
        if spouse_id and spouse_id != 0:
            try:
                spouse = await self.bot.fetch_user(spouse_id)
                spouse_name = spouse.display_name if spouse else "None"
            except Exception:
                spouse_name = "None"

        love_score = safe_int(profile.get("lovescore"), 0)

        draw_text((NAME_X, LOVE_Y), f"Spouse: {spouse_name}", font=fonts["text"])
        draw_text((NAME_X, LOVE_Y + 30), f"Love: {love_score}", font=fonts["text"])
        draw_text((NAME_X, LOVE_Y + 60), f"Children: {children_count}", font=fonts["text"])

        # Equipment
        draw_text((EQUIP_X, EQUIP_Y), "Equipment", font=fonts["title_h"])

        right_hand = None
        left_hand = None

        any_count = sum(1 for i in items if (i.get("hand") == "any"))
        if len(items) == 2 and any_count == 1 and items[0].get("hand") == "any":
            items = [items[1], items[0]]

        for i in items:
            hand = i.get("hand")
            if hand == "both":
                right_hand, left_hand = i, i
            elif hand == "left":
                left_hand = i
            elif hand == "right":
                right_hand = i
            elif hand == "any":
                if right_hand is None:
                    right_hand = i
                else:
                    left_hand = i

        def fmt_item(it):
            if not it:
                return "None"
            name = it.get("name") or "Item"
            dmg = safe_int(it.get("damage"), 0)
            arm = safe_int(it.get("armor"), 0)
            return f"{name} ({dmg + arm})"

        draw_text((EQUIP_X, EQUIP_Y + 42), f"Right Hand: {fmt_item(right_hand)}", font=fonts["small"])
        draw_text((EQUIP_X, EQUIP_Y + 67), f"Left Hand:  {fmt_item(left_hand)}", font=fonts["small"])

        if amulet:
            am_type = amulet.get("type") or "Amulet"
            am_tier = safe_int(amulet.get("tier"), 0)
            am_atk = safe_int(amulet.get("attack"), 0)
            am_def = safe_int(amulet.get("defense"), 0)
            am_hp = safe_int(amulet.get("hp"), 0)

            draw_text((EQUIP_X, EQUIP_Y + 100), f"Amulet: {am_type} (T{am_tier})", font=fonts["text"])
            draw_text((EQUIP_X, EQUIP_Y + 130), f"+ATK {am_atk}   +DEF {am_def}   +HP {am_hp}", font=fonts["small"])
        else:
            draw_text((EQUIP_X, EQUIP_Y + 100), "Amulet: None", font=fonts["text"])

        # Rankings
        draw_text((RANK_X, RANK_Y), "Rankings", font=fonts["title_h"])
        c1x = RANK_X
        c2x = RANK_X + 220
        draw_text((c1x, RANK_Y + 45), f"💰 Rich: #{rank_rich}", font=fonts["text"])
        draw_text((c2x, RANK_Y + 45), f"⭐ XP: #{rank_xp}", font=fonts["text"])
        draw_text((c1x, RANK_Y + 78), f"❤️ Love: #{rank_love}", font=fonts["text"])
        draw_text((c2x, RANK_Y + 78), f"⚔ PvP: #{rank_pvp}", font=fonts["text"])
        draw_text((c1x, RANK_Y + 111), f"🏰 BT: {rank_bt_text}", font=fonts["small"])
        draw_text((c2x, RANK_Y + 111), f"💞 Couples BT: {rank_couples_bt_text}", font=fonts["small"])
        draw_text((c1x, RANK_Y + 136), f"🏛 Agon: {agon_rank_text}", font=fonts["small"])
        if agon_streak_text:
            draw_text((c1x, RANK_Y + 160), f"🌟 {agon_streak_text}", font=fonts["small"])

        # -----------------
        # Pet block with decorative frame
        # -----------------
        if pet:
            pet_name = pet.get("name") or "Pet"
            pet_el = (pet.get("element") or "Unknown").strip()
            pet_hp = safe_int(pet.get("hp"), 0)
            pet_atk = safe_int(pet.get("attack"), 0)
            pet_def = safe_int(pet.get("defense"), 0)

            # Text (right of pet image)
            draw_text((PET_TEXT_X, PET_TEXT_Y), f"Pet: {pet_name}", font=fonts["title_h"])
            draw_text((PET_TEXT_X, PET_TEXT_Y + 40), f"Element: {pet_el}", font=fonts["text"])
            draw_text((PET_TEXT_X, PET_TEXT_Y + 70), f"HP: {pet_hp}", font=fonts["text"])
            draw_text((PET_TEXT_X, PET_TEXT_Y + 100), f"ATK: {pet_atk}", font=fonts["text"])
            draw_text((PET_TEXT_X, PET_TEXT_Y + 130), f"DEF: {pet_def}", font=fonts["text"])

            # Pet image
            pet_url = (pet.get("url") or pet.get("image_url") or "").strip()
            pimg = await self._load_image_url(pet_url)

            frame_url = PET_FRAMES.get(pet_el) or PET_FRAMES.get(pet_el.title()) or PET_FRAMES.get(pet_el.capitalize())
            frame = await self._load_image_url(frame_url) if frame_url else None

            if pimg or frame:
                resample = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS

                # Configuration
                FRAME_PADDING_PERCENT = 0.10  # 10% bigger (0.15 = 15%, 0.20 = 20%)

                # Process pet image - fit inside the pet box
                pet_w, pet_h = PET_IMG_SIZE
                rendered_pet = None
                pet_natural_w = pet_natural_h = 0

                if pimg:
                    pimg = pimg.convert("RGBA")
                    pimg.thumbnail(PET_IMG_SIZE, resample=resample)
                    pet_natural_w, pet_natural_h = pimg.size
                    rendered_pet = pimg
                else:
                    # No pet image, use full box size for frame reference
                    pet_natural_w, pet_natural_h = PET_IMG_SIZE

                # Calculate padding once and use a top-anchored layout.
                padding = int(max(pet_natural_w, pet_natural_h) * FRAME_PADDING_PERCENT)

                # Process frame - scale based on actual pet size + padding
                rendered_frame = None
                if frame:
                    frame = frame.convert("RGBA")

                    # Calculate frame size: pet size + padding on all sides
                    base_frame_w = pet_natural_w + (padding * 2)
                    base_frame_h = pet_natural_h + (padding * 2)

                    # Keep previous user tuning knobs active.
                    frame_w = max(1, int(base_frame_w * PET_FRAME_SCALE))
                    frame_h = max(1, int(base_frame_h * PET_FRAME_SCALE))

                    # Resize frame to fit around pet
                    frame = frame.resize((frame_w, frame_h), resample=resample)
                    rendered_frame = frame

                # Create composite layer
                if rendered_frame:
                    frame_w, frame_h = rendered_frame.size
                else:
                    frame_w = frame_h = 0

                # Top-anchored system:
                # frame starts at Y=0 (+ offset), pet starts at Y=padding.
                base_comp_w = max(pet_natural_w + (padding * 2), frame_w)
                base_comp_h = max(pet_natural_h + (padding * 2), frame_h)
                pet_x = (base_comp_w - pet_natural_w) // 2 + PET_IMAGE_OFFSET[0]
                pet_y = (padding if rendered_frame else 0) + PET_IMAGE_OFFSET[1]

                if rendered_frame:
                    frame_x = (base_comp_w - frame_w) // 2 + PET_FRAME_OFFSET[0]
                    frame_y = PET_FRAME_OFFSET[1]
                else:
                    frame_x = frame_y = 0

                # Expand bounds if offsets push content outside the base composite.
                min_x = min(0, pet_x, frame_x if rendered_frame else 0)
                min_y = min(0, pet_y, frame_y if rendered_frame else 0)
                max_x = max(base_comp_w, pet_x + pet_natural_w, (frame_x + frame_w) if rendered_frame else base_comp_w)
                max_y = max(base_comp_h, pet_y + pet_natural_h, (frame_y + frame_h) if rendered_frame else base_comp_h)
                comp_w = max_x - min_x
                comp_h = max_y - min_y
                composite = Image.new("RGBA", (comp_w, comp_h), (0, 0, 0, 0))
                pet_x -= min_x
                pet_y -= min_y

                # Draw pet first, then frame on top so the frame is always visible.
                if rendered_pet:
                    composite.alpha_composite(rendered_pet, (pet_x, pet_y))

                if rendered_frame:
                    frame_x -= min_x
                    frame_y -= min_y
                    composite.alpha_composite(rendered_frame, (frame_x, frame_y))

                # Paste composite onto card (global block offset).
                # Use clipping instead of clamping so manual offsets stay respected.
                paste_x = PET_IMG_X + (pet_w - comp_w) // 2 + PET_BLOCK_OFFSET[0]
                paste_y = PET_IMG_Y + PET_BLOCK_OFFSET[1]

                src_x = max(0, -paste_x)
                src_y = max(0, -paste_y)
                dst_x = max(0, paste_x)
                dst_y = max(0, paste_y)
                copy_w = min(comp_w - src_x, CARD_W - dst_x)
                copy_h = min(comp_h - src_y, CARD_H - dst_y)

                if copy_w > 0 and copy_h > 0:
                    composite_crop = composite.crop((src_x, src_y, src_x + copy_w, src_y + copy_h))
                    card.alpha_composite(composite_crop, (dst_x, dst_y))

        else:
            draw_text((PET_TEXT_X, PET_TEXT_Y), "Pet: None Equipped", font=fonts["title_h"])


        # Badges
        draw_text((BADGE_X, BADGE_Y), "Badges", font=fonts["title_h"])

        icon_size = 64
        pad = 10
        gx, gy = BADGE_X, BADGE_Y + 45

        for idx, key in enumerate(visible_badges[:4]):
            icon_url = BADGE_DEFINITIONS[key]["icon"]
            ix = gx + (idx % 4) * (icon_size + pad)
            iy = gy + (idx // 4) * (icon_size + pad)
            icon = await self._load_image_url(icon_url)
            if icon:
                icon = icon.resize((icon_size, icon_size))
                card.paste(icon, (ix, iy), icon)

        # Output
        buf = io.BytesIO()
        card.save(buf, format="PNG")
        buf.seek(0)
        await ctx.send(file=discord.File(fp=buf, filename="greekcard.png"))

    @greekcard.error
    async def greekcard_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            return await ctx.send(f"⏳ Cooldown: try again in {int(error.retry_after)}s.")
        raise error


async def setup(bot):
    await bot.add_cog(GreekCard(bot))
