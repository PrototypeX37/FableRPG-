"""
The IdleRPG Discord Bot
Copyright (C) 2018-2021 Diniboy and Gelbpunkt
Copyright (C) 2023-2024 Lunar (PrototypeX37)
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

from __future__ import annotations

import asyncio
import datetime
import io
import os
import random
import string
from collections import Counter
from datetime import timezone
from typing import List, Optional, Tuple

import aiohttp
import discord
from discord.ext import commands, tasks
from PIL import Image, ImageDraw, ImageFont, ImageFilter

from cogs.shard_communication import user_on_cooldown as user_cooldown
from utils.checks import has_char, is_gm
from utils.i18n import locale_doc, _


# ============================================================
# CONFIG
# ============================================================

# Fonts (same as greekcard)
FONT_TITLE_PATH = "assets/fonts/CaesarDressing-Regular.ttf"
FONT_TEXT_PATH = "assets/fonts/GFSDidot-Regular.ttf"
FONT_TEXT_BOLD_PATH = "assets/fonts/GFSDidot-Bold.ttf"

DRAGON_BG_FALLBACK_LOCAL = "assets/images/dragonslot_bg.png"

DRAGON_BG_URLS = [
    "https://i.imgur.com/GowLuSP.png",
]

# ✅ Best practice: keep a local fallback so the fight scene NEVER becomes black
# Put the old fight background here (recommended):
DRAGON_BG_FALLBACK_LOCAL: Optional[str] = "assets/images/dragonslot_bg.png"


# Economy constants
PLAY_COST = 1500
JACKPOT_ADD_PER_PLAY = 250
JACKPOT_ADD_ON_REWARD = 250  # original code added another +250 when total_reward != 0

# Dragon reset constants
RESET_DRAGON_HP = 125
RESET_PLAYER_HP = 100
RESET_JACKPOT_MIN = 10000
RESET_JACKPOT_MAX = 50000

# Captcha constants
CAPTCHA_TIMEOUT_SECONDS = 60
SEAT_TIMEOUT_SECONDS = 600  # 10 minutes
HTTP_TIMEOUT_SECONDS = 15

# Seat bounds
MIN_SEAT = 1
MAX_SEAT = 8


class Slots(commands.Cog):
    """DragonSlots: seats + slots + dragon-fight jackpot."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # captcha_lock stores expected text for users who are currently verifying
        # and users who are punishment-locked (until $unlock).
        self.captcha_lock: dict[int, str] = {}

        # per-user output mode
        self.text_mode: dict[int, bool] = {}

        self.timeout_duration = SEAT_TIMEOUT_SECONDS
        self.check_timeouts.start()

        self.http: aiohttp.ClientSession = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS),
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )



    # ============================================================
    # Path / font helpers
    # ============================================================

    def _resolve_path(self, path: str) -> Optional[str]:
        """Resolve relative paths safely (cwd can differ in services)."""
        if not path:
            return None

        # as-is
        if os.path.exists(path):
            return path

        # relative to repo root-ish (../assets/...) from this cog file
        base_dir = os.path.dirname(os.path.abspath(__file__))
        candidate = os.path.normpath(os.path.join(base_dir, "..", path))
        if os.path.exists(candidate):
            return candidate

        return None

    def _safe_font(self, path: str, size: int) -> ImageFont.FreeTypeFont:
        real = self._resolve_path(path)
        if real:
            return ImageFont.truetype(real, size=size)
        return ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size=size
        )

    # ============================================================
    # Core command group
    # ============================================================

    @has_char()
    @user_cooldown(6)
    @commands.group()
    @locale_doc
    async def slots(self, ctx: commands.Context):
        _(
            """Play the slot machine game.

            Use this command to play the slot machine.

            Subcommands:
            - `seats`: View slot machine seat information.
            - `takeseat`: Take a seat in the slot machine game.
            - `leaveseat`: Leave your current seat.
            - `text`: Toggle text-only mode for slot machine output.
            """
        )

        if ctx.invoked_subcommand is not None:
            return

        # Must be seated
        seat = await self._get_user_seat(ctx.author.id)
        if seat is None:
            return await ctx.send("You are not in a slot seat.")

        await self.update_last_activity(ctx.author.id)

        # If user is locked, block gameplay
        if self.is_user_captcha_locked(ctx.author.id):
            return await ctx.send("You are CAPTCHA locked. Use `$unlock` to unlock.")

        # 1% chance captcha verification
        if random.randint(1, 100) <= 1:
            ok = await self._do_captcha_verify(ctx, force=True)
            if not ok:
                return

        # Check money
        money = await self._get_user_money(ctx.author.id)
        if money is None:
            return await ctx.send("User not found in the database.")
        if money < PLAY_COST:
            return await ctx.send("You are too poor.")

        # Charge play and add to jackpot
        try:
            async with self.bot.pool.acquire() as connection:
                await connection.execute(
                    'UPDATE profile SET money = money - $1 WHERE "user" = $2',
                    PLAY_COST,
                    ctx.author.id,
                )
                await connection.execute(
                    'UPDATE dragonslots SET jackpot = jackpot + $1 WHERE seat = $2',
                    JACKPOT_ADD_PER_PLAY,
                    seat,
                )
        except Exception as e:
            return await ctx.send(f"An error occurred while deducting money: {e}")

        # Roll slots and risk
        total_reward, slot_results, dragon_count, risk_message, bonus, penalty = (
            await self._roll_slots_and_risk(seat)
        )

        # Apply reward + extra jackpot tick on reward
        try:
            async with self.bot.pool.acquire() as connection:
                if total_reward != 0:
                    if total_reward > 0:
                        await connection.execute(
                            'UPDATE profile SET money = money + $1 WHERE "user" = $2',
                            total_reward,
                            ctx.author.id,
                        )
                    else:
                        await connection.execute(
                            'UPDATE profile SET money = money - $1 WHERE "user" = $2',
                            abs(total_reward),
                            ctx.author.id,
                        )

                    await connection.execute(
                        'UPDATE dragonslots SET jackpot = jackpot + $1 WHERE seat = $2',
                        JACKPOT_ADD_ON_REWARD,
                        seat,
                    )

                jackpot_result = await connection.fetchval(
                    'SELECT jackpot FROM dragonslots WHERE seat = $1', seat
                )
        except Exception as e:
            return await ctx.send(f"An error occurred while updating money: {e}")

        # Output
        base_reward_display = total_reward - (bonus or 0) + (penalty or 0)
        if self.text_mode.get(ctx.author.id, False):
            slot_output = (
                f"Slot Machine Result\n"
                f"Slot 1: {slot_results[0]}\n"
                f"Slot 2: {slot_results[1]}\n"
                f"Slot 3: {slot_results[2]}\n"
                f"Base Reward: ${base_reward_display}\n"
                f"{risk_message if risk_message else 'Risk event: None'}\n"
                f"Jackpot: ${jackpot_result}\n"
                f"Occupant: {ctx.author.display_name}"
            )
            await ctx.send(f"```\n{slot_output}\n```")
        else:
            embed = discord.Embed(title="Slot Machine Result", color=discord.Color.blurple())
            embed.add_field(name="Slot 1", value=slot_results[0], inline=True)
            embed.add_field(name="Slot 2", value=slot_results[1], inline=True)
            embed.add_field(name="Slot 3", value=slot_results[2], inline=True)
            embed.add_field(name="Reward", value=f"${total_reward}", inline=False)
            embed.add_field(name="Risk Event", value=risk_message if risk_message else "None", inline=False)
            embed.add_field(name="Jackpot", value=f"${jackpot_result}", inline=False)
            embed.add_field(name="Occupant", value=f"{ctx.author.mention}", inline=False)
            await ctx.send(embed=embed)

        # Dragon fight sequence
        if dragon_count > 0:
            if dragon_count == 1:
                await ctx.send(
                    f"{ctx.author.display_name}, you rolled a dragon! Initiating attack sequence..."
                )
            else:
                await ctx.send(
                    f"You rolled {dragon_count} dragons! Initiating {dragon_count} attack sequences..."
                )

            for i in range(1, dragon_count + 1):
                await asyncio.sleep(1)
                action = random.randint(1, 5)
                await self._apply_dragon_event(ctx, seat, action)
                await self._render_and_resolve_dragon(ctx, seat, image_index=i, action=action)

    # ============================================================
    # Slots logic
    # ============================================================

    async def _roll_slots_and_risk(self, seat: int) -> Tuple[int, List[str], int, str, int, int]:
        fruit_values = {
            "🐉": 1000,
            "🍒": 1000,
            "🍎": 2000,
            "🍊": 2500,
            "🍏": 3000,
            "🍓": 4000,
            "🍍": 6500,
        }
        emojis = list(fruit_values.keys())
        weights = [1, 4, 3, 2, 1, 1, 1]

        slot_results = random.choices(emojis, weights=weights, k=3)
        fruit_counts = Counter(slot_results)
        dragon_count = slot_results.count("🐉")

        if len(set(slot_results)) == 1:
            total_reward = 4 * fruit_values[slot_results[0]]
        elif len(set(slot_results)) == 2 and any(count == 2 for count in fruit_counts.values()):
            total_reward = 2 * sum(
                fruit_values[fruit] for fruit, count in fruit_counts.items() if count == 2
            )
        else:
            total_reward = 0

        risk_message = ""
        bonus = 0
        penalty = 0

        # 10% risk event
        if random.randint(1, 100) <= 10:
            if random.choice(["bonus", "penalty"]) == "bonus":
                bonus = random.randint(500, 2000)
                total_reward += bonus
                risk_message = f"Risk event triggered! You got a bonus of ${bonus}."
            else:
                penalty = random.randint(100, 2000)
                async with self.bot.pool.acquire() as connection:
                    jackpot_now = await connection.fetchval(
                        "SELECT jackpot FROM dragonslots WHERE seat = $1", seat
                    )
                    jackpot_now = int(jackpot_now or 0)

                    if jackpot_now < penalty:
                        await connection.execute(
                            "UPDATE dragonslots SET jackpot = 0 WHERE seat = $1", seat
                        )
                    else:
                        await connection.execute(
                            "UPDATE dragonslots SET jackpot = jackpot - $1 WHERE seat = $2",
                            penalty,
                            seat,
                        )
                risk_message = f"Risk event triggered! You suffered a penalty of ${penalty}."

        return total_reward, slot_results, dragon_count, risk_message, bonus, penalty

    # ============================================================
    # Dragon logic
    # ============================================================

    async def _apply_dragon_event(self, ctx: commands.Context, seat: int, action: int) -> None:
        """DB-first updates with clamping; narration kept."""
        async with self.bot.pool.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT dragon, player FROM dragonslots WHERE seat = $1", seat
            )
            if not row:
                return

            if action == 1:
                await connection.execute(
                    "UPDATE dragonslots "
                    "SET dragon = GREATEST(dragon - 5, 0), player = GREATEST(player - 5, 0) "
                    "WHERE seat = $1",
                    seat,
                )
                row2 = await connection.fetchrow(
                    "SELECT dragon, player FROM dragonslots WHERE seat = $1", seat
                )
                await ctx.send(
                    f"{ctx.author.display_name}, you attacked the dragon for **5 DMG!** "
                    f"It now has {row2['dragon']}! You took **5 DMG** and now have {row2['player']}."
                )

            elif action == 2:
                await ctx.send(f"{ctx.author.display_name}, you defended yourself. You took 0 damage!")

            elif action == 3:
                dmg = 5 if random.randint(1, 10) <= 5 else 10
                await connection.execute(
                    "UPDATE dragonslots SET player = GREATEST(player - $1, 0) WHERE seat = $2",
                    dmg,
                    seat,
                )
                player_now = await connection.fetchval(
                    "SELECT player FROM dragonslots WHERE seat = $1", seat
                )
                if dmg == 5:
                    await ctx.send(
                        f"{ctx.author.display_name}, you tripped and took **5 DMG!** Your HP is now {player_now}!"
                    )
                else:
                    await ctx.send(
                        f"Oops, {ctx.author.display_name}! You stumbled and took **10 DMG**. Your HP is now {player_now}!"
                    )

            elif action == 4:
                await connection.execute(
                    "UPDATE dragonslots "
                    "SET dragon = GREATEST(dragon - 10, 0), player = GREATEST(player - 5, 0) "
                    "WHERE seat = $1",
                    seat,
                )
                row2 = await connection.fetchrow(
                    "SELECT dragon, player FROM dragonslots WHERE seat = $1", seat
                )
                await ctx.send(
                    f"{ctx.author.display_name}, you cast a spell! The dragon took **10 DMG** and you took **5 DMG**. "
                    f"Dragon HP: {row2['dragon']}; Your HP: {row2['player']}."
                )

            elif action == 5:
                await connection.execute(
                    "UPDATE dragonslots SET player = GREATEST(player - 5, 0) WHERE seat = $1",
                    seat,
                )
                player_now = int(
                    await connection.fetchval(
                        "SELECT player FROM dragonslots WHERE seat = $1", seat
                    )
                    or 0
                )
                heal = 10 if random.randint(1, 11) <= 5 else 15
                new_hp = min(player_now + heal, 100)
                await connection.execute(
                    "UPDATE dragonslots SET player = $1 WHERE seat = $2", new_hp, seat
                )
                gained = max(new_hp - player_now, 0)
                await ctx.send(
                    f"{ctx.author.display_name}, you healed for **{gained} HP!** Your HP is now {new_hp}."
                )

    def _draw_action_highlight(self, draw: ImageDraw.ImageDraw, action: int) -> None:
        locations = [
            [(228, 369), (354, 402)],
            [(228, 402), (354, 437)],
            [(425, 369), (500, 402)],
            [(425, 402), (590, 437)],
            [(615, 369), (710, 402)],
        ]
        draw.rounded_rectangle(locations[action - 1], radius=20, outline="red")

    async def _fetch_bytes(self, url: str) -> bytes:
        async with self.http.get(url, allow_redirects=True) as resp:
            resp.raise_for_status()

            ctype = (resp.headers.get("Content-Type") or "").lower()
            data = await resp.read()

            # Strong guards (Imgur/CDN sometimes returns HTML on bot-like requests)
            if "image" not in ctype:
                raise ValueError(f"URL did not return an image. content-type={ctype}")

            # magic bytes check (jpeg/png/webp/gif)
            if not (
                data.startswith(b"\xff\xd8\xff") or          # jpeg
                data.startswith(b"\x89PNG\r\n\x1a\n") or     # png
                data.startswith(b"RIFF") or                  # webp (RIFF....WEBP)
                data.startswith(b"GIF87a") or data.startswith(b"GIF89a")
            ):
                raise ValueError("Response is not a valid image file (magic bytes mismatch).")

            return data


    async def _load_dragon_background(self) -> Image.Image:
        # Try remote URLs
        for _ in range(max(1, len(DRAGON_BG_URLS))):
            if DRAGON_BG_URLS:
                url = random.choice(DRAGON_BG_URLS)
                try:
                    img_bytes = await self._fetch_bytes(url)
                    img = Image.open(io.BytesIO(img_bytes))

                    # Normalize
                    img = img.convert("RGBA")

                    # Your overlay coordinates assume 800x450
                    if img.size != (800, 450):
                        img = img.resize((800, 450), Image.NEAREST)

                    return img
                except Exception:
                    pass

        # Local fallback
        local = self._resolve_path(DRAGON_BG_FALLBACK_LOCAL or "")
        if local:
            img = Image.open(local).convert("RGBA")
            if img.size != (800, 450):
                img = img.resize((800, 450), Image.NEAREST)
            return img

        # Last resort (not transparent)
        return Image.new("RGBA", (800, 450), (20, 20, 20, 255))


    async def _render_and_resolve_dragon(
        self, ctx: commands.Context, seat: int, image_index: int, action: int
    ) -> None:
        # Get current HP from DB
        async with self.bot.pool.acquire() as connection:
            dragon_hp = int(
                await connection.fetchval("SELECT dragon FROM dragonslots WHERE seat = $1", seat)
                or 0
            )
            player_hp = int(
                await connection.fetchval("SELECT player FROM dragonslots WHERE seat = $1", seat)
                or 0
            )

        # Render optional
        if not self.text_mode.get(ctx.author.id, False):
            try:
                bg = await self._load_dragon_background()
                draw = ImageDraw.Draw(bg)

                hero_font = self._safe_font(FONT_TITLE_PATH, 33)
                dragon_font = self._safe_font(FONT_TITLE_PATH, 38)

                draw.text((80, 391), str(player_hp), font=hero_font, fill="cyan")
                draw.text((673, 10), str(dragon_hp), font=dragon_font, fill="white")
                self._draw_action_highlight(draw, action)

                buf = io.BytesIO()
                bg.save(buf, format="PNG")
                buf.seek(0)
                await ctx.send(file=discord.File(buf, filename=f"modified_image_{image_index}.png"))
            except Exception:
                await ctx.send("⚠️ (Dragon render failed, but the fight continues.)")

        await self._resolve_post_event(ctx, seat)

    async def _resolve_post_event(self, ctx: commands.Context, seat: int) -> None:
        async with self.bot.pool.acquire() as connection:
            dragon_hp = int(
                await connection.fetchval("SELECT dragon FROM dragonslots WHERE seat = $1", seat)
                or 0
            )
            player_hp = int(
                await connection.fetchval("SELECT player FROM dragonslots WHERE seat = $1", seat)
                or 0
            )

            if dragon_hp <= 0:
                jackpot_value = int(
                    await connection.fetchval("SELECT jackpot FROM dragonslots WHERE seat = $1", seat)
                    or 0
                )

                await connection.execute(
                    'UPDATE profile SET money = money + $1 WHERE "user" = $2',
                    jackpot_value,
                    ctx.author.id,
                )

                new_jackpot = random.randint(RESET_JACKPOT_MIN, RESET_JACKPOT_MAX)
                await connection.execute(
                    "UPDATE dragonslots SET dragon = $1, player = $2, jackpot = $3 WHERE seat = $4",
                    RESET_DRAGON_HP,
                    RESET_PLAYER_HP,
                    new_jackpot,
                    seat,
                )

                await ctx.send(
                    f"💎💎💎JACKPOT!💎💎💎 {ctx.author.mention}, you defeated the dragon and earned a jackpot of **${jackpot_value}**!"
                )
                return

            if player_hp <= 0:
                await connection.execute(
                    "UPDATE dragonslots SET dragon = $1, player = $2 WHERE seat = $3",
                    RESET_DRAGON_HP,
                    RESET_PLAYER_HP,
                    seat,
                )
                await ctx.send(
                    f"💀💀💀DEFEATED!💀💀💀 {ctx.author.display_name}, you were defeated by the dragon!"
                )

    # ============================================================
    # Seats / toggle
    # ============================================================

    @has_char()
    @slots.command()
    @locale_doc
    async def seats(self, ctx: commands.Context):
        _(
            """View information about slot machine seats.

            Displays the status of each seat, including occupant, dragon HP, player HP, and the current jackpot.
            """
        )
        embed = discord.Embed(title="Seat Information", color=discord.Color.blurple())
        try:
            async with self.bot.pool.acquire() as connection:
                for seat_number in range(1, 9):
                    seat_info = await connection.fetchrow(
                        "SELECT * FROM dragonslots WHERE seat = $1", seat_number
                    )
                    if not seat_info:
                        continue

                    occupant_id = seat_info["occupant"]
                    dragon_hp = seat_info["dragon"]
                    player_hp = seat_info["player"]
                    jackpot = seat_info["jackpot"]

                    if occupant_id is None:
                        value = (
                            f"Occupant: Seat free\n"
                            f"Dragon HP: {dragon_hp} 🐉\n"
                            f"Player HP: {player_hp} 👤\n"
                            f"Jackpot: {jackpot} 💰"
                        )
                    else:
                        user = await self.bot.fetch_user(occupant_id)
                        value = (
                            f"Occupant: {user.display_name}\n"
                            f"Dragon HP: {dragon_hp} 🐉\n"
                            f"Player HP: {player_hp} 👤\n"
                            f"Jackpot: {jackpot} 💰"
                        )

                    embed.add_field(name=f"Seat #{seat_number}", value=value, inline=False)

            await ctx.send(embed=embed)
        except Exception as e:
            await ctx.send(f"An error occurred: {e}")

    @has_char()
    @slots.command(name="text")
    @locale_doc
    async def toggleslottxt(self, ctx: commands.Context):
        _(
            """Toggle text-only mode for the slot machine game.

            When enabled, the slot machine will display results in plain text (no generated images).
            """
        )
        current = self.text_mode.get(ctx.author.id, False)
        self.text_mode[ctx.author.id] = not current
        mode = "Text-Only" if self.text_mode[ctx.author.id] else "Graphic"
        await ctx.send(f"{ctx.author.mention} Slot machine mode toggled. You are now in **{mode}** mode.")

    # ============================================================
    # Timeouts / activity
    # ============================================================

    def cog_unload(self):
        self.check_timeouts.cancel()
        if self.http and not self.http.closed:
            self.bot.loop.create_task(self.http.close())

    @tasks.loop(seconds=60)
    async def check_timeouts(self):
        now = datetime.datetime.now(timezone.utc)
        async with self.bot.pool.acquire() as connection:
            rows = await connection.fetch("SELECT seat, occupant, last_activity FROM dragonslots")
            for seat_info in rows:
                seat_number = seat_info["seat"]
                occupant_id = seat_info["occupant"]
                last_activity = seat_info["last_activity"]

                if occupant_id and last_activity and (now - last_activity).total_seconds() > self.timeout_duration:
                    await connection.execute(
                        "UPDATE dragonslots SET occupant = NULL, last_activity = NULL WHERE seat = $1",
                        seat_number,
                    )
                    user = await self.bot.fetch_user(occupant_id)
                    if user:
                        try:
                            await user.send(
                                f"{user.mention} You have been automatically removed from seat #{seat_number} due to inactivity."
                            )
                        except discord.Forbidden:
                            pass

    async def update_last_activity(self, occupant_id: int):
        async with self.bot.pool.acquire() as connection:
            await connection.execute(
                "UPDATE dragonslots SET last_activity = $1 WHERE occupant = $2",
                datetime.datetime.now(timezone.utc),
                occupant_id,
            )

    async def _get_user_seat(self, user_id: int) -> Optional[int]:
        try:
            async with self.bot.pool.acquire() as connection:
                row = await connection.fetchrow(
                    "SELECT seat FROM dragonslots WHERE occupant = $1", user_id
                )
                return int(row["seat"]) if row else None
        except Exception:
            return None

    async def _get_user_money(self, user_id: int) -> Optional[int]:
        try:
            async with self.bot.pool.acquire() as connection:
                return await connection.fetchval('SELECT money FROM profile WHERE "user" = $1', user_id)
        except Exception:
            return None

    # ============================================================
    # Take / Leave seat
    # ============================================================

    @slots.command()
    @user_cooldown(300)
    @has_char()
    @locale_doc
    async def takeseat(self, ctx: commands.Context, seat_number: int):
        _(
            """Take a seat in the slot machine game.

             - The number of the seat you wish to occupy (1-8).

            Occupy an available seat to participate in the slot machine game. You must leave your current seat before taking a new one.
            """
        )

        if seat_number < MIN_SEAT or seat_number > MAX_SEAT:
            return await ctx.send("Seat number must be between 1 and 8.")

        if ctx.author.id in self.captcha_lock:
            await ctx.send("You are currently locked by CAPTCHA verification.")
            return

        try:
            async with self.bot.pool.acquire() as connection:
                current_seat = await connection.fetchrow(
                    "SELECT * FROM dragonslots WHERE occupant = $1", ctx.author.id
                )
                if current_seat:
                    await ctx.send(
                        f"{ctx.author.display_name}, you are already occupying seat #{current_seat['seat']}. Please leave that seat before taking a new one."
                    )
                    return

                seat_info = await connection.fetchrow(
                    "SELECT * FROM dragonslots WHERE seat = $1", seat_number
                )
                if not seat_info:
                    return await ctx.send(f"Seat #{seat_number} does not exist.")

                if seat_info["occupant"] is None:
                    await connection.execute(
                        "UPDATE dragonslots SET occupant = $1, last_activity = $2 WHERE seat = $3",
                        ctx.author.id,
                        datetime.datetime.now(timezone.utc),
                        seat_number,
                    )
                    await ctx.send(
                        f"{ctx.author.mention} has taken seat #{seat_number}! Will be kicked after 10 minutes of inactivity."
                    )
                    await self.update_last_activity(ctx.author.id)
                else:
                    occupant = await self.bot.fetch_user(seat_info["occupant"])
                    await ctx.send(
                        f"Sorry, seat #{seat_number} is already taken by {occupant.display_name}."
                    )

        except Exception as e:
            await ctx.send(f"An error occurred: {e}")

    @has_char()
    @slots.command()
    @locale_doc
    async def leaveseat(self, ctx: commands.Context):
        _(
            """Leave your current seat in the slot machine game.

            Use this command to vacate your seat, allowing others to occupy it.
            """
        )

        # Force captcha on leave (like your earlier intent)
        ok = await self._do_captcha_verify(ctx, force=True)
        if not ok:
            return

        try:
            async with self.bot.pool.acquire() as connection:
                seat_info = await connection.fetchrow(
                    "SELECT * FROM dragonslots WHERE occupant = $1", ctx.author.id
                )
                if seat_info:
                    seat_number = seat_info["seat"]
                    await connection.execute(
                        "UPDATE dragonslots SET occupant = NULL, last_activity = NULL WHERE seat = $1",
                        seat_number,
                    )
                    await ctx.send(f"{ctx.author.mention} has left seat #{seat_number}!")
                else:
                    await ctx.send(
                        f"{ctx.author.display_name}, you are not currently occupying any seat."
                    )
        except Exception as e:
            await ctx.send(f"An error occurred: {e}")

    # ============================================================
    # Captcha
    # ============================================================

    def is_user_captcha_locked(self, user_id: int) -> bool:
        return user_id in self.captcha_lock

    async def send_locked_message(self, user: discord.User):
        locked_channel = self.bot.get_channel(1140210404627337256)
        if locked_channel:
            await locked_channel.send(
                f"{user.name}#{user.discriminator} failed the CAPTCHA and is now locked."
            )

    async def _do_captcha_verify(self, ctx: commands.Context, force: bool = False) -> bool:
        """
        Sends captcha, waits for user response.
        On timeout: kicks user from seat and keeps them locked until $unlock.
        `force` kept for clarity (always requests captcha when called).
        """
        try:
            captcha_text = self.generate_distorted_captcha()
            self.captcha_lock[ctx.author.id] = captcha_text

            await ctx.send(
                f"{ctx.author.mention} to prevent botting, enter the CAPTCHA Text as printed below. You have {CAPTCHA_TIMEOUT_SECONDS} seconds",
                file=discord.File("captcha.png"),
            )

            try:
                await self.bot.wait_for(
                    "message",
                    check=lambda msg: msg.author == ctx.author
                    and msg.content.lower() == captcha_text.lower(),
                    timeout=CAPTCHA_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                try:
                    async with self.bot.pool.acquire() as connection:
                        seat_info = await connection.fetchrow(
                            "SELECT * FROM dragonslots WHERE occupant = $1", ctx.author.id
                        )
                        if seat_info:
                            await connection.execute(
                                "UPDATE dragonslots SET occupant = NULL, last_activity = NULL WHERE seat = $1",
                                seat_info["seat"],
                            )
                    await self.send_locked_message(ctx.author)
                    await ctx.send(
                        "You are now CAPTCHA Locked. Use `$unlock` to unlock the CAPTCHA."
                    )
                except Exception as e:
                    await ctx.send(f"An error occurred: {e}")
                return False

            await ctx.send("CAPTCHA Verification Successful")
            # Important: remove lock on success
            self.captcha_lock.pop(ctx.author.id, None)
            return True

        except Exception as e:
            await ctx.send(str(e))
            return False

    def generate_distorted_captcha(self) -> str:
        captcha_text = "".join(
            random.choices(string.ascii_letters + string.digits, k=6)
        ).replace("l", "L")
        width, height = 300, 100
        image = Image.new("RGB", (width, height), "grey")
        draw = ImageDraw.Draw(image)

        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 40)
        letter_colors = ["red", "green", "blue", "purple", "orange"]
        outline_thickness = 1

        for _ in range(1000):
            x = random.randint(0, width - 1)
            y = random.randint(0, height - 1)
            draw.point((x, y), fill=random.choice(letter_colors))

        for _ in range(50):
            x = random.uniform(0, width)
            y = random.uniform(0, height)
            shape_type = random.choice(["ellipse", "line"])
            shape_color = random.choice(letter_colors)
            size = random.randint(2, 5)
            self.draw_random_shape(draw, x, y, shape_type, shape_color, size, outline_thickness)

        for i, char in enumerate(captcha_text):
            x = i * width / 6
            y = random.uniform(0, height / 2)
            draw.text((x, y), char, font=font, fill=random.choice(letter_colors))

        image = image.filter(ImageFilter.GaussianBlur(1)).filter(ImageFilter.CONTOUR)
        image.save("captcha.png")
        return captcha_text

    def draw_random_shape(self, draw, x, y, shape_type, shape_color, size, outline_thickness):
        if shape_type == "ellipse":
            draw.ellipse((x, y, x + size, y + size), outline=shape_color, width=outline_thickness)
        else:
            x2, y2 = x + random.randint(10, 30), y + random.randint(10, 30)
            draw.line([(x, y), (x2, y2)], fill=shape_color, width=outline_thickness)

    @commands.command(hidden=True)
    @has_char()
    @locale_doc
    async def unlock(self, ctx: commands.Context):
        _(
            """Unlock yourself from CAPTCHA lock by completing a CAPTCHA.

            If you are locked due to failed CAPTCHA attempts, use this command to verify and unlock yourself.
            """
        )
        if ctx.author.id not in self.captcha_lock:
            return await ctx.send("You are not currently locked by CAPTCHA verification.")

        ok = await self._do_captcha_verify(ctx, force=True)
        if ok:
            self.captcha_lock.pop(ctx.author.id, None)

    @is_gm()
    @commands.command(hidden=True)
    @locale_doc
    async def gmunlock(self, ctx: commands.Context, discord_id: int):
        _(
            """Unlock a user from CAPTCHA lock.

             - The Discord ID of the user to unlock.

            (Game Master only)
            """
        )
        if discord_id in self.captcha_lock:
            val = self.captcha_lock[discord_id]
            del self.captcha_lock[discord_id]
            await ctx.send(f"You have unlocked {val} ({discord_id}).")
        else:
            await ctx.send("User not found.")

    @is_gm()
    @commands.command(hidden=True)
    async def captcha(self, ctx: commands.Context):
        try:
            _ = self.generate_distorted_captcha()
            await ctx.send(
                "Enter the CAPTCHA Text below. You have 60 seconds",
                file=discord.File("captcha.png"),
            )
        except Exception as e:
            await ctx.send(str(e))

    # ============================================================
    # GM / DB reset tools (unchanged behavior)
    # ============================================================

    async def init_database(self):
        async with self.bot.pool.acquire() as connection:
            await connection.execute("DELETE FROM dragonslots")
            for seat_number in range(1, 9):
                jackpot = random.randint(RESET_JACKPOT_MIN, RESET_JACKPOT_MIN + 2000)
                await connection.execute(
                    "INSERT INTO dragonslots(seat, dragon, player, jackpot) VALUES($1, $2, $3, $4)",
                    seat_number,
                    RESET_DRAGON_HP,
                    RESET_PLAYER_HP,
                    jackpot,
                )

    @is_gm()
    @commands.command(hidden=True)
    @locale_doc
    async def gmdragonslotreset(self, ctx: commands.Context):
        _(
            """Reset the dragon slots to their initial state.

            (Game Master only)

            Resets all dragon slots, clearing occupants and restoring default HP and jackpot values.
            """
        )
        try:
            await self.init_database()
            await ctx.send("Dragon slots have been reset successfully!")
        except Exception as e:
            await ctx.send(f"An error occurred: {e}")

    @is_gm()
    @commands.command(hidden=True)
    @locale_doc
    async def gmjpforce(self, ctx: commands.Context):
        _(
            """Forcefully increase the jackpot for all slot seats.

            (Game Master only)
            """
        )
        try:
            async with self.bot.pool.acquire() as connection:
                seat_numbers = await connection.fetch("SELECT seat FROM dragonslots")
                for seat_info in seat_numbers:
                    seat_number = seat_info["seat"]
                    random_value = random.randint(10000, 12000)
                    await connection.execute(
                        "UPDATE dragonslots SET jackpot = jackpot + $1 WHERE seat = $2",
                        random_value,
                        seat_number,
                    )
        except Exception as e:
            await ctx.send(str(e))

    async def increase_jackpot_periodically(self, ctx: commands.Context):
        while True:
            try:
                async with self.bot.pool.acquire() as connection:
                    seat_numbers = await connection.fetch("SELECT seat FROM dragonslots")
                    for seat_info in seat_numbers:
                        seat_number = seat_info["seat"]
                        occupied = await connection.fetchval(
                            "SELECT seat FROM dragonslots WHERE seat = $1 AND occupant IS NOT NULL",
                            seat_number,
                        )
                        if not occupied:
                            random_value = random.randint(10000, 12000)
                            await connection.execute(
                                "UPDATE dragonslots SET jackpot = jackpot + $1 WHERE seat = $2",
                                random_value,
                                seat_number,
                            )
                await asyncio.sleep(21600)
            except Exception as e:
                await ctx.send(f"An error occurred while increasing jackpot: {e}")
                await asyncio.sleep(3600)

    @is_gm()
    @commands.command(hidden=True)
    @locale_doc
    async def gmjptimer(self, ctx: commands.Context):
        _(
            """Start the periodic increase of jackpot.

            (Game Master only)
            """
        )
        self.bot.loop.create_task(self.increase_jackpot_periodically(ctx))
        await ctx.send("Jackpot increase has been started.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Slots(bot))

