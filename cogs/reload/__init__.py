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
import discord
from discord.ext import commands

from utils.checks import is_gm


class ReloadCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _resolve_extension_name(self, cog_name: str) -> str:
        raw_name = cog_name.strip()
        target = raw_name if raw_name.startswith("cogs.") else f"cogs.{raw_name}"
        target_lower = target.lower()

        for loaded in self.bot.extensions:
            if loaded.lower() == target_lower:
                return loaded

        configured = getattr(self.bot.config.bot, "initial_extensions", [])
        for extension in configured:
            if extension.lower() == target_lower:
                return extension

        return target

    @commands.command(name="unload", hidden=True)
    async def unload_cog(self, ctx, cog_name: str):
        try:
            if ctx.author.id != 524674960153903126:
                return await ctx.send("Access Denied")
            # Unload the existing cog
            await ctx.send("Unloading Cog...")
            extension = self._resolve_extension_name(cog_name)
            await self.bot.unload_extension(extension)
            await ctx.send(f"{cog_name} has been unloaded.")
        except Exception as e:
            await ctx.send(f"An error occurred: {e}")

    @commands.command(name="load", hidden=True)
    async def load_cog(self, ctx, cog_name: str):
        try:
            if ctx.author.id != 524674960153903126:
                if ctx.author.id != 524674960153903126:
                    return await ctx.send("Access Denied")
            # Unload the existing cog
            await ctx.send("Loading Cog...")
            # Reload the cog using Discord.py's reload_extension
            extension = self._resolve_extension_name(cog_name)
            await self.bot.load_extension(extension)
            await ctx.send(f"{cog_name} has been loaded.")
        except Exception as e:
            await ctx.send(f"An error occurred: {e}")

    @is_gm()
    @commands.command(name="reload", hidden=True)
    async def reload_cog(self, ctx, cog_name: str):
        try:
            # Unload the existing cog
            extension = self._resolve_extension_name(cog_name)
            await self.bot.unload_extension(extension)
            await ctx.send("Reloading Cog...")
            # Reload the cog using Discord.py's reload_extension
            await self.bot.load_extension(extension)
            await ctx.send(f"{cog_name} has been reloaded.")
        except Exception as e:
            await ctx.send(f"An error occurred: {e}")


async def setup(bot):
    await bot.add_cog(ReloadCog(bot))
