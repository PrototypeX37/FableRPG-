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

import discord
from discord import Embed

from discord.enums import ButtonStyle
from discord.ext import commands
from discord.ui.button import Button

from cogs.help import chunks
from utils import random
from utils.checks import is_gm, user_is_gm
from utils.i18n import _, locale_doc
from utils.joins import JoinView
from utils.misc import nice_join


JOIN_TIMEOUT = 60 * 5


class BeginJoinView(JoinView):
    def __init__(self, *args, bot, host_id: int, force_begin: asyncio.Event, **kwargs):
        super().__init__(*args, **kwargs)
        self.bot = bot
        self.host_id = host_id
        self.force_begin = force_begin
        self._children.sort(
            key=lambda child: 1 if getattr(child, "label", None) == "Begin Now" else 0
        )

    @discord.ui.button(label="Begin Now", style=discord.ButtonStyle.primary)
    async def begin(self, interaction: discord.Interaction, button: discord.ui.Button):
        is_gm_user = await user_is_gm(self.bot, interaction.user)
        if interaction.user.id != self.host_id and not is_gm_user:
            return await interaction.response.send_message(
                "Only the host/GM can begin now.",
                ephemeral=True,
            )
        self.force_begin.set()
        await interaction.response.send_message("Beginning now...", ephemeral=True)


