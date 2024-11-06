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
import asyncio
import datetime
import json
import requests
from io import BytesIO

from discord.ext.commands import CommandError

from classes.classes import from_string as class_from_string

from moviepy.editor import AudioFileClip, ImageClip
import pytesseract
import os
import platform
import re
import statistics
import sys
import time

from collections import defaultdict, deque
from functools import partial

import aiohttp
import io

from PIL import Image, ImageEnhance, ImageOps, ImageFilter
from openai import AsyncOpenAI

import discord
import distro
import humanize
import pkg_resources as pkg
import psutil
import requests

from discord.ext import commands

from classes.converters import ImageFormat, ImageUrl
from cogs.help import chunks
from cogs.shard_communication import next_day_cooldown
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


class Miscellaneous(commands.Cog):
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

    async def generate_and_send_speech(self, interaction, text, voice):
        url = "https://api.openai.com/v1/audio/speech"
        OPENAI_KEY = self.bot.config.external.openai

        headers = {
            "Authorization": f"Bearer {OPENAI_KEY}",
            "Content-Type": "application/json"
        }
        data = {
            "model": "tts-1-hd",
            "input": text,
            "voice": voice.lower()
        }

        response = requests.post(url, headers=headers, json=data)
        if response.status_code == 200:
            audio_bytes = BytesIO(response.content)
            audio_file_path = 'output_audio.mp3'
            with open(audio_file_path, 'wb') as f:
                f.write(audio_bytes.getbuffer())

            audio_clip = AudioFileClip(audio_file_path)
            image_clip = ImageClip('black_image.png', duration=audio_clip.duration)
            video = image_clip.set_audio(audio_clip)
            video_file_path = 'output_video.mp4'
            video.write_videofile(video_file_path, codec="libx264", fps=24)

            await interaction.followup.send(file=discord.File(video_file_path))
        else:
            await interaction.followup.send(f"Error: {response.status_code} - {response.text}")


    @has_char()
    @user_cooldown(1)
    @commands.command()
    @locale_doc
    async def all(self, ctx):
        _(
            """Automatically invokes several daily commands for you.

        This command will attempt to run several of your daily or periodic commands such as `vote`, `daily`, `donatordaily`, `steal`, `date`, `pray`, and `familyevent` in one go, if they are not on cooldown.

        Usage:
          `$all`

        Note:
        - Commands that are on cooldown will be skipped, and you'll be notified when you can use them again.
        - If you are a Thief class, it will attempt to use `steal` as well.
        - This command itself has a cooldown of 1 second."""
        )

        try:
            # Define the commands and their respective cooldowns (in seconds)
            cooldowns = {
                'vote': 12 * 3600,  # 12 hours
                'daily': self.time_until_midnight(),  # Daily reset
                'donatordaily': self.time_until_midnight(),  # Daily reset
                'steal': 60 * 60,  # 1 hour
                'date': 12 * 3600,  # 12 hours
                'pray': self.time_until_midnight(),  # Daily reset
                'familyevent': 30 * 60,  # 30 minutes
            }

            character_data = await ctx.bot.pool.fetchrow(
                'SELECT * FROM profile WHERE "user"=$1;', ctx.author.id
            )

            if character_data["tier"] < 1:
                await ctx.send("You do not have access to this command.")
                return

            tasks = []  # To gather tasks for concurrent execution

            for command_name, cooldown_duration in cooldowns.items():
                command = self.bot.get_command(command_name)

                if command is not None:
                    command_ttl = await ctx.bot.redis.execute_command(
                        "TTL", f"cd:{ctx.author.id}:{command.qualified_name}"
                    )

                    if command_ttl == -2:  # No cooldown exists
                        if command_name == 'steal':
                            # Check if the user is a Thief
                            user_classes = [class_from_string(c) for c in character_data["class"]]
                            if any(type(c).__name__ == 'Thief' for c in user_classes):
                                tasks.append(ctx.invoke(command))
                                await ctx.bot.redis.execute_command(
                                    "SET", f"cd:{ctx.author.id}:{command.qualified_name}",
                                    command.qualified_name,
                                    "EX", cooldown_duration
                                )
                            else:
                                await ctx.send(_("You need to be a Thief to use the steal command."))
                        else:
                            # Invoke the command and set the new cooldown
                            tasks.append(ctx.invoke(command))
                            await ctx.bot.redis.execute_command(
                                "SET", f"cd:{ctx.author.id}:{command.qualified_name}",
                                command.qualified_name,
                                "EX", cooldown_duration
                            )
                    else:
                        # Inform the user that the command is on cooldown
                        await ctx.send(
                            f"{command_name} is on cooldown. Try again in {self.format_time(command_ttl)}."
                        )

            # Execute all the tasks concurrently
            if tasks:  # Only gather if there are tasks
                await asyncio.gather(*tasks)

        except Exception as e:
            await ctx.send(str(e))

    def format_time(self, seconds):
        """Convert seconds to HH:MM:SS format."""
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{int(hours):02}:{int(minutes):02}:{int(seconds):02}"

    def time_until_midnight(self):
        """Calculate the number of seconds until the next midnight UTC."""
        return int(86400 - (time.time() % 86400))

    
    @has_char()
    @next_day_cooldown()
    @commands.command(brief=_("Get your daily reward"))
    @locale_doc
    async def daily(self, ctx):
        _(
            """Get your daily reward. Depending on your streak, you will gain better rewards.

            After ten days, your rewards will reset. Day 11 and day 1 have the same rewards.
            The rewards will either be money (2/3 chance) or crates (1/3 chance).

            The possible rewards are:

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

            (This command has a cooldown until 12am UTC.)"""
        )

        try:
            streak = await self.bot.redis.execute_command(
                "INCR", f"idle:daily:{ctx.author.id}"
            )
            await self.bot.redis.execute_command(
                "EXPIRE", f"idle:daily:{ctx.author.id}", 48 * 60 * 60
            )  # 48h: after 2 days, they missed it
            money = 2 ** ((streak + 9) % 10) * 50
            # Either money or crates
            if random.randint(0, 2) > 0:
                money = 2 ** ((streak + 9) % 10) * 50
                # Silver = 1.5x
                if await user_is_patron(self.bot, ctx.author, "silver"):
                    money = round(money * 1.5)

                result = await self.bot.pool.fetchval('SELECT tier FROM profile WHERE "user" = $1;', ctx.author.id)

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
                        f'UPDATE profile SET "crates_{type_}"="crates_{type_}"+$1 WHERE'
                        ' "user"=$2;',
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
                ).format(txt=txt, money=money, streak=streak, prefix=ctx.clean_prefix)
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


    @is_gm()
    @commands.command(hidden=True, name="challenges")
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
    @commands.command(brief="Hug someone with a cute GIF!")
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
    @commands.command(brief="Kiss someone with a cute GIF!")
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
    @commands.command(brief="Pat someone with a cute GIF!")
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
    @commands.command(brief="Slap someone with a GIF!")
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
    @commands.command(brief="Give someone a high five!")
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
    @commands.command(brief="Wave at someone with a GIF!")
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
    @commands.command(brief="Cuddle someone with a cute GIF!")
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
    @commands.command(brief="Poke someone gently with a cute GIF!")
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
    @commands.command(brief="Bite someone gently with a playful GIF!")
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
    @commands.command(brief="Tickle someone with a GIF!")
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
    @commands.command(brief="Nuzzle someone affectionately!")
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
    @commands.command(brief="Lick someone!")
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
    @commands.command(brief="Punch someone with a GIF!")
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
    @commands.command(brief=_("Roll"))
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
    @commands.command(brief=_("View your current streak"))
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

    
    @commands.command(aliases=["donate"], brief=_("Support the bot financially"))
    @locale_doc
    async def patreon(self, ctx):
        _(
            """View the Patreon page of the bot. The different tiers will grant different rewards.
            View `{prefix}help module Patreon` to find the different commands.

            Thank you for supporting Fable RPG!"""
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

<https://patreon.com/FableRPG>"""
            ).format(guild_count=guild_count)
        )

    
    @commands.command(
        aliases=["license"], brief=_("Shows the source code and license.")
    )
    @locale_doc
    async def source(self, ctx):
        _(
            """Shows Idles GitLab page and license alongside our own source as required by AGPLv3 Licensing."""
        )
        await ctx.send("IdleRPG - AGPLv3+\nhttps://git.travitia.xyz/Kenvyra/IdleRPG")

        await ctx.send("Fable - AGPLv3+\nhttps://github.com/prototypeX37/FableRPG-")

    
    @commands.command(brief=_("Invite the bot to your server."))
    @locale_doc
    async def invite(self, ctx):
        _(
            """Invite the bot to your server.

            Use this https://discord.com/api/oauth2/authorize?client_id=1136590782183264308&permissions
            =8945276537921&scope=bot"""
        )
        await ctx.send(
            _(
                "You are running version **{version}** by The Fable"
                "Developers.\nInvite me! https://discord.com/api/oauth2/authorize?client_id=1136590782183264308"
                "&permissions=8945276537921&scope=bot"
            ).format(version=self.bot.version)
        )


    def is_gm_predicate(self):
        """Utility function to identify the is_gm check."""

        async def predicate(ctx):
            raise CommandError("is_gm check")

        return predicate

    
    @commands.command()
    @locale_doc
    async def allcommands(self, ctx):
        _("""Displays all available commands categorized by their cogs, excluding commands with the @is_gm() decorator.""")
        # Assuming static prefix '$'
        prefix = '$'
        try:
            await ctx.send("Please wait while I gather that information for you..")
            # Initialize a dictionary to store commands by cog
            cog_commands = {}

            # Iterate over all commands in the bot
            for cmd in self.bot.commands:
                if not cmd.hidden and all(
                        pred.__name__ != "is_gm" for pred in cmd.checks):  # Exclude commands with is_gm check
                    # Get the cog name or categorize as 'No Category' if not in a cog
                    cog_name = cmd.cog_name or "No Category"
                    if cog_name == "GameMaster":
                        continue

                    if cog_name not in cog_commands:
                        cog_commands[cog_name] = []

                    # Add the command to the appropriate cog category
                    cog_commands[cog_name].append(f"{prefix}{cmd.name}")

            # Define the maximum length of a message accounting for markdown characters
            max_length = 2000 - len("```\n```")  # Deduct the length of markdown characters used for formatting

            # Iterate over each cog and its commands, and send them in chunks
            for cog, commands in cog_commands.items():
                chunk = f"**{cog}**\n"  # Start with the cog name

                for command in commands:
                    # Check if adding this command will exceed the max length
                    if len(chunk) + len(command) + 1 > max_length:  # +1 for newline character
                        # Send the current chunk and reset it
                        await ctx.send(f"```\n{chunk}\n```")
                        chunk = f"**{cog}**\n"  # Reset with the cog name

                    # Add the command to the chunk
                    chunk += f"{command}\n"

                # Send any remaining commands in the last chunk
                if chunk.strip():
                    await ctx.send(f"```\n{chunk}\n```")

        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")

    
    @commands.command(brief=_("Shows statistics about the bot"))
    @locale_doc
    async def stats(self, ctx):
        _(
            """Show some stats about the bot, ranging from hard- and software statistics, over performance to ingame stats."""
        )
        async with self.bot.pool.acquire() as conn:
            characters = await conn.fetchval("SELECT COUNT(*) FROM profile;")
            items = await conn.fetchval("SELECT COUNT(*) FROM allitems;")
            pg_version = conn.get_server_version()
        temps = psutil.sensors_temperatures()
        temps = temps[list(temps.keys())[0]]
        cpu_temp = statistics.mean(x.current for x in temps)
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
        meminfo = psutil.virtual_memory()
        cpu_freq = psutil.cpu_freq()
        cpu_name = await get_cpu_name()
        compiler = re.search(r".*\[(.*)\]", sys.version)[1]

        embed = discord.Embed(
            title=_("FableRPG Statistics"),
            colour=0xB8BBFF,
            url=self.bot.BASE_URL,
            description=_(
                "Official Support Server Invite: Coming Soon"
            ),
        )
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.set_footer(
            text=f"IdleRPG {self.bot.version} | By {owner}",
            icon_url=self.bot.user.display_avatar.url,
        )
        embed.add_field(
            name=_("Hosting Statistics"),
            value=_(
                """\
CPU: **AMD Ryzen Threadripper PRO 7995WX**
CPU Usage: **{cpu}%**, **96** cores/**192** threads @ **{freq}** GHz
RAM Usage: **{ram}%** (Total: **127.1 GB**)
CPU Temperature: **{cpu_temp}°C**
Python Version **{python}** 
discord.py Version **{dpy}**
Compiler: **{compiler}**
Operating System: **{osname} {osversion}**
Kernel Version: **{kernel}**
PostgreSQL Version: **{pg_version}**
Redis Version: **{redis_version}**"""
            ).format(
                cpu_name=cpu_name,
                cpu=psutil.cpu_percent(),
                cores=psutil.cpu_count(logical=False),
                threads=psutil.cpu_count(),
                cpu_temp=round(cpu_temp, 2),
                freq=cpu_freq.max / 1000
                if cpu_freq.max
                else round(cpu_freq.current / 1000, 2),
                ram=meminfo.percent,
                total_ram=humanize.naturalsize(meminfo.total),
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

    
    @commands.command(brief=_("View the uptime"))
    @locale_doc
    async def uptime(self, ctx):
        _("""Shows how long the bot has been connected to Discord.""")
        await ctx.send(
            _("I am online for **{time}**.").format(
                time=str(self.bot.uptime).split(".")[0]
            )
        )

    
    @commands.command()
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

    
    @commands.command()
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

        if ctx.author.id != 295173706496475136:
            if ctx.author.id != 698612238549778493:

                if ctx.guild.id != 969741725931298857:
                    return
        creditss = ctx.character_data["imagecredits"]
        freecredits = ctx.character_data["freeimage"]
        # await ctx.send(f"{credits}")

        if ctx.author.id == 295173706496475136:
            await self.bot.reset_cooldown(ctx)

        if ctx.author.id == 598004694060892183:
            await self.bot.reset_cooldown(ctx)

        if ctx.author.id == 749263133620568084:
            await self.bot.reset_cooldown(ctx)



        if freecredits <= 0:

            if creditss <= 0.03:
                return await ctx.send(f"You have used up all free images for today. Additional images cost **$0.04**.")

        try:
            if ctx.author.id != 295173706496475136:
                if ctx.author.id != 598004694060892183:
                    if len(prompt) > 120:
                        return await ctx.send("The prompt cannot exceed 120 characters.")
            await ctx.send("Generating image, please wait. (This can take up to 2 minutes.)")
            client = AsyncOpenAI(api_key="")
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

    
    @commands.command()
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

        if ctx.author.id == 295173706496475136:
            await self.bot.reset_cooldown(ctx)

        if ctx.author.id != 598004694060892183:
            await self.bot.reset_cooldown(ctx)

        if creditss <= 0.11:
            return await ctx.send(f"You do not have enough credits for this model. Additional images cost **$0.12**.")

        try:
            if ctx.author.id != 295173706496475136:
                if ctx.author.id != 698612238549778493:
                    if len(prompt) > 120:
                        return await ctx.send("The prompt cannot exceed 120 characters.")
            await ctx.send("Generating HD image, please wait. (This can take up to 2 minutes.)")
            client = AsyncOpenAI(api_key="")
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

    
    @commands.command(name='talk', help='Ask ChatGPT a question!')
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

        if ctx.author.id != 295173706496475136:
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

    
    @commands.command()
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

    
    @commands.command()
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

    
    @commands.command(name='wipe', help='Clear your conversation history with the bot.')
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

    
    @commands.command(
        aliases=["pages", "about"], brief=_("Info about the bot and related sites")
    )
    @locale_doc
    async def web(self, ctx):
        _("""About the bot and our websites.""")
        await ctx.send(
            _(
                # xgettext: no-python-format
                """\
**IdleRPG** is Discord's most advanced medieval RPG bot.
We aim to provide the perfect experience at RPG in Discord with minimum effort for the user.

We are not collecting any data apart from your character information and our transaction logs.
The bot is 100% free to use and open source.
This bot is developed by people who love to code for a good cause and improving your gameplay experience.

**Links**
<https://git.travitia.xyz/Kenvyra/IdleRPG> - Source Code
<https://git.travitia.xyz> - GitLab (Public)
<https://idlerpg.xyz> - Bot Website
<https://wiki.idlerpg.xyz> - IdleRPG wiki
<https://travitia.xyz> - IdleRPG's next major upgrade
<https://idlerpg.xyz> - Our forums
<https://public-api.travitia.xyz> - Our public API
<https://cloud.idlerpg.xyz> - VPS hosting by IdleRPG
<https://github.com/Kenvyra> - Other IdleRPG related code
<https://discord.com/terms> - Discord's ToS
<https://www.ncpgambling.org/help-treatment/national-helpline-1-800-522-4700/> - Gambling Helpline"""
            )
        )

    
    @commands.command(brief=_("Show the rules again"))
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

IdleRPG is a global bot, your characters are valid everywhere"""
            )
        )


async def setup(bot):
    await bot.add_cog(Miscellaneous(bot))
