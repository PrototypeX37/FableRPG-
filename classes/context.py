"""
The IdleRPG Discord Bot
Copyright (C) 2018-2021 Diniboy and Gelbpunkt
"""
from __future__ import annotations
import asyncio
from typing import TYPE_CHECKING, Optional, Union

import aiohttp
import discord
from discord.ext import commands

from classes.errors import NoChoice
from utils.i18n import _

if TYPE_CHECKING:
    from classes.bot import Bot


class Confirmation(discord.ui.View):
    def __init__(
        self,
        text: str,
        ctx: "Context",  # forward reference (Context is defined below)
        future: asyncio.Future,
        user: discord.User,
        *args,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.text = text
        self.ctx = ctx
        self.future = future
        self.allowed_user = user
        self.message: Optional[discord.Message] = None

    async def start(self) -> None:
        self.message = await self.ctx.send(
            embed=discord.Embed(
                title=_("Confirmation"),
                description=self.text,
                colour=discord.Colour.blurple(),
            ),
            view=self,
        )

    def cleanup(self) -> None:
        if self.message:
            # Fire and forget; ignore if already gone/closed
            async def _delete():
                try:
                    await self.message.delete()
                except Exception:
                    pass
            asyncio.create_task(_delete())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.allowed_user.id == interaction.user.id:
            return True
        asyncio.create_task(
            interaction.response.send_message(
                _("This command was not initiated by you."), ephemeral=True
            )
        )
        return False

    async def on_timeout(self) -> None:
        self.cleanup()
        if not self.future.done():
            self.future.set_exception(NoChoice(_("You didn't choose anything.")))

    @discord.ui.button(emoji="❌", style=discord.ButtonStyle.red, row=0)
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        # Defer to avoid "interaction failed" toast; the message will be deleted anyway.
        if not interaction.response.is_done():
            await interaction.response.defer()
        if not self.future.done():
            self.future.set_result(False)
        self.stop()
        self.cleanup()

    @discord.ui.button(emoji="✔️", style=discord.ButtonStyle.green, row=0)
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not interaction.response.is_done():
            await interaction.response.defer()
        if not self.future.done():
            self.future.set_result(True)
        self.stop()
        self.cleanup()


class Context(commands.Context):
    """
    A custom version of the default Context.
    Provides a shortcut to the display name and
    safely resets cooldowns on declined confirmations.
    """

    bot: "Bot"
    RETRYABLE_HTTP_STATUSES = {429, 500, 502, 503, 504}
    RETRYABLE_NETWORK_ERRNOS = {54, 104}  # ECONNRESET (macOS/Linux)
    DEFAULT_HTTP_RETRIES = 3
    DEFAULT_HTTP_RETRY_DELAY = 0.75
    MAX_HTTP_RETRY_DELAY = 10.0

    @property
    def disp(self) -> str:
        return self.author.display_name

    def __repr__(self):
        return "<Context>"

    async def send(self, *args, **kwargs):
        # Files are not always safe to resend after a failed request.
        if kwargs.get("file") is not None or kwargs.get("files"):
            return await super().send(*args, **kwargs)

        retries = max(
            0, int(kwargs.pop("_http_retries", self.DEFAULT_HTTP_RETRIES))
        )
        base_delay = float(
            kwargs.pop("_http_retry_delay", self.DEFAULT_HTTP_RETRY_DELAY)
        )

        attempt = 0
        while True:
            try:
                return await super().send(*args, **kwargs)
            except discord.RateLimited as exc:
                if attempt >= retries:
                    raise
                retry_after = getattr(exc, "retry_after", None)
                if not retry_after:
                    retry_after = base_delay * (2**attempt)
                await asyncio.sleep(min(float(retry_after), self.MAX_HTTP_RETRY_DELAY))
                attempt += 1
            except discord.HTTPException as exc:
                status = getattr(exc, "status", None)
                if status not in self.RETRYABLE_HTTP_STATUSES or attempt >= retries:
                    raise
                retry_after = getattr(exc, "retry_after", None)
                if not retry_after:
                    retry_after = base_delay * (2**attempt)
                await asyncio.sleep(min(float(retry_after), self.MAX_HTTP_RETRY_DELAY))
                attempt += 1
            except (
                aiohttp.ClientConnectionError,
                aiohttp.ServerDisconnectedError,
                asyncio.TimeoutError,
                ConnectionResetError,
            ):
                if attempt >= retries:
                    raise
                retry_after = base_delay * (2**attempt)
                await asyncio.sleep(min(float(retry_after), self.MAX_HTTP_RETRY_DELAY))
                attempt += 1
            except OSError as exc:
                if getattr(exc, "errno", None) not in self.RETRYABLE_NETWORK_ERRNOS:
                    raise
                if attempt >= retries:
                    raise
                retry_after = base_delay * (2**attempt)
                await asyncio.sleep(min(float(retry_after), self.MAX_HTTP_RETRY_DELAY))
                attempt += 1

    async def confirm(
        self,
        message: str,
        timeout: int = 60,
        user: Union[discord.User, discord.Member, None] = None,
    ) -> bool:
        future: asyncio.Future[bool] = asyncio.Future()
        await Confirmation(
            message, self, future, user=user or self.author, timeout=timeout
        ).start()

        try:
            confirmed = await future
        except NoChoice:
            # Timeout -> treat as declined and reset cooldowns
            confirmed = False

        if confirmed:
            return True

        # Reset cooldowns on decline
        await self.bot.reset_cooldown(self)
        if self.command and self.command.root_parent:
            if self.command.root_parent.name == "guild":
                await self.bot.reset_guild_cooldown(self)
            elif self.command.root_parent.name == "alliance":
                await self.bot.reset_alliance_cooldown(self)
        return False
