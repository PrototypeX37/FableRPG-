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
import asyncio
import copy
import datetime
import json


import requests
from io import BytesIO

from discord.ext.commands import CommandError

from classes.classes import from_string as class_from_string

import pytesseract
import os
import platform
import re
import statistics
import sys
import time

from collections import defaultdict, deque
from functools import partial
from zoneinfo import ZoneInfo

import aiohttp
import io

from PIL import Image, ImageEnhance, ImageOps, ImageFilter
from openai import AsyncOpenAI

import discord
import distro
import humanize
import pkg_resources as pkg
import requests

from discord.ext import commands

from classes.converters import ImageFormat, ImageUrl
from cogs.help import chunks
from cogs.shard_communication import user_on_cooldown as user_cooldown
from utils import random
from utils.checks import ImgurUploadError, has_char, user_is_patron, is_gm
from utils.i18n import _, locale_doc
from utils.misc import nice_join
from utils.shell import get_cpu_name

def load_whitelist():
    with open('whitelist.json', 'r') as file:
        return json.load(file)

def save_whitelist(data):
    with open('whitelist.json', 'w') as file:
        json.dump(data, file, indent=4)

class PaginatorView(discord.ui.View):
    def __init__(self, ctx, pages, start_page=0, timeout=60):
        super().__init__(timeout=timeout)
        self.ctx = ctx
        self.pages = pages
        self.current_page = start_page
        self.message = None  # we will set this after sending

    async def on_timeout(self):
        """
        Called when the View times out (no interaction for `timeout` seconds).
        We'll disable all buttons here.
        """
        for child in self.children:
            child.disabled = True
        if self.message:
            await self.message.edit(view=self)

    # --------------------------------------------------------------------------
    # Button callbacks
    # --------------------------------------------------------------------------

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.blurple)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Ensure only the command invoker can use buttons
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message(
                "This button isn't for you!", ephemeral=True
            )

        self.current_page = (self.current_page - 1) % len(self.pages)
        await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.red)
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Ensure only the command invoker can use buttons
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message(
                "This button isn't for you!", ephemeral=True
            )

        await interaction.message.delete()
        self.stop()  # end the interaction to clean up

    @discord.ui.button(label="Next", style=discord.ButtonStyle.blurple)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Ensure only the command invoker can use buttons
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message(
                "This button isn't for you!", ephemeral=True
            )

        self.current_page = (self.current_page + 1) % len(self.pages)
        await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)


class DailyConfirmView(discord.ui.View):
    def __init__(self, ctx, timeout=45):
        super().__init__(timeout=timeout)
        self.ctx = ctx
        self.value = None
        self.message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                "This confirmation isn't for you!", ephemeral=True
            )
            return False
        return True

    async def _finish(self, interaction, value, content):
        self.value = value
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content=content, view=self)
        self.stop()

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            await self.message.edit(
                content="Daily cancelled. You did not confirm in time.",
                view=self,
            )

    @discord.ui.button(label="Yes", style=discord.ButtonStyle.success)
    async def yes_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._finish(interaction, True, "Confirmed. Running your dailies...")

    @discord.ui.button(label="No", style=discord.ButtonStyle.danger)
    async def no_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._finish(interaction, False, "Daily cancelled.")


class DailyAttachManageView(discord.ui.View):
    def __init__(self, cog, ctx, timeout=120):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.ctx = ctx
        self.message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                "This daily attachment menu isn't for you!", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Add", style=discord.ButtonStyle.success)
    async def add_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.show_dailyattach_add_picker(interaction, self.ctx, self.message)

    @discord.ui.button(label="Remove", style=discord.ButtonStyle.primary)
    async def remove_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.show_dailyattach_remove_picker(interaction, self.ctx, self.message)

    @discord.ui.button(label="Clear", style=discord.ButtonStyle.danger)
    async def clear_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.clear_daily_attachments(self.ctx.author.id)
        await self.cog.refresh_dailyattach_message(self.ctx, self.message)
        await interaction.response.send_message("Daily attachments cleared.", ephemeral=True)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary)
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content=await self.cog.build_dailyattach_message(self.ctx),
            view=self,
        )

    @discord.ui.button(label="Close", style=discord.ButtonStyle.secondary)
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.message.delete()
        except discord.HTTPException:
            for child in self.children:
                child.disabled = True
            await interaction.response.edit_message(view=self)
        else:
            if not interaction.response.is_done():
                await interaction.response.defer()
        self.stop()


