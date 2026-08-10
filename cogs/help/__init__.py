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
import math

import discord

from discord.ext import commands
from discord.ext.commands.core import Command
from discord.interactions import Interaction
from discord.ui import Button, View, button
from urllib.parse import urlparse

from classes.bot import Bot
from classes.context import Context
from utils.checks import user_is_gm
from utils.i18n import _, locale_doc

DEFAULT_BASE_URL = "https://echoesofolympus.ovh"


def normalize_base_url(base_url: str | None) -> str:
    if not isinstance(base_url, str):
        return DEFAULT_BASE_URL

    candidate = base_url.strip().rstrip("/")
    if not candidate:
        return DEFAULT_BASE_URL

    parsed = urlparse(candidate)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return candidate

    return DEFAULT_BASE_URL


def chunks(iterable, size):
    """Yield successive n-sized chunks from an iterable."""
    for i in range(0, len(iterable), size):
        yield iterable[i : i + size]


class CogMenu(View):
    def __init__(
        self,
        *,
        title: str,
        description: str,
        bot: Bot,
        color: int,
        footer: str,
        per_page: int = 5,
    ) -> None:
        self.title = title
        self.description = description
        self.bot = bot
        self.color = color
        self.footer = footer
        self.per_page = per_page
        self.page = 1
        self.message: discord.Message | None = None
        self.allowed_user: discord.User | None = None

        super().__init__(timeout=60.0)

    @property
    def pages(self) -> int:
        return math.ceil(len(self.description) / self.per_page)

    def embed(self, desc: str) -> discord.Embed:
        e = discord.Embed(
            title=self.title, color=self.color, description="\n".join(desc)
        )
        e.set_author(
            name=self.bot.user,
            icon_url=self.bot.user.display_avatar.url,
        )
        e.set_footer(
            text=f"{self.footer} | Page {self.page}/{self.pages}",
            icon_url=self.bot.user.display_avatar.url,
        )
        return e

    def should_process(self) -> bool:
        return len(self.description) > self.per_page

    def cleanup(self) -> None:
        asyncio.create_task(self.message.delete())

    async def on_timeout(self) -> None:
        self.cleanup()

    async def start(self, ctx: Context) -> None:
        self.allowed_user = ctx.author
        e = self.embed(self.description[0 : self.per_page])

        if self.should_process():
            self.message = await ctx.send(embed=e, view=self)
        else:
            self.message = await ctx.send(embed=e)

    async def update(self) -> None:
        start = (self.page - 1) * self.per_page
        end = self.page * self.per_page
        items = self.description[start:end]
        e = self.embed(items)
        await self.message.edit(embed=e)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.allowed_user.id == interaction.user.id:
            return True
        else:
            asyncio.create_task(
                interaction.response.send_message(
                    _("This command was not initiated by you."), ephemeral=True
                )
            )
            return False

    @button(
        label="Previous",
        style=discord.ButtonStyle.blurple,
        emoji="\N{BLACK LEFT-POINTING TRIANGLE}\ufe0f",
    )
    async def on_previous_page(self, interaction: Interaction, button: Button) -> None:
        if self.page != 1:
            self.page -= 1
            await self.update()

    @button(
        label="Stop",
        style=discord.ButtonStyle.red,
        emoji="\N{BLACK SQUARE FOR STOP}\ufe0f",
    )
    async def on_stop(self, interaction: Interaction, button: Button) -> None:
        self.cleanup()
        self.stop()

    @button(
        label="Next",
        style=discord.ButtonStyle.blurple,
        emoji="\N{BLACK RIGHT-POINTING TRIANGLE}\ufe0f",
    )
    async def on_next_page(self, interaction: Interaction, button: Button) -> None:
        if len(self.description) >= (self.page * self.per_page):
            self.page += 1
            await self.update()


