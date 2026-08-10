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
import importlib
import importlib.util
import sys
from pathlib import Path

import discord
from discord.ext import commands

from utils.checks import is_gm


class ReloadCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @staticmethod
    def _normalize_module_name(module: str) -> str:
        name = module.strip()
        if name.endswith(".py"):
            name = name[:-3]
        return name.replace("\\", ".").replace("/", ".")

    def _resolve_shared_module_name(self, module: str) -> str:
        module_name = self._normalize_module_name(module)
        if module_name.startswith(("utils.", "classes.")):
            return module_name
        if "." in module_name:
            raise ValueError("Shared modules must be under `utils` or `classes`.")

        for candidate in (f"utils.{module_name}", f"classes.{module_name}"):
            if candidate in sys.modules or importlib.util.find_spec(candidate):
                return candidate

        raise ValueError(f"Could not find `utils.{module_name}` or `classes.{module_name}`.")

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

    def _reset_runtime_storage_clients(self):
        for cog_name in ("Patreon", "ProcessSplice"):
            cog = self.bot.get_cog(cog_name)
            if not cog:
                continue
            for attr in ("_r2_client", "_r2_bucket", "_r2_public_base_url"):
                if hasattr(cog, attr):
                    setattr(cog, attr, None)

    def _load_utils_config_module(self):
        """
        Load utils.config explicitly from utils/config.py to avoid root config.py conflicts.
        """
        config_module = importlib.import_module("utils.config")
        module_file = (getattr(config_module, "__file__", None) or "").replace("\\", "/")
        if module_file.endswith("/utils/config.py"):
            return importlib.reload(config_module)

        config_path = Path(__file__).resolve().parents[2] / "utils" / "config.py"
        spec = importlib.util.spec_from_file_location("utils.config", str(config_path))
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Unable to create module spec for {config_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.modules["utils.config"] = module
        return module

    async def _reload_runtime_config(self):
        config_module = self._load_utils_config_module()
        self.bot.config = config_module.ConfigLoader("config.toml")
        self.bot.version = self.bot.config.bot.version
        self.bot.BASE_URL = self.bot.config.external.base_url
        self.bot.support_server_id = self.bot.config.game.support_server_id
        self.bot.activity = discord.Game(
            name=f"Fable v{self.bot.version}"
            if self.bot.config.bot.is_beta
            else self.bot.BASE_URL
        )
        self._reset_runtime_storage_clients()

        try:
            await self.bot.change_presence(activity=self.bot.activity)
        except Exception:
            pass

    async def _reload_python_module(self, module_name: str):
        loaded_module = importlib.import_module(module_name)
        return importlib.reload(loaded_module)

    async def _reload_extension_or_load(self, extension: str) -> str:
        try:
            await self.bot.reload_extension(extension)
            return f"Reloaded cog: `{extension}`"
        except commands.ExtensionNotLoaded:
            await self.bot.load_extension(extension)
            return f"Cog was not loaded; loaded now: `{extension}`"

    @is_gm()
    @commands.command(name="unload", hidden=True)
    async def unload_cog(self, ctx, cog_name: str):
        extension = self._resolve_extension_name(cog_name)
        try:
            await self.bot.unload_extension(extension)
            await ctx.send(f"Unloaded cog: {extension}")
        except commands.ExtensionNotLoaded:
            await ctx.send(f"Cog is not loaded: {extension}")
        except commands.ExtensionNotFound:
            await ctx.send(f"Cog not found: {extension}")
        except Exception as e:
            await ctx.send(f"Failed to unload cog: {extension}\n{type(e).__name__}: {e}")

    @is_gm()
    @commands.command(name="load", hidden=True)
    async def load_cog(self, ctx, cog_name: str):
        extension = self._resolve_extension_name(cog_name)
        try:
            await self.bot.load_extension(extension)
            await ctx.send(f"Loaded cog: {extension}")
        except commands.ExtensionAlreadyLoaded:
            await ctx.send(f"Cog already loaded: {extension}")
        except commands.ExtensionNotFound:
            await ctx.send(f"Cog not found: {extension}")
        except Exception as e:
            await ctx.send(f"Failed to load cog: {extension}\n{type(e).__name__}: {e}")

    @is_gm()
    @commands.command(name="reload", hidden=True)
    async def reload_cog(self, ctx, cog_name: str):
        extension = self._resolve_extension_name(cog_name)
        try:
            await self.bot.reload_extension(extension)
            await ctx.send(f"Reloaded cog: {extension}")
        except commands.ExtensionNotLoaded:
            await self.bot.load_extension(extension)
            await ctx.send(f"Cog was not loaded; loaded now: {extension}")
        except commands.ExtensionNotFound:
            await ctx.send(f"Cog not found: {extension}")
        except Exception as e:
            await ctx.send(f"Failed to reload cog: {extension}\n{type(e).__name__}: {e}")

    @is_gm()
    @commands.command(name="reloadconfig", aliases=["rcfg"], hidden=True)
    async def reload_config(self, ctx):
        try:
            await self._reload_runtime_config()
            await ctx.send("Reloaded `utils.config` and `config.toml` successfully.")
        except Exception as e:
            await ctx.send(f"Failed to reload config\n{type(e).__name__}: {e}")

    @is_gm()
    @commands.command(name="reloadfile", hidden=True)
    async def reload_file(self, ctx, *, module: str):
        module_name = self._normalize_module_name(module)
        try:
            if module_name in {"config.toml", "config", "utils.config"}:
                await self._reload_runtime_config()
                return await ctx.send(f"Reloaded runtime config via `{module}`.")

            if not module_name.startswith(("utils.", "classes.")):
                return await ctx.send(
                    "Supported targets: `utils.*`, `classes.*`, or `config.toml`."
                )

            await self._reload_python_module(module_name)
            await ctx.send(f"Reloaded module: `{module_name}`")
        except Exception as e:
            await ctx.send(f"Failed to reload `{module}`\n{type(e).__name__}: {e}")

    @is_gm()
    @commands.command(name="reloadshared", aliases=["rshared"], hidden=True)
    async def reload_shared(self, ctx, *targets: str):
        """
        Reload utils/classes modules and then reload dependent cogs.

        Usage:
          $reloadshared utils.loot classes.errors --cogs profile gods
          $reloadshared loot errors --cogs profile gods
        """
        if not targets:
            return await ctx.send(
                "Usage: `$reloadshared utils.loot classes.errors --cogs profile gods`"
            )

        if "--cogs" in targets:
            split_at = targets.index("--cogs")
            module_targets = targets[:split_at]
            cog_targets = targets[split_at + 1 :]
        else:
            module_targets = targets
            cog_targets = ()

        if not module_targets:
            return await ctx.send("Give at least one `utils.*` or `classes.*` module.")

        messages = []
        try:
            for target in module_targets:
                module_name = self._resolve_shared_module_name(target)
                await self._reload_python_module(module_name)
                messages.append(f"Reloaded module: `{module_name}`")

            for cog_name in cog_targets:
                extension = self._resolve_extension_name(cog_name)
                messages.append(await self._reload_extension_or_load(extension))

            await ctx.send("\n".join(messages))
        except Exception as e:
            await ctx.send(
                f"Failed to reload shared target\n{type(e).__name__}: {e}"
            )

    @is_gm()
    @commands.command(name="reloadloot", aliases=["rloot"], hidden=True)
    async def reload_loot_helpers(self, ctx):
        try:
            messages = []
            for module_name in ("classes.errors", "utils.loot"):
                await self._reload_python_module(module_name)
                messages.append(f"Reloaded module: `{module_name}`")

            for extension in ("cogs.gods", "cogs.profile"):
                messages.append(await self._reload_extension_or_load(extension))

            await ctx.send("\n".join(messages))
        except Exception as e:
            await ctx.send(f"Failed to reload loot helpers\n{type(e).__name__}: {e}")


async def setup(bot):
    await bot.add_cog(ReloadCog(bot))
