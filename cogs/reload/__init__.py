"""
The IdleRPG Discord Bot
Copyright (C) 2018-2021 Diniboy and Gelbpunkt
Copyright (C) 2023-2024 Lunar (PrototypeX37)

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

    @commands.command(name="unload", hidden=True)
    async def unload_cog(self, ctx, cog_name: str):
        try:
            if ctx.author.id != 295173706496475136:
                return await ctx.send("Access Denied")
            # Unload the existing cog
            await ctx.send("Unloading Cog...")
            await self.bot.unload_extension(f"cogs.{cog_name}")
            await ctx.send(f"{cog_name} has been unloaded.")
        except Exception as e:
            await ctx.send(f"An error occurred: {e}")

    @commands.command(name="load", hidden=True)
    async def load_cog(self, ctx, cog_name: str):
        try:
            if ctx.author.id != 295173706496475136:
                if ctx.author.id != 708435868842459169:
                    return await ctx.send("Access Denied")
            # Unload the existing cog
            await ctx.send("Loading Cog...")
            # Reload the cog using Discord.py's reload_extension
            await self.bot.load_extension(f"cogs.{cog_name}")
            await ctx.send(f"{cog_name} has been loaded.")
        except Exception as e:
            await ctx.send(f"An error occurred: {e}")

    @is_gm()
    @commands.command(name="reload", hidden=True)
    async def reload_cog(self, ctx, cog_name: str):
        try:
            # Unload the existing cog
            await self.bot.unload_extension(f"cogs.{cog_name}")
            await ctx.send("Reloading Cog...")
            # Reload the cog using Discord.py's reload_extension
            await self.bot.load_extension(f"cogs.{cog_name}")
            await ctx.send(f"{cog_name} has been reloaded.")
        except Exception as e:
            await ctx.send(f"An error occurred: {e}")


async def setup(bot):
    await bot.add_cog(ReloadCog(bot))