class SubcommandMenu(View):
    def __init__(
        self,
        *,
        cmds: list[commands.Command],
        title: str,
        description: str,
        bot: Bot,
        color: int,
        per_page: int = 5,
    ) -> None:
        self.cmds = cmds
        self.title = title
        self.description = description
        self.bot = bot
        self.color = color
        self.per_page = per_page
        self.page = 1
        self.group_emoji = "💠"
        self.command_emoji = "🔷"

        self.message: discord.Message | None = None
        self.ctx: commands.Context | None = None

        super().__init__(timeout=60.0)

    @property
    def pages(self) -> int:
        return math.ceil(len(self.cmds) / self.per_page)

    def embed(self, cmds: list[commands.Command]) -> discord.Embed:
        e = discord.Embed(
            title=self.title, color=self.color, description=self.description
        )
        e.set_author(
            name=self.bot.user,
            icon_url=self.bot.user.display_avatar.url,
        )
        e.add_field(
            name=_("Subcommands"),
            value="\n".join(
                [
                    f"{self.group_emoji if isinstance(c, commands.Group) else self.command_emoji}"
                    f" `{self.ctx.clean_prefix}{c.qualified_name}` - {_(c.brief)}"
                    for c in cmds
                ]
            ),
        )
        if self.should_process():
            e.set_footer(
                icon_url=self.bot.user.display_avatar.url,
                text=_(
                    "Click on the buttons to see more subcommands. | Page"
                    " {start}/{end}"
                ).format(start=self.page, end=self.pages),
            )
        return e

    def should_process(self) -> bool:
        return len(self.cmds) > self.per_page

    def cleanup(self) -> None:
        asyncio.create_task(self.message.delete())

    async def on_timeout(self) -> None:
        self.cleanup()

    async def start(self, ctx: Context) -> None:
        self.ctx = ctx
        e = self.embed(self.cmds[0 : self.per_page])

        if self.should_process():
            self.message = await ctx.send(embed=e, view=self)
        else:
            self.message = await ctx.send(embed=e)

    async def update(self) -> None:
        start = (self.page - 1) * self.per_page
        end = self.page * self.per_page
        items = self.cmds[start:end]
        e = self.embed(items)
        await self.message.edit(embed=e)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.ctx.author.id == interaction.user.id:
            return True
        else:
            asyncio.create_task(
                interaction.response.send_message(
                    _("This command was not initiated by you."), ephemeral=True
                )
            )
            return False

    @button(
        label="Previous",
        style=discord.ButtonStyle.blurple,
        emoji="\N{BLACK LEFT-POINTING TRIANGLE}\ufe0f",
    )
    async def on_previous_page(self, interaction: Interaction, button: Button) -> None:
        if self.page != 1:
            self.page -= 1
            await self.update()

    @button(
        label="Stop",
        style=discord.ButtonStyle.red,
        emoji="\N{BLACK SQUARE FOR STOP}\ufe0f",
    )
    async def on_stop(self, interaction: Interaction, button: Button) -> None:
        self.cleanup()
        self.stop()

    @button(
        label="Next",
        style=discord.ButtonStyle.blurple,
        emoji="\N{BLACK RIGHT-POINTING TRIANGLE}\ufe0f",
    )
    async def on_next_page(self, interaction: Interaction, button: Button) -> None:
        if len(self.cmds) >= (self.page * self.per_page):
            self.page += 1
            await self.update()