class DailyAttachSelect(discord.ui.Select):
    def __init__(self, cog, ctx, mode, source_message, options, max_values):
        self.cog = cog
        self.ctx = ctx
        self.mode = mode
        self.source_message = source_message
        super().__init__(
            placeholder=f"Choose commands to {mode}",
            min_values=1,
            max_values=max_values,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if self.mode == "add":
            attached = await self.cog.add_daily_attachments(self.ctx, self.values)
        else:
            attached = await self.cog.remove_daily_attachments(self.ctx, self.values)

        await self.cog.refresh_dailyattach_message(self.ctx, self.source_message)
        if attached:
            content = "Attached to daily: " + ", ".join(f"`{command}`" for command in attached)
        else:
            content = "No commands are attached to your daily now."
        await interaction.response.send_message(content, ephemeral=True)


class DailyAttachPickerView(discord.ui.View):
    def __init__(self, cog, ctx, mode, source_message, options, max_values, timeout=120):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.ctx = ctx
        self.mode = mode
        self.source_message = source_message
        self.add_item(DailyAttachSelect(cog, ctx, mode, source_message, options, max_values))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                "This daily attachment menu isn't for you!", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = DailyAttachManageView(self.cog, self.ctx)
        view.message = self.source_message
        await interaction.response.edit_message(
            content=await self.cog.build_dailyattach_message(self.ctx),
            view=view,
        )


class Miscellaneous(commands.Cog):
    DAILY_RESET_TZ = ZoneInfo("Europe/Paris")
    DAILY_RESET_HOUR = 1
    DAILY_ATTACHMENT_LIMITS = {
        0: 2,
        1: 4,
        2: 6,
        3: 10,
    }
    DAILY_COMMAND_CONFIG = {
        "cratesdaily": {
            "cooldown": 12 * 3600,
            "aliases": ("vote",),
            "label": "cratesdaily",
        },
        "boosterdaily": {
            "cooldown": "midnight",
            "aliases": ("donatordaily",),
            "label": "boosterdaily",
        },
        "steal": {
            "cooldown": 60 * 60,
            "class_requirement": "Thief",
            "label": "steal",
        },
        "date": {
            "cooldown": 12 * 3600,
            "label": "date",
        },
        "pray": {
            "cooldown": "midnight",
            "label": "pray",
        },
        "familyevent": {
            "cooldown": 30 * 60,
            "label": "familyevent",
        },
        "pve": {
            "cooldown": 1800,
            "label": "pve",
            "daily_limit_key": "pve",
            "daily_limit": 36,
        },
        "battletower fight": {
            "cooldown": 600,
            "aliases": ("bt fight",),
            "label": "bt fight",
            "daily_limit_key": "battletower_fight",
            "daily_limit": 108,
        },
        "pets train": {
            "cooldown": 1800,
            "label": "pets train",
        },
        "pets treat": {
            "cooldown": 1800,
            "label": "pets treat",
        },
        "pets pet": {
            "cooldown": 60,
            "label": "pets pet",
        },
        "pets play": {
            "cooldown": 300,
            "label": "pets play",
        },
        "pets feed": {
            "cooldown": 3600,
            "label": "pets feed",
            "kwargs": {"food_type": "basic food"},
        },
        "pets feed basic": {
            "command": "pets feed",
            "cooldown": 3600,
            "aliases": ("pets feed basic food",),
            "label": "pets feed basic",
            "kwargs": {"food_type": "basic food"},
        },
        "pets feed premium": {
            "command": "pets feed",
            "cooldown": 3600,
            "aliases": ("pets feed premium food",),
            "label": "pets feed premium",
            "kwargs": {"food_type": "premium food"},
        },
        "pets feed deluxe": {
            "command": "pets feed",
            "cooldown": 3600,
            "aliases": ("pets feed deluxe food",),
            "label": "pets feed deluxe",
            "kwargs": {"food_type": "deluxe food"},
        },
        "pets feed elemental": {
            "command": "pets feed",
            "cooldown": 3600,
            "aliases": ("pets feed elemental food",),
            "label": "pets feed elemental",
            "kwargs": {"food_type": "elemental food"},
        },
    }

    def __init__(self, bot):
        self.bot = bot
        self.talk_context = defaultdict(partial(deque, maxlen=3))
        self.conversations = {}
        self.ALLOWED_CHANNELS = {
            1145473586556055672,
            1152255240654045295,
            1149193023259951154
        }
        self.whitelist = load_whitelist()
        self._streaks_table_ready = False
        self._daily_attachments_table_ready = False

    async def get_imgur_url(self, url: str):
        async with self.bot.session.post(
                "https://api.imgur.com/3/image",
                headers={
                    "Authorization": f"Client-ID {self.bot.config.external.imgur_token}"
                },
                json={"image": url, "type": "url"},
        ) as r:
            json = await r.json()
            try:
                short_url = json["data"]["link"]
            except KeyError:
                raise ImgurUploadError()
        return short_url

    @commands.command()
    async def wiki(self, ctx):
        await ctx.send("https://wiki.fablerpg.xyz")

    async def ensure_daily_attachments_table(self):
        if self._daily_attachments_table_ready:
            return

        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_command_attachments (
                    user_id BIGINT NOT NULL,
                    command_name TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (user_id, command_name)
                );
                """
            )
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS daily_command_attachments_user_position_idx
                    ON daily_command_attachments (user_id, position);
                """
            )
        self._daily_attachments_table_ready = True

    def _daily_config_aliases(self):
        aliases = {}
        for command_name, config in self.DAILY_COMMAND_CONFIG.items():
            aliases[command_name] = command_name
            aliases[f"${command_name}"] = command_name
            for alias in config.get("aliases", ()):
                aliases[alias] = command_name
                aliases[f"${alias}"] = command_name
        return aliases

    def _normalize_daily_attachment(self, command_name):
        normalized = command_name.strip().lower()
        return self._daily_config_aliases().get(normalized)

    def _normalize_daily_attachments(self, command_names):
        aliases = self._daily_config_aliases()
        tokens = [command.strip().lower() for command in command_names if command.strip()]
        normalized_commands = []
        invalid_commands = []
        index = 0

        while index < len(tokens):
            match = None
            matched_text = None
            matched_length = 0

            for length in range(min(4, len(tokens) - index), 0, -1):
                candidate = " ".join(tokens[index:index + length])
                candidate = candidate.lstrip("$")
                normalized = aliases.get(candidate) or aliases.get(f"${candidate}")
                if normalized:
                    match = normalized
                    matched_text = candidate
                    matched_length = length
                    break

            if match:
                if match not in normalized_commands:
                    normalized_commands.append(match)
                index += matched_length
            else:
                invalid_commands.append(matched_text or tokens[index])
                index += 1

        return normalized_commands, invalid_commands

    async def get_daily_attachment_limit(self, ctx):
        limit = self.DAILY_ATTACHMENT_LIMITS[0]
        try:
            tier = await self.bot.pool.fetchval(
                'SELECT "tier" FROM profile WHERE "user"=$1;', ctx.author.id
            )
            tier = int(tier or 0)
        except (TypeError, ValueError):
            tier = 0

        if tier >= 3:
            limit = self.DAILY_ATTACHMENT_LIMITS[3]
        elif tier == 2:
            limit = self.DAILY_ATTACHMENT_LIMITS[2]
        elif tier == 1:
            limit = self.DAILY_ATTACHMENT_LIMITS[1]

        try:
            if await user_is_patron(self.bot, ctx.author, "silver"):
                return self.DAILY_ATTACHMENT_LIMITS[3]
            if await user_is_patron(self.bot, ctx.author, "bronze"):
                return max(limit, self.DAILY_ATTACHMENT_LIMITS[2])
            if await user_is_patron(self.bot, ctx.author, "basic"):
                return max(limit, self.DAILY_ATTACHMENT_LIMITS[1])
        except Exception:
            pass

        return limit

    async def get_daily_attachments(self, user_id):
        await self.ensure_daily_attachments_table()
        rows = await self.bot.pool.fetch(
            """
            SELECT command_name
            FROM daily_command_attachments
            WHERE user_id=$1
            ORDER BY position ASC, created_at ASC;
            """,
            user_id,
        )
        return [row["command_name"] for row in rows]

    async def save_daily_attachments(self, user_id, command_names):
        await self.ensure_daily_attachments_table()
        async with self.bot.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM daily_command_attachments WHERE user_id=$1;",
                    user_id,
                )
                for position, command_name in enumerate(command_names, start=1):
                    await conn.execute(
                        """
                        INSERT INTO daily_command_attachments (user_id, command_name, position)
                        VALUES ($1, $2, $3);
                        """,
                        user_id,
                        command_name,
                        position,
                    )

    async def clear_daily_attachments(self, user_id):
        await self.ensure_daily_attachments_table()
        await self.bot.pool.execute(
            "DELETE FROM daily_command_attachments WHERE user_id=$1;",
            user_id,
        )

    async def add_daily_attachments(self, ctx, command_names):
        limit = await self.get_daily_attachment_limit(ctx)
        attached_commands = await self.get_daily_attachments(ctx.author.id)
        merged_commands = attached_commands[:]
        for command_name in command_names:
            if command_name not in merged_commands:
                merged_commands.append(command_name)
        merged_commands = merged_commands[:limit]
        await self.save_daily_attachments(ctx.author.id, merged_commands)
        return merged_commands

    async def remove_daily_attachments(self, ctx, command_names):
        attached_commands = await self.get_daily_attachments(ctx.author.id)
        remaining_commands = [
            command_name
            for command_name in attached_commands
            if command_name not in command_names
        ]
        await self.save_daily_attachments(ctx.author.id, remaining_commands)
        return remaining_commands

    async def build_dailyattach_message(self, ctx):
        limit = await self.get_daily_attachment_limit(ctx)
        attached_commands = await self.get_daily_attachments(ctx.author.id)
        available = ", ".join(
            f"`{config.get('label', command_name)}`"
            for command_name, config in self.DAILY_COMMAND_CONFIG.items()
        )

        if attached_commands:
            active = attached_commands[:limit]
            inactive = attached_commands[limit:]
            message = _("Attached to daily: {commands}\nSlots: **{used}/{limit}**").format(
                commands=", ".join(f"`{command}`" for command in active),
                used=len(active),
                limit=limit,
            )
            if inactive:
                message += _("\nInactive because they exceed your current slot limit: {commands}").format(
                    commands=", ".join(f"`{command}`" for command in inactive)
                )
        else:
            message = _("No commands are attached to your daily yet.\nSlots: **0/{limit}**").format(
                limit=limit
            )

        message += _("\nAvailable commands: {commands}").format(commands=available)
        message += _("\nUse the buttons below, or `{prefix}dailyattach add pray date`.").format(
            prefix=ctx.clean_prefix
        )
        return message

    async def refresh_dailyattach_message(self, ctx, message):
        if message is None:
            return
        view = DailyAttachManageView(self, ctx)
        view.message = message
        try:
            await message.edit(content=await self.build_dailyattach_message(ctx), view=view)
        except discord.HTTPException:
            pass

    def dailyattach_options(self, command_names):
        return [
            discord.SelectOption(
                label=self.DAILY_COMMAND_CONFIG[command_name].get("label", command_name),
                value=command_name,
            )
            for command_name in command_names
        ]

    async def show_dailyattach_add_picker(self, interaction, ctx, source_message):
        limit = await self.get_daily_attachment_limit(ctx)
        attached_commands = await self.get_daily_attachments(ctx.author.id)
        remaining_slots = limit - len(attached_commands)
        if remaining_slots <= 0:
            return await interaction.response.send_message(
                f"You already use all **{limit}** daily attachment slots.",
                ephemeral=True,
            )

        choices = [
            command_name
            for command_name in self.DAILY_COMMAND_CONFIG
            if command_name not in attached_commands
        ]
        if not choices:
            return await interaction.response.send_message(
                "There are no more commands available to attach.",
                ephemeral=True,
            )

        options = self.dailyattach_options(choices[:25])
        view = DailyAttachPickerView(
            self,
            ctx,
            "add",
            source_message,
            options,
            min(remaining_slots, len(options)),
        )
        await interaction.response.edit_message(
            content=f"Choose up to **{remaining_slots}** command(s) to attach.",
            view=view,
        )

    async def send_dailyattach_add_picker(self, ctx):
        limit = await self.get_daily_attachment_limit(ctx)
        attached_commands = await self.get_daily_attachments(ctx.author.id)
        remaining_slots = limit - len(attached_commands)
        if remaining_slots <= 0:
            return await ctx.send(f"You already use all **{limit}** daily attachment slots.")

        choices = [
            command_name
            for command_name in self.DAILY_COMMAND_CONFIG
            if command_name not in attached_commands
        ]
        if not choices:
            return await ctx.send("There are no more commands available to attach.")

        options = self.dailyattach_options(choices[:25])
        view = DailyAttachPickerView(
            self,
            ctx,
            "add",
            None,
            options,
            min(remaining_slots, len(options)),
        )
        message = await ctx.send(
            f"Choose up to **{remaining_slots}** command(s) to attach.",
            view=view,
        )
        view.source_message = message
        for child in view.children:
            if isinstance(child, DailyAttachSelect):
                child.source_message = message

    async def show_dailyattach_remove_picker(self, interaction, ctx, source_message):
        attached_commands = await self.get_daily_attachments(ctx.author.id)
        if not attached_commands:
            return await interaction.response.send_message(
                "You do not have any daily attachments to remove.",
                ephemeral=True,
            )

        options = self.dailyattach_options(attached_commands[:25])
        view = DailyAttachPickerView(
            self,
            ctx,
            "remove",
            source_message,
            options,
            len(options),
        )
        await interaction.response.edit_message(
            content="Choose command(s) to remove from daily.",
            view=view,
        )

    async def send_dailyattach_remove_picker(self, ctx):
        attached_commands = await self.get_daily_attachments(ctx.author.id)
        if not attached_commands:
            return await ctx.send("You do not have any daily attachments to remove.")

        options = self.dailyattach_options(attached_commands[:25])
        view = DailyAttachPickerView(
            self,
            ctx,
            "remove",
            None,
            options,
            len(options),
        )
        message = await ctx.send(
            "Choose command(s) to remove from daily.",
            view=view,
        )
        view.source_message = message
        for child in view.children:
            if isinstance(child, DailyAttachSelect):
                child.source_message = message

    def _daily_command_cooldown(self, config):
        cooldown = config["cooldown"]
        if cooldown == "midnight":
            return self.time_until_midnight()
        return int(cooldown)

    async def _check_daily_command_limit(self, ctx, config):
        command_key = config.get("daily_limit_key")
        limit = config.get("daily_limit")
        if not command_key or not limit:
            return True, None

        antiscript = self.bot.get_cog("AntiScript")
        if antiscript is None:
            return True, None

        normalized_key = antiscript.normalize_command_key(command_key)
        ok = await antiscript.check_and_increment_command_use(
            user_id=ctx.author.id,
            command_name=normalized_key,
            limit=int(limit),
            increment=0,
        )
        if ok:
            return True, (normalized_key, int(limit))
        return False, f"You've reached the daily threshold for `{normalized_key}`."

    async def _increment_daily_command_limit(self, ctx, track_info):
        if not track_info:
            return

        antiscript = self.bot.get_cog("AntiScript")
        if antiscript is None:
            return

        command_key, limit = track_info
        await antiscript.check_and_increment_command_use(
            user_id=ctx.author.id,
            command_name=command_key,
            limit=int(limit),
            increment=1,
        )

    async def run_daily_attached_commands(self, ctx, command_names=None):
        if command_names is None:
            command_names = await self.get_daily_attachments(ctx.author.id)

        limit = await self.get_daily_attachment_limit(ctx)
        command_names = command_names[:limit]
        if not command_names:
            return []

        character_data = await ctx.bot.pool.fetchrow(
            'SELECT class FROM profile WHERE "user"=$1;', ctx.author.id
        )
        user_classes = {
            type(c).__name__
            for c in map(class_from_string, character_data["class"])
        } if character_data and character_data["class"] else set()

        command_entries = []
        status_messages = []
        for command_name in command_names:
            config = self.DAILY_COMMAND_CONFIG.get(command_name)
            if not config:
                status_messages.append(f"`{command_name}`: command is no longer attachable")
                continue
            command_lookup = config.get("command", command_name)
            command = self.bot.get_command(command_lookup)
            if not command:
                command = next(
                    (
                        self.bot.get_command(alias)
                        for alias in (command_name, *config.get("aliases", ()))
                        if self.bot.get_command(alias)
                    ),
                    None,
                )
            if not command:
                status_messages.append(
                    f"`{config.get('label', command_name)}`: command is not loaded"
                )
                continue
            command_entries.append(
                {
                    "name": command_name,
                    "label": config.get("label", command_name),
                    "config": config,
                    "command": command,
                    "cooldown_key": command.qualified_name,
                }
            )

        async with ctx.bot.redis.pipeline() as pipe:
            for entry in command_entries:
                pipe.ttl(f"cd:{ctx.author.id}:{entry['cooldown_key']}")
            cooldowns = await pipe.execute()

        tasks = []

        for entry, current_cooldown in zip(command_entries, cooldowns):
            cmd_name = entry["label"]
            config = entry["config"]
            command = entry["command"]
            cooldown_key = entry["cooldown_key"]

            if current_cooldown != -2:
                remaining = self.format_time(current_cooldown)
                status_messages.append(f"`{cmd_name}`: {remaining} cooldown remaining")
                continue

            if class_req := config.get("class_requirement"):
                if class_req not in user_classes:
                    status_messages.append(f"`{cmd_name}`: Requires {class_req} class")
                    continue

            allowed, daily_track_info = await self._check_daily_command_limit(ctx, config)
            if not allowed:
                status_messages.append(f"`{cmd_name}`: {daily_track_info}")
                continue

            args = config.get("args", ())
            kwargs = config.get("kwargs", {})
            cooldown_seconds = self._daily_command_cooldown(config)
            cooldown_claimed = await ctx.bot.redis.execute_command(
                "SET",
                f"cd:{ctx.author.id}:{cooldown_key}",
                cooldown_key,
                "EX",
                cooldown_seconds,
                "NX",
            )
            if not cooldown_claimed:
                current_cooldown = await ctx.bot.redis.execute_command(
                    "TTL", f"cd:{ctx.author.id}:{cooldown_key}"
                )
                if current_cooldown == -1:
                    current_cooldown = cooldown_seconds
                    await ctx.bot.redis.execute_command(
                        "EXPIRE", f"cd:{ctx.author.id}:{cooldown_key}", current_cooldown
                    )
                elif current_cooldown == -2:
                    current_cooldown = cooldown_seconds
                remaining = self.format_time(current_cooldown)
                status_messages.append(f"`{cmd_name}`: {remaining} cooldown remaining")
                continue

            invoke_ctx = copy.copy(ctx)
            invoke_ctx.command = command
            invoke_ctx._daily_attachment_cooldown_claimed = True
            tasks.append((invoke_ctx.invoke(command, *args, **kwargs), daily_track_info))

        if tasks:
            results = await asyncio.gather(
                *(task for task, _daily_track_info in tasks),
                return_exceptions=True,
            )
            for result, (_task, daily_track_info) in zip(results, tasks):
                if isinstance(result, Exception):
                    status_messages.append(f"Attached command error: {str(result)}")
                else:
                    await self._increment_daily_command_limit(ctx, daily_track_info)

        return status_messages

    @has_char()
    @user_cooldown(1)
    @commands.hybrid_command()
    @locale_doc
    async def all(self, ctx):
        _("""Automatically invokes daily and your attached daily commands.

        This command will attempt to run `daily`, which will also run the commands
        you attached with `dailyattach`. If `daily` is on cooldown, this command
        will still try to run your attached commands directly.

        Usage:
          `$all`

        Note:
        - Commands that are on cooldown will be skipped
        - Attached command slots are limited by Patreon tier
        - This command itself has a cooldown of 1 second""")

        daily_command = self.bot.get_command("daily")
        if daily_command:
            await ctx.invoke(daily_command)
        try:
            await self.bot.reset_cooldown(ctx)
        except Exception:
            pass

    @has_char()
    @commands.group(
        name="dailyattach",
        aliases=["dailycommands", "dailycmd"],
        invoke_without_command=True,
        brief=_("Manage commands attached to daily"),
    )
    async def dailyattach(self, ctx):
        view = DailyAttachManageView(self, ctx)
        view.message = await ctx.send(
            await self.build_dailyattach_message(ctx),
            view=view,
        )

    @dailyattach.command(name="add", brief=_("Attach commands to daily"))
    async def dailyattach_add(self, ctx, *command_names):
        if not command_names:
            return await self.send_dailyattach_add_picker(ctx)

        normalized_commands, invalid_commands = self._normalize_daily_attachments(
            command_names
        )

        if invalid_commands:
            available = ", ".join(
                f"`{config.get('label', command_name)}`"
                for command_name, config in self.DAILY_COMMAND_CONFIG.items()
            )
            return await ctx.send(
                _("These commands cannot be attached: {commands}\nAvailable commands: {available}").format(
                    commands=", ".join(f"`{command}`" for command in invalid_commands),
                    available=available,
                )
            )

        limit = await self.get_daily_attachment_limit(ctx)
        attached_commands = await self.get_daily_attachments(ctx.author.id)
        unique_new_commands = [
            command_name
            for command_name in normalized_commands
            if command_name not in attached_commands
        ]
        if len(attached_commands) + len(unique_new_commands) > limit:
            return await ctx.send(
                _("You can attach up to **{limit}** commands with your current tier. Remove one first or upgrade your Patreon tier.").format(
                    limit=limit
                )
            )

        merged_commands = await self.add_daily_attachments(ctx, normalized_commands)

        await ctx.send(
            _("Attached to daily: {commands}").format(
                commands=", ".join(f"`{command}`" for command in merged_commands)
            )
        )

    @dailyattach.command(name="remove", aliases=["delete"], brief=_("Detach commands from daily"))
    async def dailyattach_remove(self, ctx, *command_names):
        if not command_names:
            return await self.send_dailyattach_remove_picker(ctx)

        normalized_commands, _invalid_commands = self._normalize_daily_attachments(
            command_names
        )
        if not normalized_commands:
            return await ctx.send(_("None of those commands are attached to daily."))

        remaining_commands = await self.remove_daily_attachments(ctx, normalized_commands)

        if remaining_commands:
            return await ctx.send(
                _("Attached to daily: {commands}").format(
                    commands=", ".join(f"`{command}`" for command in remaining_commands)
                )
            )
        await ctx.send(_("No commands are attached to your daily now."))

    @dailyattach.command(name="clear", brief=_("Remove all daily attachments"))
    async def dailyattach_clear(self, ctx):
        await self.clear_daily_attachments(ctx.author.id)
        await ctx.send(_("No commands are attached to your daily now."))

    def format_time(self, seconds):
        """Convert seconds to HH:MM:SS format."""
        seconds = max(int(seconds), 0)
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{int(hours):02}:{int(minutes):02}:{int(seconds):02}"

    def time_until_midnight(self):
        """Calculate seconds until the configured daily reset time."""
        _current_reset, next_reset = self._daily_reset_window()
        now = datetime.datetime.now(self.DAILY_RESET_TZ)
        return max(1, int((next_reset - now).total_seconds()))

    def _daily_reset_window(self):
        now = datetime.datetime.now(self.DAILY_RESET_TZ)
        current_reset = now.replace(
            hour=self.DAILY_RESET_HOUR,
            minute=0,
            second=0,
            microsecond=0,
        )
        if now < current_reset:
            current_reset -= datetime.timedelta(days=1)
        next_reset = current_reset + datetime.timedelta(days=1)
        return current_reset, next_reset

    async def _daily_claimed_since_reset(self, user_id):
        current_reset, _next_reset = self._daily_reset_window()
        current_reset_utc = current_reset.astimezone(
            datetime.timezone.utc
        ).replace(tzinfo=None)
        try:
            await self.ensure_streaks_table()
            last_daily = await self.bot.pool.fetchval(
                "SELECT last_daily FROM streaks WHERE user_id = $1;",
                user_id,
            )
        except Exception:
            return True
        if last_daily and last_daily.tzinfo is not None:
            last_daily = last_daily.astimezone(
                datetime.timezone.utc
            ).replace(tzinfo=None)
        return bool(last_daily and last_daily >= current_reset_utc)

    def _daily_window_key(self):
        current_reset, _next_reset = self._daily_reset_window()
        return current_reset.date().isoformat()

    async def ensure_streaks_table(self):
        if self._streaks_table_ready:
            return

        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS streaks (
                    user_id BIGINT PRIMARY KEY,
                    current_streak INTEGER NOT NULL DEFAULT 0,
                    highest_days INTEGER NOT NULL DEFAULT 0,
                    restore_points INTEGER NOT NULL DEFAULT 3,
                    last_daily TIMESTAMP,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                );
                """
            )
            await conn.execute(
                """
                ALTER TABLE streaks
                    ADD COLUMN IF NOT EXISTS current_streak INTEGER NOT NULL DEFAULT 0,
                    ADD COLUMN IF NOT EXISTS highest_days INTEGER NOT NULL DEFAULT 0,
                    ADD COLUMN IF NOT EXISTS restore_points INTEGER NOT NULL DEFAULT 3,
                    ADD COLUMN IF NOT EXISTS last_daily TIMESTAMP,
                    ADD COLUMN IF NOT EXISTS created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP NOT NULL DEFAULT NOW();
                """
            )
            await conn.execute(
                """
                UPDATE streaks
                SET current_streak = COALESCE(current_streak, 0),
                    highest_days = COALESCE(highest_days, 0),
                    restore_points = COALESCE(restore_points, 3);
                """
            )
        self._streaks_table_ready = True

    @has_char()
    @commands.hybrid_command(brief=_("Get your daily reward"))
    @locale_doc
    async def daily(self, ctx):
        _(
            """Get your daily reward. Depending on your streak, you will gain better rewards.

            After ten days, your rewards will reset. Day 11 and day 1 have the same rewards.
            The rewards will either be money (2/3 chance) or crates (1/3 chance).

            Special milestone rewards:
            Day 50: 1 Legendary Crate + 100,000 gold
            Day 100: 1 Fortune Crate + 150,000 gold
            Day 200: 100 Mystery Crates + 200,000 gold
            Day 300: 1 Divine Crate + 300,000 gold
            Day 400: 3 Fortune Crates + 400,000 gold
            Day 500: 2 Divine Crates + 500,000 gold
            
            **These milestones cycle every 500 days**


            Regular rewards:
            __Day 1__
            $50 or 1-6 common crates

            __Day 2__
            $100 or 1-5 common crates

            __Day 3__
            $200 or 1-4 common (99%) or uncommon (1%) crates

            __Day 4__
            $400 or 1-4 common (99%) or uncommon (1%) crates

            __Day 5__
            $800 or 1-4 common (99%) or uncommon (1%) crates

            __Day 6__
            $1,600 or 1-3 common (80%), uncommon (19%) or rare (1%) crates

            __Day 7__
            $3,200 or 1-2 uncommon (80%), rare (19%) or magic (1%) crates

            __Day 8__
            $6,400 or 1-2 uncommon (80%), rare (19%) or magic (1%) crates

            __Day 9__
            $12,800 or 1-2 uncommon (80%), rare (19%) or magic (1%) crates

            __Day 10__
            $25,600 or 1 rare (80%), magic (19%) or legendary (1%) crate

            If you don't use this command up to 48 hours after the first use, you will lose your streak.

            (This command has a cooldown until 1am Europe/Paris.)"""
        )

        try:
            if not getattr(ctx, "skip_daily_confirm", False):
                view = DailyConfirmView(ctx)
                view.message = await ctx.send(
                    f"{ctx.author.mention} Would you like to use all dailies?",
                    view=view,
                )
                await view.wait()
                if not view.value:
                    return

            daily_cooldown_key = f"cd:{ctx.author.id}:daily"
            daily_cooldown_seconds = self.time_until_midnight()
            daily_claimed = await self.bot.redis.execute_command(
                "SET",
                daily_cooldown_key,
                self._daily_window_key(),
                "EX",
                daily_cooldown_seconds,
                "NX",
            )
            if not daily_claimed:
                daily_cooldown = await self.bot.redis.execute_command(
                    "TTL", daily_cooldown_key
                )
                daily_value = await self.bot.redis.execute_command(
                    "GET", daily_cooldown_key
                )
                if isinstance(daily_value, (bytes, bytearray)):
                    daily_value = daily_value.decode()

                current_window = self._daily_window_key()
                if daily_value != current_window:
                    if not await self._daily_claimed_since_reset(ctx.author.id):
                        await self.bot.redis.execute_command("DEL", daily_cooldown_key)
                        daily_claimed = await self.bot.redis.execute_command(
                            "SET",
                            daily_cooldown_key,
                            current_window,
                            "EX",
                            daily_cooldown_seconds,
                            "NX",
                        )
                        if daily_claimed:
                            daily_cooldown = None
                    else:
                        await self.bot.redis.execute_command(
                            "SET",
                            daily_cooldown_key,
                            current_window,
                            "EX",
                            daily_cooldown_seconds,
                        )

                if daily_claimed:
                    pass
                elif daily_cooldown != -2:
                    daily_cooldown = daily_cooldown_seconds
                    await self.bot.redis.execute_command(
                        "EXPIRE", daily_cooldown_key, daily_cooldown
                    )
                else:
                    daily_cooldown = daily_cooldown_seconds

            if not daily_claimed:
                attached_status = await self.run_daily_attached_commands(ctx)
                status_messages = [
                    f"`daily`: {self.format_time(daily_cooldown)} cooldown remaining"
                ]
                status_messages.extend(attached_status)
                return await ctx.send(
                    _("Status Report:\n{status_report}").format(
                        status_report="\n".join(status_messages)
                    )
                )

            streak = await self.bot.redis.execute_command(
                "INCR", f"idle:daily:{ctx.author.id}"
            )
            await self.bot.redis.execute_command(
                "EXPIRE", f"idle:daily:{ctx.author.id}", 48 * 60 * 60
            )  # 48h: after 2 days, they missed it

            await self.ensure_streaks_table()
            async with self.bot.pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO streaks (user_id, current_streak, highest_days, last_daily)
                    VALUES ($1, $2, $2, NOW())
                    ON CONFLICT (user_id) DO UPDATE
                    SET current_streak = EXCLUDED.current_streak,
                        highest_days = GREATEST(streaks.highest_days, EXCLUDED.current_streak),
                        last_daily = NOW();
                    """,
                    ctx.author.id,
                    int(streak),
                )

            # Handle milestone rewards
            milestone_rewards = {
                50: ("legendary", 1, 100000),
                100: ("fortune", 1, 150000),
                200: ("mystery", 100, 200000),
                300: ("divine", 1, 300000),
                400: ("fortune", 3, 400000),
                500: ("divine", 2, 500000),
                550: ("fortune", 2, 100000),
                600: ("divine", 1, 150000),
                700: ("mystery", 100, 200000),
                800: ("fortune", 3, 300000),
                900: ("divine", 4, 400000),
                1000: ("divine", 4, 500000),
            }

            if streak in milestone_rewards:
                crate_type, crate_amount, bonus_money = milestone_rewards[streak]
                async with self.bot.pool.acquire() as conn:
                    # Add crates
                    await conn.execute(
                        f'UPDATE profile SET "crates_{crate_type}"="crates_{crate_type}"+$1 WHERE "user"=$2;',
                        crate_amount,
                        ctx.author.id,
                    )
                    # Add bonus money
                    await conn.execute(
                        'UPDATE profile SET "money"="money"+$1 WHERE "user"=$2;',
                        bonus_money,
                        ctx.author.id,
                    )
                    # Log transactions
                    await self.bot.log_transaction(
                        ctx,
                        from_=1,
                        to=ctx.author.id,
                        subject="milestone_crates",
                        data={"Rarity": crate_type, "Amount": crate_amount},
                        conn=conn,
                    )
                    await self.bot.log_transaction(
                        ctx,
                        from_=1,
                        to=ctx.author.id,
                        subject="milestone_money",
                        data={"Gold": bonus_money},
                        conn=conn,
                    )
                txt = f"**{crate_amount}** {getattr(self.bot.cogs['Crates'].emotes, crate_type)} and **${bonus_money}**"
            else:
                # Regular daily rewards logic
                money = 2 ** ((streak + 9) % 10) * 50
                if random.randint(0, 2) > 0:
                    money = 2 ** ((streak + 9) % 10) * 50
                    # Silver = 1.5x
                    if await user_is_patron(self.bot, ctx.author, "silver"):
                        money = round(money * 1.5)

                    result = await self.bot.pool.fetchval('SELECT tier FROM profile WHERE "user" = $1;', ctx.author.id)
                    result = int(result or 0)

                    if result >= 3:
                        money = round(money * 3)

                    async with self.bot.pool.acquire() as conn:
                        await conn.execute(
                            'UPDATE profile SET "money"="money"+$1 WHERE "user"=$2;',
                            money,
                            ctx.author.id,
                        )
                        await self.bot.log_transaction(
                            ctx,
                            from_=1,
                            to=ctx.author.id,
                            subject="daily",
                            data={"Gold": money},
                            conn=conn,
                        )
                    txt = f"**${money}**"
                else:
                    num = round(((streak + 9) % 10 + 1) / 2)
                    amt = random.randint(1, 6 - num)
                    types = [
                        "common",
                        "uncommon",
                        "rare",
                        "magic",
                        "legendary",
                        "common",
                        "common",
                        "common",
                    ]  # Trick for -1
                    type_ = random.choice(
                        [types[num - 3]] * 80 + [types[num - 2]] * 19 + [types[num - 1]] * 1
                    )
                    async with self.bot.pool.acquire() as conn:
                        await conn.execute(
                            f'UPDATE profile SET "crates_{type_}"="crates_{type_}"+$1 WHERE "user"=$2;',
                            amt,
                            ctx.author.id,
                        )
                        await self.bot.log_transaction(
                            ctx,
                            from_=1,
                            to=ctx.author.id,
                            subject="crates",
                            data={"Rarity": type_, "Amount": amt},
                            conn=conn,
                        )
                    txt = f"**{amt}** {getattr(self.bot.cogs['Crates'].emotes, type_)}"

            if ctx.guild == 969741725931298857:
                async with self.bot.pool.acquire() as conn:
                    await conn.execute(
                        'UPDATE profile SET "freeimage"=$1 WHERE "user"=$2;',
                        3,
                        ctx.author.id,
                    )

            await ctx.send(
                _(
                    "You received your daily {txt}!\nYou are on a streak of **{streak}**"
                    " days!\n*Tip: `{prefix}vote` every 12 hours to get an up to legendary"
                    " crate with possibly rare items!*"
                ).format(txt=txt, streak=streak, prefix=ctx.clean_prefix)
            )

            attached_status = await self.run_daily_attached_commands(ctx)
            if attached_status:
                await ctx.send(
                    _("Status Report:\n{status_report}").format(
                        status_report="\n".join(attached_status)
                    )
                )
        except Exception as e:
            import traceback
            error_message = f"Error occurred: {e}\n"
            error_message += traceback.format_exc()
            await ctx.send(error_message)
            print(error_message)

    def read_challenges_from_file(self, filename):
        with open(filename, 'r') as file:
            challenges = file.readlines()
        return [challenge.strip() for challenge in challenges]  # Strip newline characters

    def format_monsters(self, monsters):
        formatted = ""
        for level, monster_list in sorted(monsters.items()):
            # Assign a color based on the level
            if level == 1:
                level_color = "🟢"
            elif level == 2:
                level_color = "🟡"
            elif level == 3:
                level_color = "🔴"
            elif level == 4:
                level_color = "🔵"
            else:
                level_color = "⚪"

            # Add level heading
            formatted += f"**{level_color} Level {level}**\n\n"

            for monster in monster_list:
                name = monster["name"]
                url = monster.get("url", "")
                if url:
                    monster_entry = f"- **{name}**\n  [![{name}]({url})]({url})\n\n"
                else:
                    monster_entry = f"- **{name}**\n  *No image available.*\n\n"
                formatted += monster_entry

            # Add a separator between levels
            formatted += "---\n\n"
        return formatted

    # Function to split the formatted text into chunks <=2000 characters
    def split_into_chunks(self, text, max_length=2000):
        chunks = []
        while len(text) > max_length:
            # Find the last newline within the limit
            split_pos = text.rfind('\n', 0, max_length)
            if split_pos == -1:
                # If no newline found, force split
                split_pos = max_length
            chunks.append(text[:split_pos])
            text = text[split_pos:].lstrip('\n')
        chunks.append(text)
        return chunks


    @commands.hybrid_command()
    async def choose(self, ctx, *, args: str):
        """
        Chooses between two options provided by the user.
        Handles input like "$choose heads tails" or "$choose heads or tails".
        """
        # Split the input by "or" or whitespace
        if " or " in args:
            options = [option.strip() for option in args.split(" or ")]
        else:
            options = args.split()

        # Ensure there are exactly two options
        if len(options) != 2:
            await ctx.send("Please provide exactly two options, separated by a space or 'or'.")
            return

        # Randomly select between the two options
        result = random.choice(options)
        await ctx.send(f"{result}")




    @is_gm()
    @commands.hybrid_command(hidden=True, name="challenges")
    @locale_doc
    async def send_challenges(self, ctx):
        try:
            challenges = self.read_challenges_from_file("challenges.txt")  # Read challenges from file
            selected_challenges = random.sample(challenges, 6)  # Select 6 random challenges
            response = "\n".join(selected_challenges)

            await ctx.author.send(f"Here are 6 random challenges for you:\n{response}")
        except Exception as e:
            await ctx.send(e)


    @user_cooldown(5)
    @commands.hybrid_command(brief="Hug someone with a cute GIF!")
    @locale_doc
    async def hug(self, ctx, user: discord.Member):
        _(
            """`<user>` - The member to hug.

        Send a virtual hug to another member! This command fetches a random hug GIF and displays it along with a message mentioning both you and the user.

        Usage:
          `$hug @username`

        Note:
        - You cannot hug yourself.
        - This command has a cooldown of 5 seconds."""
        )

        try:

            if user == ctx.author:
                await ctx.send("That's.. uh.. that's pretty sad.")
                return

            async with aiohttp.ClientSession() as session:
                # Replace 'YOUR_GIPHY_API_KEY' with your Giphy API key
                async with session.get(
                        f"https://api.giphy.com/v1/gifs/search?api_key=VYSGSDAzA8X0PPWf252QMdG5wvvDyJG2&q=hug&limit=20&rating=pg") as r:
                    if r.status == 200:
                        data = await r.json()
                        gif_url = random.choice(data['data'])['images']['original']['url']
                    else:
                        gif_url = None

            if gif_url:
                embed = discord.Embed(description=f"{ctx.author.mention} hugs {user.mention} 🤗")
                embed.set_image(url=gif_url)
                await ctx.send(embed=embed)
            else:
                await ctx.send("Couldn't fetch a hug GIF at the moment!")
        except Exception as e:
            await ctx.send(e)


    @user_cooldown(5)
    @commands.hybrid_command(brief="Kiss someone with a cute GIF!")
    @locale_doc
    async def kiss(self, ctx, user: discord.Member):
        _(
            """`<user>` - The member to kiss.

        Give someone a virtual kiss! This command fetches a random kiss GIF and displays it along with a message mentioning both you and the user.

        Usage:
          `$kiss @username`

        Note:
        - You cannot kiss yourself.
        - This command has a cooldown of 5 seconds."""
        )

        try:
            if user == ctx.author:
                await ctx.send("Kissing yourself? That's interesting...")
                return

            async with aiohttp.ClientSession() as session:
                async with session.get(
                        f"https://api.giphy.com/v1/gifs/search?api_key=VYSGSDAzA8X0PPWf252QMdG5wvvDyJG2&q=kiss&limit=20&rating=pg") as r:
                    if r.status == 200:
                        data = await r.json()
                        gif_url = random.choice(data['data'])['images']['original']['url']
                    else:
                        gif_url = None

            if gif_url:
                embed = discord.Embed(description=f"{ctx.author.mention} kisses {user.mention} 😘")
                embed.set_image(url=gif_url)
                await ctx.send(embed=embed)
            else:
                await ctx.send("Couldn't fetch a kiss GIF at the moment!")
        except Exception as e:
            await ctx.send(e)


    @user_cooldown(5)
    @commands.hybrid_command(brief="Bonk someone with a funny GIF!")
    @locale_doc
    async def bonk(self, ctx, user: discord.Member):
        _(
            """`<user>` - The member to bonk.

        Give someone a virtual bonk! This command displays a random bonk GIF from a predefined list, along with a message mentioning both you and the user.

        Usage:
          `$bonk @username`

        Note:
        - You cannot bonk yourself.
        - This command has a cooldown of 5 seconds."""
        )

        try:
            if user == ctx.author:
                await ctx.send("Bonking yourself? That must hurt...")
                return

            gif_url = "https://media0.giphy.com/media/HmgnQQjEMbMz0oLpqn/giphy.gif?cid=49e4d7b557ooon5bnhtiz3j1n2gp2og8b0qronyhl9njvkcg&ep=v1_gifs_search&rid=giphy.gif&ct=g"

            # Create and send the embed message
            embed = discord.Embed(description=f"{ctx.author.mention} bonks {user.mention} 🔨")
            embed.set_image(url=gif_url)
            await ctx.send(embed=embed)
        except Exception as e:
            await ctx.send(f"An error occurred: {e}")

    @user_cooldown(5)
    @commands.hybrid_command(brief="Pat someone with a cute GIF!")
    @locale_doc
    async def pat(self, ctx, user: discord.Member):
        _(
            """`<user>` - The member to pat.

        Pat someone on the head virtually! This command fetches a random pat GIF and displays it along with a message mentioning both you and the user.

        Usage:
          `$pat @username`

        Note:
        - You cannot pat yourself.
        - This command has a cooldown of 5 seconds."""
        )

        try:
            if user == ctx.author:
                await ctx.send("Self-pats are good for self-care!")
                return

            async with aiohttp.ClientSession() as session:
                async with session.get(
                        f"https://api.giphy.com/v1/gifs/search?api_key=VYSGSDAzA8X0PPWf252QMdG5wvvDyJG2&q=pat&limit=20&rating=pg") as r:
                    if r.status == 200:
                        data = await r.json()
                        gif_url = random.choice(data['data'])['images']['original']['url']
                    else:
                        gif_url = None

            if gif_url:
                embed = discord.Embed(description=f"{ctx.author.mention} pats {user.mention} 😊")
                embed.set_image(url=gif_url)
                await ctx.send(embed=embed)
            else:
                await ctx.send("Couldn't fetch a pat GIF at the moment!")
        except Exception as e:
            await ctx.send(e)


    @user_cooldown(5)
    @commands.hybrid_command(brief="Slap someone with a GIF!")
    @locale_doc
    async def slap(self, ctx, user: discord.Member):
        _(
            """`<user>` - The member to slap.

        Slap another member virtually! This command fetches a random slap GIF and displays it along with a message mentioning both you and the user.

        Usage:
          `$slap @username`

        Note:
        - You cannot slap yourself.
        - This command has a cooldown of 5 seconds."""
        )

        try:
            if user == ctx.author:
                await ctx.send("Slapping yourself? That doesn't seem healthy!")
                return

            async with aiohttp.ClientSession() as session:
                async with session.get(
                        f"https://api.giphy.com/v1/gifs/search?api_key=VYSGSDAzA8X0PPWf252QMdG5wvvDyJG2&q=slap&limit=20&rating=pg") as r:
                    if r.status == 200:
                        data = await r.json()
                        gif_url = random.choice(data['data'])['images']['original']['url']
                    else:
                        gif_url = None

            if gif_url:
                embed = discord.Embed(description=f"{ctx.author.mention} slaps {user.mention} 😡")
                embed.set_image(url=gif_url)
                await ctx.send(embed=embed)
            else:
                await ctx.send("Couldn't fetch a slap GIF at the moment!")
        except Exception as e:
            await ctx.send(e)


    @user_cooldown(5)
    @commands.hybrid_command(brief="Give someone a high five!")
    @locale_doc
    async def highfive(self, ctx, user: discord.Member):
        _(
            """`<user>` - The member to high-five.

        Give a virtual high-five to another member! This command fetches a random high-five GIF and displays it along with a message mentioning both you and the user.

        Usage:
          `$highfive @username`

        Note:
        - You cannot high-five yourself.
        - This command has a cooldown of 5 seconds."""
        )

        try:
            if user == ctx.author:
                await ctx.send("You can't high-five yourself... or can you?")
                return

            async with aiohttp.ClientSession() as session:
                async with session.get(
                        f"https://api.giphy.com/v1/gifs/search?api_key=VYSGSDAzA8X0PPWf252QMdG5wvvDyJG2&q=highfive&limit=20&rating=pg") as r:
                    if r.status == 200:
                        data = await r.json()
                        gif_url = random.choice(data['data'])['images']['original']['url']
                    else:
                        gif_url = None

            if gif_url:
                embed = discord.Embed(description=f"{ctx.author.mention} gives {user.mention} a high five! 🙌")
                embed.set_image(url=gif_url)
                await ctx.send(embed=embed)
            else:
                await ctx.send("Couldn't fetch a high five GIF at the moment!")
        except Exception as e:
            await ctx.send(e)


    @user_cooldown(5)
    @commands.hybrid_command(brief="Wave at someone with a GIF!")
    @locale_doc
    async def wave(self, ctx, user: discord.Member):
        _(
            """`<user>` - The member to wave at.

        Wave at someone with a friendly GIF! This command fetches a random wave GIF and displays it along with a message mentioning both you and the user.

        Usage:
          `$wave @username`

        Note:
        - You cannot wave at yourself.
        - This command has a cooldown of 5 seconds."""
        )

        try:
            if user == ctx.author:
                await ctx.send("Waving at yourself? That's a bit awkward...")
                return

            async with aiohttp.ClientSession() as session:
                async with session.get(
                        f"https://api.giphy.com/v1/gifs/search?api_key=VYSGSDAzA8X0PPWf252QMdG5wvvDyJG2&q=wave&limit=20&rating=pg") as r:
                    if r.status == 200:
                        data = await r.json()
                        gif_url = random.choice(data['data'])['images']['original']['url']
                    else:
                        gif_url = None

            if gif_url:
                embed = discord.Embed(description=f"{ctx.author.mention} waves at {user.mention} 👋")
                embed.set_image(url=gif_url)
                await ctx.send(embed=embed)
            else:
                await ctx.send("Couldn't fetch a wave GIF at the moment!")
        except Exception as e:
            await ctx.send(e)


    @user_cooldown(5)
    @commands.hybrid_command(brief="Cuddle someone with a cute GIF!")
    @locale_doc
    async def cuddle(self, ctx, user: discord.Member):
        _(
            """`<user>` - The member to cuddle.

        Give someone a warm virtual cuddle! This command fetches a random cuddle GIF and displays it along with a message mentioning both you and the user.

        Usage:
          `$cuddle @username`

        Note:
        - You cannot cuddle yourself.
        - This command has a cooldown of 5 seconds."""
        )

        try:
            if user == ctx.author:
                await ctx.send("Cuddling yourself? A warm blanket works too!")
                return

            async with aiohttp.ClientSession() as session:
                async with session.get(
                        f"https://api.giphy.com/v1/gifs/search?api_key=VYSGSDAzA8X0PPWf252QMdG5wvvDyJG2&q=cuddle&limit=20&rating=pg") as r:
                    if r.status == 200:
                        data = await r.json()
                        gif_url = random.choice(data['data'])['images']['original']['url']
                    else:
                        gif_url = None

            if gif_url:
                embed = discord.Embed(description=f"{ctx.author.mention} cuddles {user.mention} 🤗")
                embed.set_image(url=gif_url)
                await ctx.send(embed=embed)
            else:
                await ctx.send("Couldn't fetch a cuddle GIF at the moment!")
        except Exception as e:
            await ctx.send(e)


    @user_cooldown(5)
    @commands.hybrid_command(brief="Poke someone gently with a cute GIF!")
    @locale_doc
    async def poke(self, ctx, user: discord.Member):
        _(
            """`<user>` - The member to poke.

        Gently poke another member! This command fetches a random poke GIF and displays it along with a message mentioning both you and the user.

        Usage:
          `$poke @username`

        Note:
        - You cannot poke yourself.
        - This command has a cooldown of 5 seconds."""
        )

        try:
            if user == ctx.author:
                await ctx.send("Poking yourself? That’s odd!")
                return

            async with aiohttp.ClientSession() as session:
                async with session.get(
                        f"https://api.giphy.com/v1/gifs/search?api_key=VYSGSDAzA8X0PPWf252QMdG5wvvDyJG2&q=poke&limit=20&rating=pg") as r:
                    if r.status == 200:
                        data = await r.json()
                        gif_url = random.choice(data['data'])['images']['original']['url']
                    else:
                        gif_url = None

            if gif_url:
                embed = discord.Embed(description=f"{ctx.author.mention} pokes {user.mention} 👈")
                embed.set_image(url=gif_url)
                await ctx.send(embed=embed)
            else:
                await ctx.send("Couldn't fetch a poke GIF at the moment!")
        except Exception as e:
            await ctx.send(e)


    @user_cooldown(5)
    @commands.hybrid_command(brief="Bite someone gently with a playful GIF!")
    @locale_doc
    async def bite(self, ctx, user: discord.Member):
        _(
            """`<user>` - The member to bite playfully.

        Playfully bite someone! This command fetches a random bite GIF and displays it along with a message mentioning both you and the user.

        Usage:
          `$bite @username`

        Note:
        - You cannot bite yourself.
        - This command has a cooldown of 5 seconds."""
        )

        try:
            if user == ctx.author:
                await ctx.send("Biting yourself? Ouch!")
                return

            async with aiohttp.ClientSession() as session:
                async with session.get(
                        f"https://api.giphy.com/v1/gifs/search?api_key=VYSGSDAzA8X0PPWf252QMdG5wvvDyJG2&q=bite&limit=20&rating=pg") as r:
                    if r.status == 200:
                        data = await r.json()
                        gif_url = random.choice(data['data'])['images']['original']['url']
                    else:
                        gif_url = None

            if gif_url:
                embed = discord.Embed(description=f"{ctx.author.mention} bites {user.mention} playfully 😋")
                embed.set_image(url=gif_url)
                await ctx.send(embed=embed)
            else:
                await ctx.send("Couldn't fetch a bite GIF at the moment!")
        except Exception as e:
            await ctx.send(e)


    @user_cooldown(5)
    @commands.hybrid_command(brief="Tickle someone with a GIF!")
    @locale_doc
    async def tickle(self, ctx, user: discord.Member):
        _(
            """`<user>` - The member to tickle.

        Tickle someone and make them laugh! This command fetches a random tickle GIF and displays it along with a message mentioning both you and the user.

        Usage:
          `$tickle @username`

        Note:
        - You cannot tickle yourself.
        - This command has a cooldown of 5 seconds."""
        )

        try:
            if user == ctx.author:
                await ctx.send("Tickling yourself? Doesn't quite work!")
                return

            async with aiohttp.ClientSession() as session:
                async with session.get(
                        f"https://api.giphy.com/v1/gifs/search?api_key=VYSGSDAzA8X0PPWf252QMdG5wvvDyJG2&q=tickle&limit=20&rating=pg") as r:
                    if r.status == 200:
                        data = await r.json()
                        gif_url = random.choice(data['data'])['images']['original']['url']
                    else:
                        gif_url = None

            if gif_url:
                embed = discord.Embed(description=f"{ctx.author.mention} tickles {user.mention} 😂")
                embed.set_image(url=gif_url)
                await ctx.send(embed=embed)
            else:
                await ctx.send("Couldn't fetch a tickle GIF at the moment!")
        except Exception as e:
            await ctx.send(e)


    @user_cooldown(5)
    @commands.hybrid_command(brief="Nuzzle someone affectionately!")
    @locale_doc
    async def nuzzle(self, ctx, user: discord.Member):
        _(
            """`<user>` - The member to nuzzle.

        Affectionately nuzzle someone! This command fetches a random nuzzle GIF and displays it along with a message mentioning both you and the user.

        Usage:
          `$nuzzle @username`

        Note:
        - You cannot nuzzle yourself.
        - This command has a cooldown of 5 seconds."""
        )

        try:
            if user == ctx.author:
                await ctx.send("Nuzzling yourself? That's an interesting form of self-love!")
                return

            async with aiohttp.ClientSession() as session:
                async with session.get(
                        f"https://api.giphy.com/v1/gifs/search?api_key=VYSGSDAzA8X0PPWf252QMdG5wvvDyJG2&q=nuzzle&limit=20&rating=pg") as r:
                    if r.status == 200:
                        data = await r.json()
                        gif_url = random.choice(data['data'])['images']['original']['url']
                    else:
                        gif_url = None

            if gif_url:
                embed = discord.Embed(description=f"{ctx.author.mention} nuzzles {user.mention} 😽")
                embed.set_image(url=gif_url)
                await ctx.send(embed=embed)
            else:
                await ctx.send("Couldn't fetch a nuzzle GIF at the moment!")
        except Exception as e:
            await ctx.send(e)


    @user_cooldown(5)
    @commands.hybrid_command(brief="Lick someone!")
    @locale_doc
    async def lick(self, ctx, user: discord.Member):
        _(
            """`<user>` - The member to lick.

        Lick someone playfully! This command fetches a random lick GIF and displays it along with a message mentioning both you and the user.

        Usage:
          `$lick @username`

        Note:
        - You cannot lick yourself.
        - This command has a cooldown of 5 seconds."""
        )

        try:
            if user == ctx.author:
                await ctx.send("Licking yourself? Weirdo.")
                return

            async with aiohttp.ClientSession() as session:
                async with session.get(
                        f"https://api.giphy.com/v1/gifs/search?api_key=VYSGSDAzA8X0PPWf252QMdG5wvvDyJG2&q=lick-face&limit=20&rating=pg") as r:
                    if r.status == 200:
                        data = await r.json()
                        gif_url = random.choice(data['data'])['images']['original']['url']
                    else:
                        gif_url = None

            if gif_url:
                embed = discord.Embed(description=f"{ctx.author.mention} licks {user.mention} ewww.")
                embed.set_image(url=gif_url)
                await ctx.send(embed=embed)
            else:
                await ctx.send("Couldn't fetch a lick GIF at the moment!")
        except Exception as e:
            await ctx.send(e)


    @user_cooldown(5)
    @commands.hybrid_command(brief="Punch someone with a GIF!")
    @locale_doc
    async def punch(self, ctx, user: discord.Member):
        _(
            """`<user>` - The member to punch.

        Deliver a virtual punch to someone! This command fetches a random punch GIF and displays it along with a message mentioning both you and the user.

        Usage:
          `$punch @username`

        Note:
        - You cannot punch yourself.
        - This command has a cooldown of 5 seconds."""
        )

        try:
            if user == ctx.author:
                await ctx.send("Punching yourself? That's not a good idea!")
                return

            async with aiohttp.ClientSession() as session:
                async with session.get(
                        f"https://api.giphy.com/v1/gifs/search?api_key=VYSGSDAzA8X0PPWf252QMdG5wvvDyJG2&q=punch&limit=20&rating=pg") as r:
                    if r.status == 200:
                        data = await r.json()
                        gif_url = random.choice(data['data'])['images']['original']['url']
                    else:
                        gif_url = None

            if gif_url:
                embed = discord.Embed(description=f"{ctx.author.mention} punches {user.mention}! 👊")
                embed.set_image(url=gif_url)
                await ctx.send(embed=embed)
            else:
                await ctx.send("Couldn't fetch a punch GIF at the moment!")
        except Exception as e:
            await ctx.send(e)


    @has_char()
    @commands.hybrid_command(brief=_("Roll"))
    @locale_doc
    async def roll(self, ctx):
        _(
            """Send a rolling bread (🥖) emoji.

        Use this command to get a random roll (of bread)!

        Usage:
          `$roll`

        Note:
        - This is a fun command with no cooldown."""
        )

        await ctx.send("🥖")


    @has_char()
    @commands.hybrid_command(brief=_("View your current streak"))
    @locale_doc
    async def streak(self, ctx):
        _(
            """Want to flex your streak on someone or just check how many days in a row you've claimed your daily reward? This command is for you"""
        )
        streak = await self.bot.redis.execute_command(
            "GET", f"idle:daily:{ctx.author.id}"
        )
        if not streak:
            return await ctx.send(
                _(
                    "You don't have a daily streak yet. You can get one going by using"
                    " the command `{prefix}daily`!"
                ).format(prefix=ctx.clean_prefix)
            )
        await ctx.send(
            _("You are on a daily streak of **{streak}!**").format(
                streak=streak.decode()
            )
        )

    @has_char()
    @commands.hybrid_command(brief=_("Restore your lost daily streak"))
    @locale_doc
    async def restore(self, ctx):
        _(
            """Restore your lost daily streak using restore points.

            You have 3 restore points available. Each use restores your streak
            to your previous highest streak achieved.

            This can only be used if your current streak is lower than your highest
            recorded streak and you have restore points remaining."""
        )
        try:
            await self.ensure_streaks_table()

            async with self.bot.pool.acquire() as conn:
                user_data = await conn.fetchrow(
                    """
                    SELECT current_streak, highest_days, restore_points
                    FROM streaks
                    WHERE user_id = $1;
                    """,
                    ctx.author.id,
                )

                if user_data is None:
                    redis_streak = await self.bot.redis.execute_command(
                        "GET", f"idle:daily:{ctx.author.id}"
                    )
                    if not redis_streak:
                        return await ctx.send(
                            _(
                                "You haven't used the daily command yet! Use `{prefix}daily` first."
                            ).format(prefix=ctx.clean_prefix)
                        )

                    current = int(
                        redis_streak.decode()
                        if isinstance(redis_streak, (bytes, bytearray))
                        else redis_streak
                    )
                    await conn.execute(
                        """
                        INSERT INTO streaks (user_id, current_streak, highest_days, last_daily)
                        VALUES ($1, $2, $2, NOW())
                        ON CONFLICT (user_id) DO NOTHING;
                        """,
                        ctx.author.id,
                        current,
                    )
                    user_data = {
                        "current_streak": current,
                        "highest_days": current,
                        "restore_points": 3,
                    }

                current_streak = int(user_data["current_streak"] or 0)
                highest_days = int(user_data["highest_days"] or 0)
                restore_points = int(user_data["restore_points"] or 0)

                if restore_points <= 0:
                    embed = discord.Embed(
                        title=_("Streak Restore Unavailable"),
                        description=_("You have no restore points remaining."),
                        color=discord.Color.red(),
                    )
                    return await ctx.send(embed=embed)

                if current_streak >= highest_days:
                    embed = discord.Embed(
                        title=_("Streak Restore Not Needed"),
                        description=_(
                            "Your current streak (**{current}**) is already at or above your highest streak (**{highest}**)."
                        ).format(current=current_streak, highest=highest_days),
                        color=discord.Color.orange(),
                    )
                    return await ctx.send(embed=embed)

                new_points = restore_points - 1
                await conn.execute(
                    """
                    UPDATE streaks
                    SET current_streak = $1,
                        restore_points = $2,
                        last_daily = NOW()
                    WHERE user_id = $3;
                    """,
                    highest_days,
                    new_points,
                    ctx.author.id,
                )

                await self.bot.redis.execute_command(
                    "SET", f"idle:daily:{ctx.author.id}", highest_days
                )
                await self.bot.redis.execute_command(
                    "EXPIRE", f"idle:daily:{ctx.author.id}", 48 * 60 * 60
                )

                await self.bot.log_transaction(
                    ctx,
                    from_=ctx.author.id,
                    to=1,
                    subject="streak_restore",
                    data={
                        "From": current_streak,
                        "To": highest_days,
                        "Points_Remaining": new_points,
                    },
                    conn=conn,
                )

            embed = discord.Embed(
                title=_("Streak Restored"),
                description=_("Your daily streak has been restored successfully."),
                color=discord.Color.green(),
            )
            embed.add_field(name=_("Before"), value=f"**{current_streak}**", inline=True)
            embed.add_field(name=_("After"), value=f"**{highest_days}**", inline=True)
            embed.add_field(name=_("Restore Points"), value=f"**{new_points}**/3", inline=True)
            embed.set_footer(
                text=_("Keep claiming `{prefix}daily` to continue your streak.").format(
                    prefix=ctx.clean_prefix
                )
            )
            await ctx.send(embed=embed)
        except Exception as e:
            import traceback

            print(f"Error occurred in restore: {e}\n{traceback.format_exc()}")
            await ctx.send(
                _(
                    "An unexpected error occurred while restoring your streak. Please try again later."
                )
            )


    @commands.hybrid_command(aliases=["donate"], brief=_("Support the bot financially"))
    @locale_doc
    async def patreon(self, ctx):
        _(
            """View the Patreon page of the bot. The different tiers will grant different rewards.
            View `{prefix}help module Patreon` to find the different commands.

            Thank you for supporting EoO!"""
        )
        guild_count = sum(
            await self.bot.cogs["Sharding"].handler(
                "guild_count", self.bot.cluster_count
            )
        )
        await ctx.send(
            _(
                """\
This bot has its own patreon page.

**Why should I donate?**
This bot is currently on {guild_count} servers, and it is growing fast.
Hosting this bot for all users is not easy and costs a lot of money.
If you want to continue using the bot or just help us, please donate a small amount.
Even $1 can help us.
**Thank you!**

https://www.patreon.com/c/Danaelis97"""
            ).format(guild_count=guild_count)
        )


    @commands.hybrid_command(
        aliases=["license"], brief=_("Shows the source code and license.")
    )
    @locale_doc
    async def source(self, ctx):
        _(
            """Shows Idles GitLab page and license alongside our own source as required by AGPLv3 Licensing."""
        )
        await ctx.send("IdleRPG - AGPLv3+\nhttps://git.travitia.xyz/Kenvyra/IdleRPG")

        await ctx.send("Fable - AGPLv3+\nhttps://github.com/prototypeX37/FableRPG-")


    @commands.hybrid_command(brief=_("Invite the bot to your server."))
    @locale_doc
    async def invite(self, ctx):
        _(
            """Please join our support server https://discord.gg/BVWtrWvaDA"""
        )
        await ctx.send(
            _(
                "You are running version **{version}**"
                "Developers.\nJoin us https://discord.gg/BVWtrWvaDA"
            ).format(version=self.bot.version)
        )




    async def paginate_embeds(self, ctx, pages, timeout=60):
        """
        Given a list of discord.Embed objects, paginate them in the channel with
        reaction controls: ◀️, ❌, ▶️

        :param ctx: The command context
        :param pages: A list of discord.Embed objects
        :param timeout: Timeout in seconds for reaction waiting
        """
        if not pages:
            await ctx.send("No pages to display.")
            return

        current_page = 0
        message = await ctx.send(embed=pages[current_page])

        # Add reactions for navigation
        reactions = ["◀️", "❌", "▶️"]
        for r in reactions:
            await message.add_reaction(r)

        def check(reaction, user):
            return (
                    user == ctx.author
                    and reaction.message.id == message.id
                    and str(reaction.emoji) in reactions
            )

        while True:
            try:
                reaction, user = await ctx.bot.wait_for(
                    "reaction_add",
                    timeout=timeout,
                    check=check
                )
            except:
                # Timed out, remove the reactions and break
                try:
                    await message.clear_reactions()
                except discord.Forbidden:
                    pass
                break

            # Remove the user's reaction
            try:
                await message.remove_reaction(reaction.emoji, user)
            except discord.Forbidden:
                pass

            if str(reaction.emoji) == "◀️":
                # Go to previous page
                current_page = (current_page - 1) % len(pages)
                await message.edit(embed=pages[current_page])
            elif str(reaction.emoji) == "▶️":
                # Go to next page
                current_page = (current_page + 1) % len(pages)
                await message.edit(embed=pages[current_page])
            elif str(reaction.emoji) == "❌":
                # Close the pagination
                await message.delete()
                break

    @commands.command()
    async def allcommands(self, ctx):
        """Displays all available commands categorized by their cogs, excluding @is_gm() commands."""

        # Example check for blacklisted user
        if ctx.author.id == 764904008833171478:
            return await ctx.send(
                f"{ctx.author.mention} your access to `allcommands` has automatically been revoked due to the reason: Automod Spam"
            )

        loading_message = await ctx.send("Please wait while I gather that information for you...")

        try:
            cog_commands = {}
            # Gather all commands, excluding hidden commands and those with @is_gm() checks
            for cmd in self.bot.commands:
                if cmd.hidden:
                    continue
                # Exclude commands that have a `is_gm` check
                if any(pred.__name__ == "is_gm" for pred in cmd.checks):
                    continue

                cog_name = cmd.cog_name or "No Category"
                # Optionally exclude a "GameMaster" cog entirely
                if cog_name == "GameMaster":
                    continue

                if cog_name not in cog_commands:
                    cog_commands[cog_name] = []

                cog_commands[cog_name].append(cmd.name)

            # Create pages (embeds)
            pages = []
            prefix = "$"  # change as needed

            for cog_name, commands_list in cog_commands.items():
                embed = discord.Embed(
                    title=f"{cog_name} Commands",
                    description=f"Commands in **{cog_name}** category.",
                    color=discord.Color.blue()
                )

                cmd_text = "\n".join(f"`{prefix}{cmd_name}`" for cmd_name in commands_list)
                embed.add_field(name="Commands", value=cmd_text, inline=False)

                pages.append(embed)

            # If we never found any commands
            if not pages:
                return await loading_message.edit(content="No commands found.")

            # Remove "Please wait..."
            await loading_message.delete()

            # Create the PaginatorView
            view = PaginatorView(ctx, pages, start_page=0, timeout=60)
            # Send the first page with the view
            message = await ctx.send(embed=pages[0], view=view)
            # Store reference to the message in the view (so we can edit on timeout)
            view.message = message

        except Exception as e:
            await ctx.send(e)


    @commands.hybrid_command(brief=_("Shows statistics about the bot"))
    @locale_doc
    async def stats(self, ctx):
        _(
            """Show some stats about the bot, ranging from hard- and software statistics, over performance to ingame stats."""
        )
        async with self.bot.pool.acquire() as conn:
            characters = await conn.fetchval("SELECT COUNT(*) FROM profile;")
            items = await conn.fetchval("SELECT COUNT(*) FROM allitems;")
            pg_version = conn.get_server_version()
        pg_version = f"{pg_version.major}.{pg_version.micro} {pg_version.releaselevel}"
        d0 = self.bot.user.created_at
        d1 = datetime.datetime.now(datetime.timezone.utc)
        delta = d1 - d0
        myhours = delta.days * 1.5
        sysinfo = distro.linux_distribution()
        if self.bot.owner_ids:
            owner = nice_join(
                [str(await self.bot.get_user_global(u)) for u in self.bot.owner_ids]
            )
        else:
            owner = str(await self.bot.get_user_global(self.bot.owner_id))
        guild_count = sum(
            await self.bot.cogs["Sharding"].handler(
                "guild_count", self.bot.cluster_count
            )
        )
        compiler = re.search(r".*\[(.*)\]", sys.version)[1]

        embed = discord.Embed(
            title=_("EoO Statistics"),
            colour=0xB8BBFF,
            url=self.bot.BASE_URL,
            description=_(
                "Official Support Server Invite: https://discord.gg/BVWtrWvaDA"
            ),
        )
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.set_footer(
            text=f"EoO {self.bot.version} | By {owner}",
            icon_url=self.bot.user.display_avatar.url,
        )
        embed.add_field(
            name=_("Hosting Statistics"),
            value=_(
                """\
CPU: ****
Python Version **{python}** 
discord.py Version **{dpy}**
Compiler: **{compiler}**
Operating System: **{osname} {osversion}**
Kernel Version: **{kernel}**
PostgreSQL Version: **{pg_version}**
Redis Version: **{redis_version}**"""
            ).format(
                python=platform.python_version(),
                dpy=pkg.get_distribution("discord.py").version,
                compiler=compiler,
                osname=sysinfo[0].title(),
                osversion=sysinfo[1],
                kernel=os.uname().release if os.name == "posix" else "NT",
                pg_version=pg_version,
                redis_version=self.bot.redis_version,
            ),
            inline=False,
        )
        embed.add_field(
            name=_("Bot Statistics"),
            value=_(
                """\
Code lines written: **{lines}**
Shards: **{shards}**
Servers: **{guild_count}**
Characters: **{characters}**
Items: **{items}**
Average hours of work: **{hours}**"""
            ).format(
                lines=self.bot.linecount,
                shards=self.bot.shard_count,
                guild_count=guild_count,
                characters=characters,
                items=items,
                hours=myhours,
            ),
            inline=False,
        )
        await ctx.send(embed=embed)


    @commands.hybrid_command(brief=_("View the uptime"))
    @locale_doc
    async def uptime(self, ctx):
        _("""Shows how long the bot has been connected to Discord.""")
        await ctx.send(
            _("I am online for **{time}**.").format(
                time=str(self.bot.uptime).split(".")[0]
            )
        )


    @commands.hybrid_command()
    @has_char()
    @locale_doc
    async def credits(self, ctx):
        _(
            """Check your remaining image generation credits.

        This command shows how many free images you have left and your current balance of image credits.

        Usage:
          `$credits`

        Note:
        - Image credits are used for generating images with certain commands."""
        )

        creditss = ctx.character_data["imagecredits"]
        freecredits = ctx.character_data["freeimage"]

        await ctx.send(f"You have **{freecredits}** free images left and a balance of **${creditss}**.")


    @commands.hybrid_command()
    @has_char()
    @user_cooldown(60)
    @locale_doc
    async def imagine(self, ctx, *, prompt):
        _(
            """`<prompt>` - The text prompt describing the image you want to generate.

        Generate an image based on your text prompt using AI.

        Usage:
          `$imagine a sunset over the mountains`

        Note:
        - This command uses image credits. You have a limited number of free images per day, after which generating images will cost in-game currency.
        - The prompt should not exceed 120 characters.
        - This command has a cooldown of 60 seconds."""
        )


        creditss = ctx.character_data["imagecredits"]
        freecredits = 0
        # await ctx.send(f"{credits}")

        if ctx.author.id == 524674960153903126:
            await self.bot.reset_cooldown(ctx)

        if ctx.author.id == 598004694060892183:
            await self.bot.reset_cooldown(ctx)

        if ctx.author.id == 749263133620568084:
            await self.bot.reset_cooldown(ctx)



        if freecredits <= 0:

            if creditss <= 0.03:
                return await ctx.send(f"You have used up all free images for today. Additional images cost **$0.04**.")

        try:
            if ctx.author.id != 524674960153903126:
                if ctx.author.id != 598004694060892183:
                    if len(prompt) > 120:
                        return await ctx.send("The prompt cannot exceed 120 characters.")
            await ctx.send("Generating image, please wait. (This can take up to 2 minutes.)")
            client = AsyncOpenAI()
            response = await client.images.generate(
                model="dall-e-3",
                prompt=prompt,
                size="1024x1024",
                quality="standard",
                n=1,
            )

            image_url = response.data[0].url
            async with ctx.typing():
                async with aiohttp.ClientSession() as session:
                    async with session.get(image_url) as resp:
                        if resp.status != 200:
                            return await ctx.send('Could not download file...')
                        data = io.BytesIO(await resp.read())
                        await ctx.send(f"{ctx.author.mention}, your image is ready!")

                        if freecredits > 0:
                            async with self.bot.pool.acquire() as connection:
                                await connection.execute(
                                    f'UPDATE profile SET "freeimage" = freeimage -1 WHERE "user" = {ctx.author.id}'
                                )
                        else:
                            async with self.bot.pool.acquire() as connection:
                                await connection.execute(
                                    f'UPDATE profile SET "imagecredits" = imagecredits -0.04 WHERE "user" = {ctx.author.id}'
                                )
                        await ctx.send(file=discord.File(data, 'image.png'))
        except Exception as e:
            await ctx.send(f"An error has occurred")


    @commands.hybrid_command()
    @user_cooldown(80)
    @has_char()
    @locale_doc
    async def imaginebig(self, ctx, *, prompt):
        _(
            """`<prompt>` - The text prompt describing the high-resolution image you want to generate.

        Generate a high-definition image based on your text prompt using AI.

        Usage:
          `$imaginebig a detailed cityscape at night`

        Note:
        - This command costs more image credits than the standard `imagine` command.
        - You must have enough image credits to use this command.
        - The prompt should not exceed 120 characters.
        - This command has a cooldown of 80 seconds."""
        )

        creditss = ctx.character_data["imagecredits"]
        freecredits = ctx.character_data["freeimage"]
        # await ctx.send(f"{credits}")

        if ctx.author.id == 524674960153903126:
            await self.bot.reset_cooldown(ctx)

        if ctx.author.id != 598004694060892183:
            await self.bot.reset_cooldown(ctx)

        if creditss <= 0.11:
            return await ctx.send(f"You do not have enough credits for this model. Additional images cost **$0.12**.")

        try:
            if ctx.author.id != 524674960153903126:
                if ctx.author.id != 698612238549778493:
                    if len(prompt) > 120:
                        return await ctx.send("The prompt cannot exceed 120 characters.")
            await ctx.send("Generating HD image, please wait. (This can take up to 2 minutes.)")
            client = AsyncOpenAI()
            response = await client.images.generate(
                model="dall-e-3",
                prompt=prompt,
                size="1792x1024",
                quality="hd",
                n=1,
            )

            image_url = response.data[0].url
            async with ctx.typing():
                async with aiohttp.ClientSession() as session:
                    async with session.get(image_url) as resp:
                        if resp.status != 200:
                            return await ctx.send('Could not download file...')
                        data = io.BytesIO(await resp.read())
                        await ctx.send(f"{ctx.author.mention}, your image is ready!")
                        async with self.bot.pool.acquire() as connection:
                            await connection.execute(
                                f'UPDATE profile SET "imagecredits" = imagecredits -0.12 WHERE "user" = {ctx.author.id}'
                            )
                        await ctx.send(file=discord.File(data, 'image.png'))
        except Exception as e:
            await ctx.send(f"An error has occurred")


    @commands.hybrid_command(name='talk', help='Ask ChatGPT a question!')
    @locale_doc
    async def talk(self, ctx, *, question):
        _(
            """`<question>` - The message or question you want to ask.

        Chat with the AI assistant. This command allows you to have a conversation with the bot.

        Usage:
          `$talk How are you today?`

        Note:
        - Your conversation history is maintained during the session.
        - Use `$wipe` to clear your conversation history.
        - Please adhere to the community guidelines when using this command."""
        )

        # Check if the command is invoked in one of the allowed channels

        if ctx.author.id != 524674960153903126:
            if ctx.author.id != 698612238549778493:
                if ctx.guild:
                    if ctx.guild.id not in [969741725931298857, 1285448244859764839]:
                        return
                else:
                    if ctx.author.id != 500713532111716365:
                        return

        user_id = ctx.author.id

        # Add the user's new message to their conversation history
        if user_id not in self.conversations:
            self.conversations[user_id] = []
        try:
            # Fetch the response from GPT-3 using the entire conversation as context
            response = await self.get_gpt_response_async(
                self.conversations[user_id] + [{"role": "user", "content": question}])
        except Exception as e:
            await ctx.send(e)
        # Append the user message and response to the conversation
        self.conversations[user_id].extend([
            {"role": "user", "content": question},
            {"role": "system", "content": response}
        ])

        # Ensure the conversation doesn't exceed 100 messages
        while len(self.conversations[user_id]) > 400:
            self.conversations[user_id].pop(0)  # remove the oldest message

        # Split and send the response back to the user
        for chunk in self.split_message(response):
            await ctx.send(chunk)


    @commands.hybrid_command()
    @locale_doc
    async def cookie(self, ctx, target_member: discord.Member):
        _(
            """`<user>` - The member to give a cookie to.

        Give a virtual cookie to another member!

        Usage:
          `$cookie @username`

        Note:
        - This is a fun command to share some sweetness."""
        )

        await ctx.send(
            f"**{target_member.display_name}**, you've been given a cookie by **{ctx.author.display_name}**. 🍪")


    @commands.hybrid_command()
    @locale_doc
    async def ice(self, ctx, target_member: discord.Member):
        _(
            """`<user>` - The member to give ice cream to.

        Share some virtual ice cream with someone!

        Usage:
          `$ice @username`

        Note:
        - This is a fun command to share some treats."""
        )

        await ctx.send(
            f"{target_member.mention}, here is your ice: 🍨!")


    @commands.hybrid_command(name='wipe', help='Clear your conversation history with the bot.')
    @locale_doc
    async def clear_memory(self, ctx):
        _(
            """Clear your conversation history with the AI assistant.

        Use this command to reset your conversation with the bot.

        Usage:
          `$wipe`

        Note:
        - This will delete your current conversation history with the `talk` command."""
        )

        user_id = ctx.author.id
        if user_id in self.conversations:
            del self.conversations[user_id]
            await ctx.send("Your conversation history has been cleared!")
        else:
            await ctx.send("You don't have any conversation history to clear.")

    def split_message(self, content, limit=1909):
        """Split a message into chunks under a specified limit without breaking words."""
        chunks = []
        while len(content) > limit:
            split_index = content.rfind(' ', 0, limit)
            if split_index == -1:
                split_index = limit
            chunk = content[:split_index]
            chunks.append(chunk)
            content = content[split_index:].strip()  # Remove leading space for next chunk
        chunks.append(content)
        return chunks

    async def get_gpt_response_async(self, conversation_history):
        url = "https://api.openai.com/v1/chat/completions"
        OPENAI_KEY = self.bot.config.external.openai
        headers = {
            "Authorization": f"Bearer {OPENAI_KEY}",
            "Content-Type": "application/json"
        }
        data = {
            "model": "gpt-4-turbo",
            "messages": conversation_history
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=data) as response:
                    response_data = await response.json()
                    return response_data['choices'][0]['message']['content'].strip()
        except aiohttp.ClientError as e:
            return f"Error connecting to OpenAI: {str(e)}"
        except Exception as e:
            return f"Unexpected error! Is the pipeline server running? {e}"

    async def get_gpt_response_async2(self, conversation_history):
        url = "https://api.openai.com/v1/chat/completions"
        OPENAI_KEY = self.bot.config.external.openai
        headers = {
            "Authorization": f"Bearer {OPENAI_KEY}",
            "Content-Type": "application/json"
        }
        data = {
            "model": "o1-preview",
            "messages": conversation_history
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=data) as response:
                    response_data = await response.json()
                    return response_data['choices'][0]['message']['content'].strip()
        except aiohttp.ClientError as e:
            return f"Error connecting to OpenAI: {str(e)}"
        except Exception as e:
            return f"Unexpected error! Is the pipeline server running? {e}"

    def _minigames_help_embed(self, prefix: str) -> discord.Embed:
        embed = discord.Embed(
            title=_("Mini-games Help"),
            description=_("Quick index for the community mini-games."),
            color=discord.Color.gold(),
        )
        embed.add_field(
            name=_("Olympus Dash"),
            value=(
                f"`{prefix}od start <laps 1-25> [value]` - board race with tiles, coins, shop dice, rewards.\n"
                f"`{prefix}od help` - full rules and commands.\n"
                f"`{prefix}od legend` - tile effects."
            ),
            inline=False,
        )
        embed.add_field(
            name=_("Chariot Race"),
            value=(
                f"`{prefix}chariot start [laps] [entry_fee]` - team race with actions and mystery divine cards.\n"
                f"`{prefix}chariot bet <amount> <team>` - spectator betting during betting phase.\n"
                f"`{prefix}chariot help` - full rules and commands."
            ),
            inline=False,
        )
        embed.add_field(
            name=_("Odyssey's Voyage"),
            value=(
                f"`{prefix}odysseyvoyage` or `{prefix}voyage` / `{prefix}ov` - trick-taking voyage lobby.\n"
                f"`{prefix}ov epic` - Epic Mode.\n"
                f"`{prefix}ov help` - rules and card reference."
            ),
            inline=False,
        )
        embed.add_field(
            name=_("Lykaion"),
            value=(
                f"`{prefix}lykaion start [extended|normal|fast|blitz]` - Greek social deduction lobby.\n"
                f"`{prefix}lykaion help` - roles, timers, and game flow.\n"
                f"`{prefix}lkroles` - role reference pages."
            ),
            inline=False,
        )
        embed.add_field(
            name=_("Russian Roulette"),
            value=(
                f"`{prefix}rr [bet]` - classic Russian Roulette.\n"
                f"`{prefix}rrbeta [bet] [bullets]` - beta variant with spectator bets.\n"
                f"`{prefix}rrbet <amount> @player` - bet during an RR beta lobby."
            ),
            inline=False,
        )
        embed.add_field(
            name=_("Hunger Games"),
            value=(
                f"`{prefix}hungergames` or `{prefix}hg` - join an automated elimination game.\n"
                "Last survivor wins."
            ),
            inline=False,
        )
        embed.add_field(
            name=_("Trivia & Cards"),
            value=(
                f"`{prefix}cah` - Cards Against Humanity lobby.\n"
                f"`{prefix}trivia [easy|medium|hard]` or `{prefix}tr` - trivia question."
            ),
            inline=False,
        )
        embed.add_field(
            name=_("Scheduled Mini-games"),
            value=(
                f"`{prefix}minigamescheduler` or `{prefix}mgs` - GM-only scheduler status.\n"
                f"`{prefix}mgs interval [amount] [minutes|hours]` - check or change spawn timing.\n"
                f"`{prefix}mgs roster` - check or change which games rotate."
            ),
            inline=False,
        )
        embed.set_footer(text=_("Use each mini-game's help command for detailed rules."))
        return embed

    @commands.group(name="minigames", aliases=["mg"], invoke_without_command=True)
    @commands.guild_only()
    async def minigames(self, ctx: commands.Context):
        await ctx.send(embed=self._minigames_help_embed(ctx.clean_prefix))

    @minigames.command(name="help", aliases=["h"])
    @commands.guild_only()
    async def minigames_help(self, ctx: commands.Context):
        await ctx.send(embed=self._minigames_help_embed(ctx.clean_prefix))


    @commands.hybrid_command(
        aliases=["pages", "about"], brief=_("Info about the bot and related sites")
    )
    @locale_doc
    async def web(self, ctx):
        _("""About the bot and our websites.""")
        await ctx.send(
            _(
                # xgettext: no-python-format
                """\
**EoO** is Discord's most advanced greek mythology RPG bot.
We aim to provide the perfect experience at RPG in Discord with minimum effort for the user.

We are not collecting any data apart from your character information and our transaction logs.
The bot is 100% free to use and open source.
This bot is developed by people who love to code for a good cause and improving your gameplay experience.

**Links**
<https://git.travitia.xyz/Kenvyra/IdleRPG> - Source Code (IdleRPG)
<https://git.travitia.xyz/prototypeX37/FableRPG-> - Source Code (FableRPG)
<https://github.com/Danaelis/Echoes-of-Olympus/> -Source Code (EoO)
<https://discord.com/terms> - Discord's ToS
<https://www.ncpgambling.org/help-treatment/national-helpline-1-800-522-4700/> - Gambling Helpline"""
            )
        )


    @commands.hybrid_command(brief=_("Show the rules again"))
    @locale_doc
    async def rules(self, ctx):
        _(
            """Shows the rules you consent to when creating a character. Don't forget them!"""
        )
        await ctx.send(
            _(
                """\
1) Only up to two characters per individual
2) No abusing or benefiting from bugs or exploits
3) Be friendly and kind to other players
4) Trading in-game content for anything outside of the game is prohibited
5) Giving or selling renamed items is forbidden

EoO is a global bot, your characters are valid everywhere"""
            )
        )


async def setup(bot):
    await bot.add_cog(Miscellaneous(bot))