class FateSelect(discord.ui.Select):
    def __init__(self, parent_view, entries):
        self.parent_view = parent_view
        options = [
            discord.SelectOption(label=entry[:100], value=str(index))
            for index, entry in enumerate(entries)
        ]
        super().__init__(
            placeholder="Choose your fate",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        self.parent_view.choice_index = int(self.values[0])
        self.parent_view.stop()
        await interaction.response.edit_message(
            content="Your fate is sealed.",
            view=None,
        )


class FateSelectView(discord.ui.View):
    def __init__(self, parent_view, entries):
        super().__init__(timeout=45)
        self.parent_view = parent_view
        self.add_item(FateSelect(parent_view, entries))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.parent_view.player.id:
            await interaction.response.send_message(
                "This fate is not yours to choose.",
                ephemeral=True,
            )
            return False
        return True


class FatePromptView(discord.ui.View):
    def __init__(self, player, entries):
        super().__init__(timeout=45)
        self.player = player
        self.entries = entries
        self.choice_index = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.player.id:
            await interaction.response.send_message(
                "This fate is not yours to choose.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Choose Fate", style=discord.ButtonStyle.primary)
    async def choose_fate(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Choose your fate. Only you can see these options.",
            view=FateSelectView(self, self.entries),
            ephemeral=True,
        )

    async def disable_prompt(self, message):
        for child in self.children:
            child.disabled = True
        try:
            await message.edit(view=self)
        except discord.HTTPException:
            pass


class GameBase:
    def __init__(self, ctx, players: list, reward: int = 0):
        self.players = players
        self.ctx = ctx
        self.reward = max(0, int(reward))

    def rand_chunks(self, iterable):
        idx = 0
        for i in range(0, len(iterable)):
            if i < idx:
                continue
            num = random.randint(1, 4)
            yield iterable[i : i + num]
            idx += num

    async def get_inputs(self):
        all_actions = [
            (
                _("Gather as much ambrosia as you can"),
                _("gathers as much ambrosia as they can"),
                None,
            ),
            (
                _("Grab a satchel and retreat to the olive groves"),
                _("grabs a satchel and retreats to the olive groves"),
                "leave",
            ),
            (_("Drink hemlock and accept fate"), _("drinks hemlock and falls"), "leave"),
            (_("Throw yourself on a spear"), _("throws themselves on a spear"), "leave"),
            (
                _("Run from the altar of plenty"),
                _("runs from the altar of plenty"),
                None,
            ),
            (
                _("Search for a bundle of Greek fire"),
                _("finds a bundle of Greek fire"),
                None,
            ),
            (_("Look for a sacred spring"), _("finds a sacred spring"), None),
            (
                _("Get a healer's kit from Asclepius' shrine"),
                _("clutches a healer's kit from Asclepius' shrine and runs away"),
                None,
            ),
            (
                _("Grab a bronze satchel"),
                _("grabs a bronze satchel, not realizing it is empty"),
                None,
            ),
            (_("Challenge USER to single combat"), _("slays USER in single combat"), ("kill", "USER")),
            (
                _("Ambush USER at the sacred spring"),
                _("ambushes USER while they drink at the sacred spring"),
                ("kill", "USER"),
            ),
            (
                _("Try to hide cursed traps"),
                _("hides cursed traps across the arena"),
                None,
            ),
            (
                _("Bathe in a marble fountain"),
                _("bathes in a marble fountain and enjoys the silence"),
                None,
            ),
        ]
        team_actions = [
            (_("strike down USER"), ("kill", "USER")),
            (_("feast by a brazier and trade heroic myths"), None),
            (_("mock USER in the name of Dionysus"), "user"),
            (_("meet their doom by stepping on a cursed trap"), "killall"),
            (_("hold a Dionysian revel and get drunk"), None),
            (_("watch the stars and trade prophecies"), None),
            (_("listen to the winds of Olympus"), None),
            (_("attempt to strike USER but Athena denies them"), "user"),
            (_("perform a tragic play to distract USER"), "user"),
            (_("track down USER and strike from the shadows"), ("kill", "USER")),
        ]
        team_actions_2 = [
            (_("meet their doom by stepping on a cursed trap"), "killall"),
            (_("beg the Fates for release and end their own lives"), "killall"),
            (_("sit together and recite Homeric verses"), None),
            (_("dance a war paean together"), None),
            (_("sing hymns to Apollo together"), None),
            (_("share a quiet evening beneath Olympus"), None),
            (_("watch the others blunder like lost satyrs"), None),
            (_("kiss beneath Selene's moonlight"), None),
            (
                _("recite a tragedy together when USER is struck by a hidden arrow"),
                ("killtogether", "USER"),
            ),
        ]
        user_actions = []
        roundtext = _("**Trial {round} of Olympus**")
        status = await self.ctx.send(
            roundtext.format(round=self.round), delete_after=60
        )
        killed_this_round = []
        for p in self.rand_chunks(self.players):
            if len(p) == 1:
                text = _("The Fates call on {user} to choose...").format(user=p[0])
                try:
                    await status.edit(content=f"{status.content}\n{text}")
                except discord.errors.NotFound:
                    status = await self.ctx.send(
                        f"{roundtext.format(round=self.round)}\n{text}", delete_after=60
                    )
                actions = random.sample(all_actions, 3)
                possible_kills = [
                    item
                    for item in self.players
                    if item not in killed_this_round and item != p[0]
                ]
                if len(possible_kills) > 0:
                    kill = random.choice(possible_kills)
                    okay = True
                else:
                    kill = random.choice([i for i in self.players if i != p[0]])
                    okay = False
                actions2 = []
                for a, b, c in actions:
                    if c == ("kill", "USER"):
                        actions2.append(
                            (
                                a.replace("USER", kill.name),
                                b.replace("USER", kill.name),
                                ("kill", kill),
                            )
                        )
                    else:
                        actions2.append((a, b, c))
                actions_desc = [a[0] for a in actions2]
                view = FatePromptView(p[0], actions_desc)
                prompt = await self.ctx.send(
                    _("{user}, choose your fate.").format(user=p[0].mention),
                    view=view,
                    delete_after=60,
                )
                timed_out = await view.wait()
                await view.disable_prompt(prompt)
                if timed_out or view.choice_index is None:
                    await self.ctx.send(
                        _(
                            "{user} did not choose in time! Choosing a fate at random..."
                        ).format(user=p[0]),
                        delete_after=30,
                    )
                    action = random.choice(actions2)
                else:
                    action = actions2[view.choice_index]
                if okay or (not okay and isinstance(action[2], tuple)):
                    user_actions.append((p[0], action[1]))
                else:
                    user_actions.append(
                        (p[0], _("attempts to strike {user} but fails").format(user=kill))
                    )
                if action[2]:
                    if action[2] == "leave":
                        killed_this_round.append(p[0])
                    else:
                        if okay:
                            killed_this_round.append(action[2][1])
                text = _("Judged")
                try:
                    await status.edit(content=f"{status.content} {text}")
                except discord.errors.NotFound:
                    pass
            else:
                possible_kills = [item for item in p if p not in killed_this_round]
                if len(possible_kills) > 0:
                    target = random.choice(possible_kills)
                else:
                    target = None
                if len(p) > 2:
                    action = random.choice(team_actions)
                else:
                    action = random.choice(team_actions_2)
                users = [u for u in p if u != target]
                if not action[1]:
                    user_actions.append((nice_join([u.name for u in p]), action[0]))
                elif not target:  # fix
                    user_actions.append(
                        (nice_join([u.name for u in p]), _("do nothing."))
                    )
                elif action[1] == "user":
                    user_actions.append(
                        (
                            nice_join([u.name for u in users]),
                            action[0].replace("USER", target.name),
                        )
                    )
                elif action[1] == "killall":
                    user_actions.append((nice_join([u.name for u in p]), action[0]))
                    killed_this_round.extend(p)
                else:
                    if action[1][0] == "kill":
                        user_actions.append(
                            (
                                nice_join([u.name for u in users]),
                                action[0].replace("USER", target.name),
                            )
                        )
                    elif action[1][0] == "killtogether":
                        user_actions.append(
                            (
                                nice_join([u.name for u in p]),
                                action[0].replace("USER", target.name),
                            )
                        )
                    killed_this_round.append(target)
        await asyncio.sleep(2)
        for p in killed_this_round:
            try:
                self.players.remove(p)
            except ValueError:
                pass
        embed = discord.Embed(
            title=f"Trial {self.round} of Olympus", color=discord.Color.green()
        )
        for u, a in user_actions:
            embed.add_field(name=u, value=a, inline=False)

        await self.ctx.send(embed=embed)
        self.round += 1

    async def send_cast(self):
        cast = copy.copy(self.players)
        cast = random.shuffle(cast)
        cast = list(chunks(cast, 2))
        self.cast = cast

        embed = discord.Embed(title="Champions of Olympus", color=discord.Color.blue())
        for i, team in enumerate(cast, start=1):
            if len(team) == 2:
                embed.add_field(
                    name=f"House #{i}",
                    value=f"{team[0].mention} {team[1].mention}",
                    inline=False,
                )
            else:
                embed.add_field(
                    name=f"House #{i}", value=f"{team[0].mention}", inline=False
                )

        await self.ctx.send(embed=embed)

    async def main(self):
        self.round = 1
        await self.send_cast()
        while len(self.players) > 1:
            await self.get_inputs()
            await asyncio.sleep(3)

        try:
            if len(self.players) == 1:
                embed = discord.Embed(
                    title="Olympian Trials Results", color=0x00FF00
                )  # Green color
                embed.description = _("The gods crown {winner} as victor!").format(
                    winner=self.players[0].mention
                )
                if self.reward > 0:
                    async with self.ctx.bot.pool.acquire() as conn:
                        await conn.execute(
                            'UPDATE profile SET "money"="money"+$1 WHERE "user"=$2;',
                            self.reward,
                            self.players[0].id,
                        )
                    embed.add_field(
                        name="Sponsored Reward",
                        value=f"${self.reward}",
                        inline=False,
                    )
                avatar_url = str(self.players[0].avatar) or "https://cdn.discordapp.com/embed/avatars/3.png"
                embed.set_thumbnail(url=avatar_url)
            else:
                embed = discord.Embed(
                    title="Olympian Trials Results", color=0xFF0000
                )  # Red color
                embed.description = _("The arena claims everyone!")
                embed.set_thumbnail(
                    url="https://64.media.tumblr.com/688393f27c7e1bf442a5a0edc81d41b5/ee1cd685d21520b0-f9/s500x750/4237c55e0f8b85cb943f6e7adb5562866a54ff2a.gif")

            await self.ctx.send(embed=embed)
        except Exception as e:
            await self.ctx.send(f"An error occurred: {e}")


class HungerGames(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.games = {}


    @commands.command(aliases=["hg"], brief=_("Enter the Olympian trials"))
    @locale_doc
    async def hungergames(self, ctx):
        _(
            """Starts the Olympian trials

            Players will be able to join by making an offering via the :shallow_pan_of_food: emoji.
            The game is controlled via both random actions and possibly chosen actions.
            Players may choose an action from an ephemeral channel prompt only they can see. If no action is chosen by the player, the bot chooses one for them.

            Not every player will get the opportunity to choose an action. Sometimes nobody gets to choose, so don't be discouraged. """
        )
        if self.games.get(ctx.channel.id):
            return await ctx.send(_("There is already a game in here!"))

        self.games[ctx.channel.id] = "forming"

        if ctx.channel.id == self.bot.config.game.official_tournament_channel_id:
            force_begin = asyncio.Event()
            view = BeginJoinView(
                Button(
                    style=ButtonStyle.primary,
                    label="Join the Olympian Trials!",
                    emoji="\U0001f958",
                ),
                message=_("You entered the Olympian Trials."),
                timeout=JOIN_TIMEOUT,
                bot=self.bot,
                host_id=ctx.author.id,
                force_begin=force_begin,
            )
            message = await ctx.send(
                f"{ctx.author.mention} has proclaimed a grand Olympian Trials melee!",
                view=view,
            )
            try:
                await asyncio.wait_for(force_begin.wait(), timeout=JOIN_TIMEOUT)
            except asyncio.TimeoutError:
                pass
            view.stop()
            for child in view.children:
                child.disabled = True
            try:
                await message.edit(view=view)
            except discord.HTTPException:
                pass
            players = list(view.joined)
        else:
            force_begin = asyncio.Event()
            view = BeginJoinView(
                Button(
                    style=ButtonStyle.primary,
                    label="Join the Olympian Trials!",
                    emoji="\U0001f958",
                ),
                message=_("You entered the Olympian Trials."),
                timeout=JOIN_TIMEOUT,
                bot=self.bot,
                host_id=ctx.author.id,
                force_begin=force_begin,
            )
            if not getattr(ctx, "auto_minigame", False):
                view.joined.add(ctx.author)
                text = _("{author} has started an Olympian Trials match!")
                message = await ctx.send(text.format(author=ctx.author.mention), view=view)
            else:
                message = await ctx.send(_("An Olympian Trials match is starting!"), view=view)
            try:
                await asyncio.wait_for(force_begin.wait(), timeout=JOIN_TIMEOUT)
            except asyncio.TimeoutError:
                pass
            view.stop()
            for child in view.children:
                child.disabled = True
            try:
                await message.edit(view=view)
            except discord.HTTPException:
                pass
            players = list(view.joined)

        if len(players) < 2:
            del self.games[ctx.channel.id]
            await self.bot.reset_cooldown(ctx)
            return await ctx.send(_("Not enough players joined..."))

        game = GameBase(ctx, players=players, reward=getattr(ctx, "scheduled_reward", 0))
        self.games[ctx.channel.id] = game
        try:
            await game.main()
        except Exception as e:
            await ctx.send(
                _("An error happened during the Olympian trial. Please try again!")
            )
            raise e
        finally:
            try:
                del self.games[ctx.channel.id]
            except KeyError:  # got stuck in between
                pass


async def setup(bot):
    await bot.add_cog(HungerGames(bot))
