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
import asyncio
import random
from discord.ext import commands
from utils.checks import has_char, user_is_gm
from utils.i18n import _, locale_doc

JOIN_TIMEOUT = 60 * 5
RUSSIAN_ROULETTE_GIF_URL = "https://i.ibb.co/kKn0zQs/ezgif-4-51fcaad25e.gif"


class RussianLobbyView(discord.ui.View):
    def __init__(
        self,
        cog,
        channel_id: int,
        host_id: int,
        force_begin: asyncio.Event,
        timeout: int,
    ):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.channel_id = channel_id
        self.host_id = host_id
        self.force_begin = force_begin

    @discord.ui.button(label="Join", style=discord.ButtonStyle.success)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        message = await self.cog.join_game(self.channel_id, interaction.user)
        await interaction.response.send_message(message, ephemeral=True)

    @discord.ui.button(label="Begin Now", style=discord.ButtonStyle.primary)
    async def begin(self, interaction: discord.Interaction, button: discord.ui.Button):
        is_gm_user = await user_is_gm(self.cog.bot, interaction.user)
        if interaction.user.id != self.host_id and not is_gm_user:
            return await interaction.response.send_message(
                "Only the host/GM can begin now.",
                ephemeral=True,
            )
        self.force_begin.set()
        await interaction.response.send_message("Beginning now...", ephemeral=True)


class Game:
    def __init__(self):
        self.participants = []
        self.is_game_running = False
        self.roundnum = 1
        self.bettotal = 0
        self.counter = 0
        self.betamount = 0
        self.joined_players = set()
        self.gamestarted = False
        self.single = False
        self.sponsor_reward = 0

