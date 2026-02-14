
"""GreekCard cog

What this cog provides
- $greekcard [@user|id|discordtag] : renders the GreekCard image
- $gcbg <direct i.imgur.com link ending with .png/.jpg/.jpeg/.webp> : set background
- $gcbg (with image attachment) : set background from upload
- $gcbg view : show current background
- $gcbg remove : clear to default

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
from typing import Optional, Dict, Tuple

import discord
from discord.ext import commands
from discord.ext.commands import BucketType
from utils import misc as rpgtools


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

# 4) Badge icons (bits -> icon URL)
# Dragon badge removed for now.
BADGE_ICONS = {
    0: "https://i.imgur.com/RiZ8Xd3.png",  # Most PvP wins
    1: "https://i.imgur.com/mTil6hG.png",  # Richest
    2: "https://i.imgur.com/q8CyQLz.png",  # Splicer (most splice requests)
    3: "https://i.imgur.com/tvbXKW3.png",  # The lover (top of love board)
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
    async def _ensure_settings_row(self, conn, user_id: int):
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

    async def _get_badge_top_users(self, conn) -> Dict[int, Optional[int]]:
        """Compute top user for each badge category (dragon removed)."""
        top: Dict[int, Optional[int]] = {0: None, 1: None, 2: None, 3: None}

        top[0] = await conn.fetchval('SELECT "user" FROM profile ORDER BY pvpwins DESC NULLS LAST, xp DESC LIMIT 1')
        top[1] = await conn.fetchval('SELECT "user" FROM profile ORDER BY money DESC NULLS LAST, xp DESC LIMIT 1')
        top[3] = await conn.fetchval('SELECT "user" FROM profile ORDER BY lovescore DESC NULLS LAST, xp DESC LIMIT 1')

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
        top[2] = splicer_user

        return top

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

    @commands.command(name="greekcard")
    @commands.cooldown(1, 30, BucketType.user)
    async def greekcard(self, ctx, *, target: str = None):
        """Render the GreekCard for yourself or someone else."""

        try:
            user = await self._resolve_target_user(ctx, target)
        except Exception:
            return await ctx.send("Unknown User")

        fonts = self._get_fonts()

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

            badge_top = await self._get_badge_top_users(conn)

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

        PET_FRAME_SCALE = 1.50       # 1.00 = same as box, >1 bigger, <1 smaller
        PET_FRAME_OFFSET = (0, 0)     # (x, y) frame shift inside the pet box


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

        draw.text((NAME_X, NAME_Y), str(name_text), font=fonts["title_xl"], fill="white")
        draw.text((NAME_X, NAME_Y + 55), f"Race: {race}", font=fonts["text"], fill="white")
        draw.text((NAME_X, NAME_Y + 85), f"Class: {' / '.join(classes)}", font=fonts["text"], fill="white")

        # Stats two columns
        level = rpgtools.xptolevel(profile["xp"])

        god = profile.get("god") or "None"
        atk = safe_int(profile.get("statatk"), 0)
        deff = safe_int(profile.get("statdef"), 0)

        col1_x = NAME_X
        col2_x = NAME_X + 210
        y = STATS_Y

        draw.text((col1_x, y), f"Level: {level}", font=fonts["text_bold"], fill="white")
        draw.text((col1_x, y + 32), f"God: {god}", font=fonts["text"], fill="white")

        draw.text((col2_x, y), f"ATK: {atk}", font=fonts["text_bold"], fill="white")
        draw.text((col2_x, y + 32), f"DEF: {deff}", font=fonts["text_bold"], fill="white")


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

        draw.text((NAME_X, LOVE_Y), f"Spouse: {spouse_name}", font=fonts["text"], fill="white")
        draw.text((NAME_X, LOVE_Y + 30), f"Love: {love_score}", font=fonts["text"], fill="white")
        draw.text((NAME_X, LOVE_Y + 60), f"Children: {children_count}", font=fonts["text"], fill="white")

        # Equipment
        draw.text((EQUIP_X, EQUIP_Y), "Equipment", font=fonts["title_h"], fill="white")

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

        draw.text((EQUIP_X, EQUIP_Y + 42), f"Right Hand: {fmt_item(right_hand)}", font=fonts["small"], fill="white")
        draw.text((EQUIP_X, EQUIP_Y + 67), f"Left Hand:  {fmt_item(left_hand)}", font=fonts["small"], fill="white")

        if amulet:
            am_type = amulet.get("type") or "Amulet"
            am_tier = safe_int(amulet.get("tier"), 0)
            am_atk = safe_int(amulet.get("attack"), 0)
            am_def = safe_int(amulet.get("defense"), 0)
            am_hp = safe_int(amulet.get("hp"), 0)

            draw.text((EQUIP_X, EQUIP_Y + 100), f"Amulet: {am_type} (T{am_tier})", font=fonts["text"], fill="white")
            draw.text((EQUIP_X, EQUIP_Y + 130), f"+ATK {am_atk}   +DEF {am_def}   +HP {am_hp}", font=fonts["small"], fill="white")
        else:
            draw.text((EQUIP_X, EQUIP_Y + 100), "Amulet: None", font=fonts["text"], fill="white")

        # Rankings
        draw.text((RANK_X, RANK_Y), "Rankings", font=fonts["title_h"], fill="white")
        c1x = RANK_X
        c2x = RANK_X + 220
        draw.text((c1x, RANK_Y + 45), f"💰 Rich: #{rank_rich}", font=fonts["text"], fill="white")
        draw.text((c2x, RANK_Y + 45), f"⭐ XP: #{rank_xp}", font=fonts["text"], fill="white")
        draw.text((c1x, RANK_Y + 78), f"❤️ Love: #{rank_love}", font=fonts["text"], fill="white")
        draw.text((c2x, RANK_Y + 78), f"⚔ PvP: #{rank_pvp}", font=fonts["text"], fill="white")

        # -----------------
        # Pet block (bigger image + one-column specs) with scalable frame
        # -----------------
        if pet:
            pet_name = pet.get("name") or "Pet"
            pet_el = (pet.get("element") or "Unknown").strip()
            pet_hp = safe_int(pet.get("hp"), 0)
            pet_atk = safe_int(pet.get("attack"), 0)
            pet_def = safe_int(pet.get("defense"), 0)

            # Text (right of pet image)
            draw.text((PET_TEXT_X, PET_TEXT_Y), f"Pet: {pet_name}", font=fonts["title_h"], fill="white")
            draw.text((PET_TEXT_X, PET_TEXT_Y + 40), f"Element: {pet_el}", font=fonts["text"], fill="white")
            draw.text((PET_TEXT_X, PET_TEXT_Y + 70), f"HP: {pet_hp}", font=fonts["text"], fill="white")
            draw.text((PET_TEXT_X, PET_TEXT_Y + 100), f"ATK: {pet_atk}", font=fonts["text"], fill="white")
            draw.text((PET_TEXT_X, PET_TEXT_Y + 130), f"DEF: {pet_def}", font=fonts["text"], fill="white")

            # Pet image
            pet_url = (pet.get("url") or pet.get("image_url") or "").strip()
            pimg = await self._load_image_url(pet_url)

            # Create the pet box regardless, so we can still show a frame even if image fails
            block = Image.new("RGBA", PET_IMG_SIZE, (0, 0, 0, 0))

            # Paste pet image centered (if exists)
            if pimg:
                # Fit inside box while preserving ratio
                pimg = pimg.convert("RGBA")
                pimg.thumbnail(PET_IMG_SIZE)

                px = (PET_IMG_SIZE[0] - pimg.size[0]) // 2
                py = (PET_IMG_SIZE[1] - pimg.size[1]) // 2
                block.paste(pimg, (px, py), pimg)

            # Frame overlay (same frame, scalable)
            frame_url = PET_FRAMES.get(pet_el) or PET_FRAMES.get(pet_el.title()) or PET_FRAMES.get(pet_el.capitalize())
            frame = await self._load_image_url(frame_url) if frame_url else None
            if frame:
                frame = frame.convert("RGBA")

                fw = max(1, int(PET_IMG_SIZE[0] * PET_FRAME_SCALE))
                fh = max(1, int(PET_IMG_SIZE[1] * PET_FRAME_SCALE))
                frame = frame.resize((fw, fh))

                fx = (PET_IMG_SIZE[0] - fw) // 2 + PET_FRAME_OFFSET[0]
                fy = (PET_IMG_SIZE[1] - fh) // 2 + PET_FRAME_OFFSET[1]

                # paste with alpha, do NOT alpha_composite (sizes differ)
                block.paste(frame, (fx, fy), frame)

            # Final paste to card
            card.paste(block, (PET_IMG_X, PET_IMG_Y), block)

        else:
            draw.text((PET_TEXT_X, PET_TEXT_Y), "Pet: None Equipped", font=fonts["title_h"], fill="white")


        # Badges
        draw.text((BADGE_X, BADGE_Y), "Badges", font=fonts["title_h"], fill="white")

        to_show = []
        for bit, icon_url in BADGE_ICONS.items():
            top_uid = badge_top.get(bit)
            is_top = (top_uid == user.id) if top_uid else False
            has_bit = bit_is_set(profile.get("badges"), bit)
            if is_top or has_bit:
                to_show.append((bit, icon_url))

        icon_size = 64
        pad = 10
        gx, gy = BADGE_X, BADGE_Y + 45

        for idx, (_, icon_url) in enumerate(to_show[:10]):
            ix = gx + (idx % 5) * (icon_size + pad)
            iy = gy + (idx // 5) * (icon_size + pad)
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