class Help(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def docs_url(self) -> str:
        return normalize_base_url(getattr(self.bot, "BASE_URL", None))

    def tutorial_url(self) -> str:
        return f"{self.docs_url()}/tutorial"

    @commands.command(aliases=["commands", "cmds"], brief=_("View the command list"))
    @locale_doc
    async def documentation(self, ctx):
        _("""Sends a link to the official documentation.""")
        await ctx.send(
            _(
                "**Check {url} for a list of"
                " commands**"
            ).format(url=self.docs_url())
        )

    @commands.command(aliases=["faq", "quickstart"], brief=_("View the tutorial"))
    @locale_doc
    async def tutorial(self, ctx):
        _("""Shows a quickstart tutorial and links to the full tutorial and FAQ.""")
        tutorial_url = self.tutorial_url()
        docs_url = self.docs_url()
        prefix = ctx.clean_prefix

        e = discord.Embed(
            title=_("Fable Quickstart"),
            colour=self.bot.config.game.primary_colour,
            url=tutorial_url,
            description=_(
                "Use these core commands to get started, then open the full tutorial"
                " for deeper guidance."
            ),
        )
        e.set_author(name=self.bot.user, icon_url=self.bot.user.display_avatar.url)
        e.add_field(
            name=_("Start your character"),
            value=_(
                "`{prefix}create` to begin your journey."
            ).format(prefix=prefix),
            inline=False,
        )
        e.add_field(
            name=_("Learn commands fast"),
            value=_(
                "`{prefix}help` for command help and `{prefix}documentation` for the"
                " full command list."
            ).format(prefix=prefix),
            inline=False,
        )
        e.add_field(
            name=_("Configure your experience"),
            value=_(
                "`{prefix}settings prefix` to change the prefix, and"
                " `{prefix}language set` to set your language."
            ).format(prefix=prefix),
            inline=False,
        )

        view = discord.ui.View()
        view.add_item(discord.ui.Button(label=_("Open Full Tutorial"), url=tutorial_url))
        view.add_item(discord.ui.Button(label=_("Open Documentation"), url=docs_url))
        await ctx.send(embed=e, view=view)

class IdleHelp(commands.HelpCommand):
    def __init__(self, *args, **kwargs):
        kwargs["command_attrs"] = {
            "brief": _("Views the help on a topic."),
            "help": _(
                """Views the help on a topic.

            The topic may either be a command name or a module name.
            Command names are always preferred, so for example, `{prefix}help adventure`
            will show the help on the command, not the module.

            To view the help on a module explicitely, use `{prefix}help module [name]`"""
            ),
        }

        super().__init__(*args, **kwargs)
        self.verify_checks = False
        self.color = None
        self.gm_exts = {"GameMaster"}
        self.owner_exts = {"Owner"}
        self.group_emoji = "💠"
        self.command_emoji = "🔷"

    async def command_callback(self, ctx, *, command=None):
        await self.prepare_help_command(ctx, command)
        bot = ctx.bot

        if command is None:
            mapping = self.get_bot_mapping()
            return await self.send_bot_help(mapping)

        PREFER_COG = False
        if command.lower().startswith(("module ", "module:")):
            command = command[7:]
            PREFER_COG = True

        if PREFER_COG:
            if command.lower() == "gamemaster":
                command = "GameMaster"
            else:
                command = command.title()
            cog = bot.get_cog(command)
            if cog is not None:
                return await self.send_cog_help(cog)

        maybe_coro = discord.utils.maybe_coroutine

        keys = command.split(" ")
        cmd = bot.all_commands.get(keys[0])
        if cmd is None:
            cog = bot.get_cog(command.title())
            if cog is not None:
                return await self.send_cog_help(cog)

            string = await maybe_coro(
                self.command_not_found, self.remove_mentions(keys[0])
            )
            return await self.send_error_message(string)

        for key in keys[1:]:
            try:
                found = cmd.all_commands.get(key)
            except AttributeError:
                string = await maybe_coro(
                    self.subcommand_not_found, cmd, self.remove_mentions(key)
                )
                return await self.send_error_message(string)
            else:
                if found is None:
                    string = await maybe_coro(
                        self.subcommand_not_found, cmd, self.remove_mentions(key)
                    )
                    return await self.send_error_message(string)
                cmd = found

        if isinstance(cmd, commands.Group):
            return await self.send_group_help(cmd)
        else:
            return await self.send_command_help(cmd)

    async def send_bot_help(self, mapping):
        base_url = normalize_base_url(getattr(self.context.bot, "BASE_URL", None))
        tutorial_url = f"{base_url}/tutorial"
        e = discord.Embed(
            title=_(
                "Fable Help {version}",
            ).format(version=self.context.bot.version),
            color=self.context.bot.config.game.primary_colour,
            url="https://wiki.fablerpg.xyz/",
        )
        e.set_author(
            name=self.context.bot.user,
            icon_url=self.context.bot.user.display_avatar.url,
        )
        e.description = _(
            "**Welcome to the Fable help.**\n"
            "Check out our tutorial:\n"
            "- {tutorial_url}\n"
            "Need a quick summary? Use `{prefix}tutorial`."
        ).format(prefix=self.context.clean_prefix, tutorial_url=tutorial_url)

        has_gm_access = await user_is_gm(self.context.bot, self.context.author)
        allowed = []
        for cog in sorted(mapping.keys(), key=lambda x: x.qualified_name if x else ""):
            if cog is None:
                continue
            if (
                not has_gm_access
                and cog.qualified_name in self.gm_exts
            ):
                continue
            if (
                self.context.author.id not in self.context.bot.owner_ids
                and cog.qualified_name in self.owner_exts
            ):
                continue
            if (
                cog.qualified_name not in self.gm_exts
                and len([c for c in cog.get_commands() if not c.hidden]) == 0
            ):
                continue
            allowed.append(cog.qualified_name)
        cogs = [allowed[x : x + 3] for x in range(0, len(allowed), 3)]
        length_list = [len(element) for row in cogs for element in row]
        column_width = max(length_list)
        rows = []
        for row in cogs:
            rows.append("".join(element.ljust(column_width + 2) for element in row))
        e.add_field(name=_("Modules"), value="```{}```".format("\n".join(rows)))

        await self.context.send(embed=e)

    async def send_cog_help(self, cog):
        has_gm_access = await user_is_gm(self.context.bot, self.context.author)
        if (cog.qualified_name in self.gm_exts) and (
            not has_gm_access
        ):
            if self.context.author.id in self.context.bot.owner_ids:
                pass  # owners don't have restrictions
            else:
                return await self.context.send(
                    _("You do not have access to these commands!")
                )
        if (cog.qualified_name in self.owner_exts) and (
            self.context.author.id not in self.context.bot.owner_ids
        ):
            return await self.context.send(
                _("You do not have access to these commands!")
            )

        menu = CogMenu(
            title=(
                f"[{cog.qualified_name.upper()}] {len(set(cog.walk_commands()))}"
                " commands"
            ),
            bot=self.context.bot,
            color=self.context.bot.config.game.primary_colour,
            description=[
                f"{self.group_emoji if isinstance(c, commands.Group) else self.command_emoji}"
                f" `{self.context.clean_prefix}{c.qualified_name} {c.signature}` - {_(c.brief) if c.brief else _('No brief help available')}"
                for c in cog.get_commands()
            ],
            footer=_("See '{prefix}help <command>' for more detailed info").format(
                prefix=self.context.clean_prefix
            ),
        )

        await menu.start(self.context)

    async def send_command_help(self, command: Command):
        has_gm_access = await user_is_gm(self.context.bot, self.context.author)
        if command.cog:
            if (command.cog.qualified_name in self.gm_exts) and (
                not has_gm_access
            ):
                if self.context.author.id in self.context.bot.owner_ids:
                    pass  # owners don't have restrictions
                else:
                    return await self.context.send(
                        _("You do not have access to this command!")
                    )
            if (command.cog.qualified_name in self.owner_exts) and (
                self.context.author.id not in self.context.bot.owner_ids
            ):
                return await self.context.send(
                    _("You do not have access to this command!")
                )

        e = discord.Embed(
            title=(
                f"[{command.cog.qualified_name.upper()}] {command.qualified_name}"
                f" {command.signature}"
            ),
            colour=self.context.bot.config.game.primary_colour,
            description=_(command.help).format(prefix=self.context.clean_prefix)
            if command.help
            else _("No help available"),
        )
        e.set_author(
            name=self.context.bot.user,
            icon_url=self.context.bot.user.display_avatar.url,
        )

        if command.aliases:
            e.add_field(
                name=_("Aliases"), value="`{}`".format("`, `".join(command.aliases))
            )
        await self.context.send(embed=e)

    async def send_group_help(self, group):
        has_gm_access = await user_is_gm(self.context.bot, self.context.author)
        if group.cog:
            if (
                not has_gm_access
                and group.cog.qualified_name in self.gm_exts
            ):
                return await self.context.send(
                    _("You do not have access to this command!")
                )
            if (
                self.context.author.id not in self.context.bot.owner_ids
                and group.cog.qualified_name in self.owner_exts
            ):
                return await self.context.send(
                    _("You do not have access to this command!")
                )

        menu = SubcommandMenu(
            title=(
                f"[{group.cog.qualified_name.upper()}] {group.qualified_name}"
                f" {group.signature}"
            ),
            bot=self.context.bot,
            color=self.context.bot.config.game.primary_colour,
            description=_(group.help).format(prefix=self.context.clean_prefix),
            cmds=list(group.commands),
        )
        await menu.start(self.context)


async def setup(bot: Bot) -> None:
    bot.remove_command("help")
    await bot.add_cog(Help(bot))
    bot.help_command = IdleHelp()
    bot.help_command.cog = bot.get_cog("Help")