class Russian(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.games = {}

    async def join_game(self, channel_id: int, user) -> str:
        game = self.games.get(channel_id)

        if not game or not game.gamestarted:
            return "There is no game running. You can't join now."

        if game.is_game_running:
            return "A game is already running. You can't join now."

        if user in game.joined_players:
            return f"{user.mention}, you have already joined this game."

        async with self.bot.pool.acquire() as conn:
            profile = await conn.fetchrow(
                'SELECT "money" FROM profile WHERE "user" = $1;',
                user.id
            )

        if not profile:
            return f"{user.mention}, you need a character to join this game."

        if game.bettotal > 0:
            if game.counter == 0:
                game.betamount = game.bettotal
                game.counter = 1

            if profile["money"] < game.betamount:
                return f"{user.mention}, you are too poor."

            async with self.bot.pool.acquire() as conn:
                await conn.execute(
                    'UPDATE profile SET "money"="money" - $1 WHERE "user"=$2;',
                    game.betamount, user.id
                )
            game.bettotal += game.betamount
            game.participants.append(user)
            game.joined_players.add(user)
            return f"{user.mention} has joined the game and paid a bet of {game.betamount}."

        game.participants.append(user)
        game.joined_players.add(user)
        return f"{user.mention} has joined the game!"

    @has_char()
    @commands.command()
    async def join(self, ctx):
        await ctx.send(await self.join_game(ctx.channel.id, ctx.author))

    @has_char()
    @commands.command(aliases=["rr", "gungame"], brief=_("Play Russian Roulette"))
    @locale_doc
    async def russianroulette(self, ctx, bet: int = 0):
        _(
            """`<amount>` - the amount of money to bid

            Start a game of Russian Roulette.

            Players take turns pulling the trigger while pointing the gun at their own head or another player's head, with the hope of avoiding the live round"""
        )
        game = self.games.get(ctx.channel.id)

        if game:
            await ctx.send("A game is already running in this channel.")
            return

        game = Game()
        game.sponsor_reward = max(0, int(getattr(ctx, "scheduled_reward", 0)))
        self.games[ctx.channel.id] = game
        force_begin = asyncio.Event()
        view = RussianLobbyView(self, ctx.channel.id, ctx.author.id, force_begin, JOIN_TIMEOUT)

        if bet < 0:
            await ctx.send(f"{ctx.author.mention} your bet must be above 0!")
            del self.games[ctx.channel.id]
            return

        if bet > 0:
            # Check the player's balance
            async with self.bot.pool.acquire() as conn:
                user_balance = await conn.fetchval(
                    'SELECT "money" FROM profile WHERE "user" = $1;',
                    ctx.author.id
                )

            if user_balance < bet:
                await ctx.send(f"{ctx.author.mention}, you don't have enough money to cover the bet of **${bet}**.")
                del self.games[ctx.channel.id]
                return
            else:
                async with self.bot.pool.acquire() as conn:
                    await conn.execute(
                        'UPDATE profile SET "money"="money" - $1 WHERE "user"=$2;',
                        bet, ctx.author.id
                    )
                game.bettotal = bet
                game.winnings = game.bettotal
                lobby_message = await ctx.send(
                    f"Russian Roulette game has started with an entry fee of **${bet}!** Wait up to 5 minutes for players to join with the button or **$join**.",
                    view=view,
                )
                game.gamestarted = True
                if not getattr(ctx, "auto_minigame", False):
                    game.joined_players.add(ctx.author)
        else:
            reward_line = (
                f"\nSponsored reward: **${game.sponsor_reward}**"
                if game.sponsor_reward > 0 else ""
            )
            lobby_message = await ctx.send(
                f"**Russian Roulette game has started!** Players have up to 5 minutes to join with the button or **$join**.{reward_line}",
                view=view,
            )
            game.gamestarted = True
            if not getattr(ctx, "auto_minigame", False):
                game.joined_players.add(ctx.author)

        if not getattr(ctx, "auto_minigame", False):
            game.participants.append(ctx.author)
        try:
            await asyncio.wait_for(force_begin.wait(), timeout=JOIN_TIMEOUT)
        except asyncio.TimeoutError:
            pass
        view.stop()
        for child in view.children:
            child.disabled = True
        try:
            await lobby_message.edit(view=view)
        except discord.HTTPException:
            pass

        if len(game.participants) < 2:
            await ctx.send("Not enough players to start the game.")
            if bet > 0:
                async with self.bot.pool.acquire() as conn:
                    await conn.execute(
                        'UPDATE profile SET "money"="money"+$1 WHERE "user"=$2;',
                        bet, ctx.author.id
                    )
            del self.games[ctx.channel.id]
            return

        random.shuffle(game.participants)
        remaining = len(game.participants)
        await ctx.send(f"There are {remaining} players!")
        game.is_game_running = True  # Set the flag
        chambers = [False] * 5 + [True]
        random.shuffle(chambers)
        await self.announce_round(ctx, game)

        try:
            while len(game.participants) > 1:
                players_to_remove = []
                player_eliminated = False
                other_player = None

                for player in game.participants.copy():
                    await asyncio.sleep(5)
                    await ctx.send(
                        f"It's {player.mention}'s turn! They pick up the gun and turn it towards their head and slowly pull the trigger...")
                    await asyncio.sleep(4)  # Simulate suspense

                    chamber_drawn = chambers.pop(0)

                    if chamber_drawn:
                        await asyncio.sleep(2)  # Simulate suspense
                        if len(game.participants) == 2 and random.random() < 0.25:
                            other_player = [p for p in game.participants if p != player][0]
                            embed = discord.Embed(
                                title="BANG!",
                                description=f"{other_player.mention} has been shot by {player.mention}!",
                                color=discord.Color.red()
                            )
                            embed.set_image(url=RUSSIAN_ROULETTE_GIF_URL)
                            await asyncio.sleep(3)  # Simulate suspense
                            await ctx.send(embed=embed)
                            players_to_remove.append(other_player)
                            game.participants.remove(other_player)
                            player_eliminated = True
                            shotother = 1
                        else:
                            embed = discord.Embed(
                                title="BANG!",
                                description=f"{player.mention} has shot themselves in the face!",
                                color=discord.Color.red()
                            )
                            embed.set_image(url=RUSSIAN_ROULETTE_GIF_URL)
                            await asyncio.sleep(3)  # Simulate suspense
                            await ctx.send(embed=embed)
                            players_to_remove.append(player)
                            game.participants.remove(player)
                            player_eliminated = True
                            shotother = 0

                    else:
                        await asyncio.sleep(3)  # Simulate suspense
                        embed = discord.Embed(
                            title="The Gun Clicks!",
                            description=f"{player.mention} has survived this round and passes the gun to the next player!",
                            color=discord.Color.green()
                        )
                        await ctx.send(embed=embed)
                        await asyncio.sleep(3)  # Simulate suspense

                    if player_eliminated:
                        if shotother == 1:
                            if other_player is not None:
                                await ctx.send(f"Round over! {other_player.mention} was killed!")
                        else:
                            await ctx.send(f"Round over! {player.mention} was killed!")
                        remaining = len(game.participants)
                        if remaining > 1:
                            await ctx.send(f"There are {remaining} player(s) remaining")

                        if len(game.participants) == 1:
                            winner = game.participants[0]
                            if bet > 0:
                                async with self.bot.pool.acquire() as conn:
                                    await conn.execute(
                                        'UPDATE profile SET "money"="money"+$1 WHERE "user"=$2;',
                                        game.bettotal, winner.id
                                    )
                                winnings = game.bettotal - game.winnings
                                await ctx.send(
                                    f"Congratulations {winner.mention}! You are the last one standing and won **${winnings}**."
                                )
                            elif game.sponsor_reward > 0:
                                async with self.bot.pool.acquire() as conn:
                                    await conn.execute(
                                        'UPDATE profile SET "money"="money"+$1 WHERE "user"=$2;',
                                        game.sponsor_reward,
                                        winner.id,
                                    )
                                await ctx.send(
                                    f"Congratulations {winner.mention}! You are the last one standing and won the sponsored reward of **${game.sponsor_reward}**."
                                )
                            else:
                                await ctx.send(
                                    f"Congratulations {winner.mention}! You are the last one standing. **Game over!**"
                                )
                            del self.games[ctx.channel.id]
                            return
                        else:
                            game.roundnum += 1
                            await self.announce_round(ctx, game)
                            chambers = [False] * 5 + [True]
                            random.shuffle(chambers)
                            player_eliminated = False

                if not game.participants:
                    break  # If all players are eliminated
        except Exception as e:
            await ctx.send(f"An error occurred: {e}")
        finally:
            if ctx.channel.id in self.games:
                del self.games[ctx.channel.id]

    async def announce_round(self, ctx, game):
        embed = discord.Embed(
            title=f"Round {game.roundnum}",
            description="Surviving players automatically move to the next round. Round will start in 5 seconds..",
            color=discord.Color.green()
        )
        embed.set_image(url=RUSSIAN_ROULETTE_GIF_URL)
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(Russian(bot))
