
import asyncio
import datetime
import re
import traceback

from discord import Embed, File
from decimal import Decimal, ROUND_HALF_UP, getcontext
import utils.misc as rpgtools
import discord

from discord.enums import ButtonStyle
import random as randomm
from discord.ext import commands, tasks
from discord.ui.button import Button
from discord.interactions import Interaction
from discord.ui import Button, View

from classes.classes import Raider
from classes.classes import from_string as class_from_string
from classes.converters import IntGreaterThan
from cogs.shard_communication import user_on_cooldown as user_cooldown
from utils import random
from utils.checks import AlreadyRaiding, has_char, is_gm, is_god
from utils.i18n import _, locale_doc
from utils.joins import JoinView

class MystMaze(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.raid_active = False

    async def set_raid_timer(self):
        self.raid_active = True

    async def clear_raid_timer(self):
        self.raid_active = False

    @is_god()
    @commands.command(hidden=True, brief=_("Starts Mystical Labyrinth"))
    async def spawnmaze(self, ctx):
        """[Hecate only] Starts a Mystical Labyrinth."""
        await self.set_raid_timer()

        try:
            view = JoinView(
                Button(style=ButtonStyle.primary, label="Join the Labyrinth!"),
                message=_("You joined the Labyrinth."),
                timeout=60 * 15,
            )

            channels = [
                self.bot.get_channel(1415389846834516172),  # This is the current channel where the command was invoked
             ]

            channel1 = self.bot.get_channel(1415389846834516172)
            role_id1 = 1415388751064076318

            if channel1:
                role1 = ctx.guild.get_role(role_id1)
                if role1:
                    await channel1.send(content=f"{role1.mention}", allowed_mentions=discord.AllowedMentions(roles=True))

            
            # Message content, organized for better formatting
            message_intro = """
            Seekers of knowledge and secrets,
            Enter the Labyrinth at your own peril.
            Perceive the divine signs of thy guide,
            Retrieve the artifact, or meet thyn demise.

            """

            message_trial = """
            **__Pursuers of knowledge,stand before the threshold.__**
            The Threshold closes in 15 minutes
            """

            message_note = """
            **Only followers of Hecate may join.**
            """

            # Create the embed with structured fields
            embed = discord.Embed(
                title="Champions of Truth ",
                color=discord.Color.purple()
            )
            embed.add_field(name="Hecate's wisdom", value=message_intro, inline=False)
            embed.add_field(name="Labyrinth Information", value=message_trial, inline=False)
            embed.add_field(name="Notice", value=message_note, inline=False)
            embed.set_footer(text="Prepare yourself the Labyrinth awaits.")
            embed.timestamp = discord.utils.utcnow()

            # Attach the file (image)
            embed.set_image(url="https://i.imgur.com/zoK4X4p.jpeg")

            # Updated helper function to send to both channels and handle file closing issue
            async def send_to_channels(embed=None, content=None, view=None):
                """Helper function to send a message to all channels."""
                for channel in channels:
                    if channel is not None:  # Ensure the channel is valid
                        try:
                            await channel.send(embed=embed, content=content, view=view)
                        except Exception as e:
                            await ctx.send(f"Failed to send message to {channel.name}: {str(e)}")
                    else:
                        await ctx.send("One of the channels could not be found.")

            # Call this function with file_path
            await send_to_channels(embed=embed, content=None, view=view)

            # Sending the embed with the file to the channels

            if not self.bot.config.bot.is_beta:
                await asyncio.sleep(300)
                await send_to_channels(content="**Astraea and her Ouroboros will be visible in 10 minutes**")
                await asyncio.sleep(300)
                await send_to_channels(content="**Astraea and her Ouroboros will be visible in 5 minutes**")
                await asyncio.sleep(180)
                await send_to_channels(content="**Astraea and her Ouroboros will be visible in 2 minutes**")
                await asyncio.sleep(60)
                await send_to_channels(content="**Astraea and her Ouroboros will be visible in 1 minute**")
                await asyncio.sleep(30)
                await send_to_channels(content="**Astraea and her Ouroboros will be visible in 30 seconds**")
                await asyncio.sleep(20)
                await send_to_channels(content="**Astraea and her Ouroboros will be visible in 10 seconds**")
            else:
                await asyncio.sleep(300)
                await send_to_channels(content="**The Labyrinth’s threshold closes in 10 minutes**")
                await asyncio.sleep(300)
                await send_to_channels(content="**The Labyrinth’s threshold closes in 5  minutes**")
                await asyncio.sleep(180)
                await send_to_channels(content="**The Labyrinth’s threshold closes in 2 minutes**")
                await asyncio.sleep(60)
                await send_to_channels(content="**The Labyrinth’s threshold closes in 1 minute**")
                await asyncio.sleep(30)
                await send_to_channels(content="**The Labyrinth’s threshold closes in 30 seconds**")
                await asyncio.sleep(20)
                await send_to_channels(content="**The Labyrinth’s threshold closes in 10 seconds**")

            view.stop()

            await send_to_channels(content="**Mystical Labyrinth will commence! Fetch participant data... Hang on!**")

            async with self.bot.pool.acquire() as conn:
                raid = []
                HowMany = 0
                for u in view.joined:
                    if (
                            not (
                                    profile := await conn.fetchrow(
                                        'SELECT * FROM profile WHERE "user"=$1;', u.id
                                    )
                            )
                            or profile["god"] != "Hecate"
                    ):
                        continue
                    HowMany = HowMany + 1
                    raid.append(u)

            await send_to_channels(content="**Done getting data!**")
            await send_to_channels(content=f"**{HowMany} followers joined!**")

            while len(raid) > 1:
                time = random.choice(["day", "night"])
                if time == "day":
                    em = discord.Embed(
                        title="The torches brighten,illuminating the way",
                        description="The presence of Hecate is obvious, her signs linger in thy sight, "
                                    "her guidance leading to better paths.",
                        colour=0x71368a,
                    )
                else:
                    em = discord.Embed(
                        title="The torches diminish, the shadows bring uncertainty",
                        description="Darkness covers the halls, each step brings more and more unknown, "
                                    "do thy eyes deceive you?",
                        colour=0x71368a,
                    )
                em.set_thumbnail(url="https://i.imgur.com/eToOkob.png")
                await send_to_channels(embed=em)
                await asyncio.sleep(5)
                target = random.choice(raid)
                if time == "day":
                    event = random.choice(
                        [
                            {
                                "text": "You come before a crossroads",
                                "win": 80,
                                "win_text": "You take note of a torch hanging overhead in one of the directions."
                                            "You follow the sign.",
                                "lose_text": "Despite your best efforts to look, you are unable to notice the signs."
                                             "You lose your way.",
                            },
                            {
                                "text": "The darkness envelopes you, you hear shuffling in the dark around you and feel something with fur.",
                                "win": 50,
                                "win_text": "You reach for the loyal guide who shows the way forward." ,
                                "lose_text": "You jump back in fright at the creature near you running in a random direction.",
                            },
                            {
                                "text": "Getting to a fork in the road words hang before you “The Past is a place of…",
                                "win": 60,
                                "win_text": "You take the path that forms the words 'Reference' as the past should always be referenced." ,
                                "lose_text": "You take the path that forms the words 'Residence' as you end up back on a path you had already taken before.",
                             },
                        ]
                    )
                else:
                    event = random.choice(
                        [
                            {
                                "text": "Before you are two paths, the wall in front of you read’s “The future is something you…",
                                "win": 50,
                                "win_text": "You look at the options before you and choose “Create” as the future can not truly come unless you aid to create it yourself." ,
                                "lose_text": "You see one of the sides says “Enter” you decide to follow its advice, but now you don’t know where to go from here.",
                            },
                            {
                                "text": "As you are walking you end up falling down a trap door, at the bottom of the drop there is an engraving of words before you “Yesterday’s the past, tomorrow is the future, but today is a…”",
                                "win": 45,
                                "win_text": "Gift, as the words leave your mouth a rope descends from above.",
                                "lose_text": "Mystery, as the words leave your mouth you fall further and further into the Labyrinth.",
                            },
                            {
                                "text": "Before you is a large drop with only a thin ledge to try to use to pass.",
                                "win": 20,
                                "win_text": "You notice twin snake’s eyes on you, watching how they slither above "
                                            "and on the ledge you notice places to put your hands and feet to safely cross",
                                "lose_text": "Facing the ledge you try to go head first at it, but end up going head first "
                                             "down the drop instead.",
                            },
                            {
                                "text": "A door stands in your way, a lock keeps you from entering. Keys, items, and weapons lay scattered around the door.",
                                "win": 50,
                                "win_text": "A dusty, but elegant onyx dagger catches your eye, you place it in the lock and it opens.",
                                "lose_text": "You pick up an axe and attempt to break the lock, in doing so you fall into a trap.",
                            },
                            {
                                "text": "You are no longer sure where to go and you come across a crossroads.",
                                "win": 50,
                                "win_text": "ou notice the subtle markings of a serpent’s body going in the right direction and follow it.",
                                "lose_text": "You pick a random direction as you do the calculations on if it's a good path, but you aren’t the best at math…",
                            },
                            {
                                "text": "You see a list of commands that say to follow them.",
                                "win": 50,
                                "win_text": "Noticing that some of the commands have twin torches next to them you only follow those and get to the next area.",
                                "lose_text": "You follow all the commands blindly, finding yourself very lost.",
                            },
                            {
                                "text": "A howl rings out through the corridors.",
                                "win": 50,
                                "win_text": "You follow the howl in the right direction, leading you back on path.",
                                "lose_text": "You turn tail and go in the opposite direction hoping to avoid a beast, but end up lost.",
                            },
                            {
                                "text": "There are three goblet’s filled with varying amounts of liquid, the archway a head reads “One chance, all must be equal, but you may not touch the goblets”",
                                "win": 50,
                                "win_text": "ou take note of the pedestal that the goblets reside on, there is a serpent spiraling around it, you choose to use your hand to follow it in turn knocking the pedestal over emptying all the goblets. ",
                                "lose_text": "You pace around the room trying to think of something when your robe catches one of the goblets knocking it over, making the room begin to cave in",
                            },
                            {
                                "text": "ou reach a dead end.",
                                "win": 50,
                                "win_text": "Investigating the wall reveals a slight engraving of a torch, you bring one nearby closer, as the light hits the direct area the path forward opens.",
                                "lose_text": "You do not see a way forward, you turn back.",
                            }
                        ]
                    )
                does_win = event["win"] >= random.randint(1, 100)
                if does_win:
                    text = event["win_text"]
                else:
                    text = event["lose_text"]
                    raid.remove(target)
                em = discord.Embed(
                    title=event["text"],
                    description=text,
                    colour=0x71368a,
                )
                em.set_author(name=f"{target}", icon_url=target.display_avatar.url)
                em.set_footer(text=f"{len(raid)} followers remain")
                em.set_thumbnail(url="https://i.imgur.com/zoK4X4p.jpeg")
                await send_to_channels(embed=em)
                await asyncio.sleep(5)

            winner = raid[0]
            async with self.bot.pool.acquire() as conn:
                # Fetch the luck value for the specified user (winner)
                luck_query = await conn.fetchval(
                    'SELECT luck FROM profile WHERE "user" = $1;',
                    winner.id,
                )

            # Convert luck_query to float
            luck_query_float = float(luck_query)

            # Define gods with their boundaries
            gods = {
                "Hecate": {"boundary_low": 0.8, "boundary_high": 1.2},
                "Thanatos": {"boundary_low": 0.4, "boundary_high": 1.6},
                "Morpheus": {"boundary_low": 0, "boundary_high": 2.0},
                "Apollo": {"boundary_low": 0, "boundary_high": 2.0},
            }

            # Replace 'selected_god' with the actual selected god name (e.g., "Astraea")
            selected_god = "Hecate"  # Example, replace dynamically
            god_data = gods.get(selected_god)

            if not god_data:
                raise ValueError(f"God {selected_god} not found.")

            boundary_low = god_data["boundary_low"]
            boundary_high = god_data["boundary_high"]

            # Normalize the user's luck value
            normalized_luck = (luck_query_float - boundary_low) / (boundary_high - boundary_low)
            normalized_luck = max(0.0, min(1.0, normalized_luck))  # Clamp between 0.0 and 1.0

            # Scale the divine weight
            weightdivine = 0.20 + (0.20 * normalized_luck)  # Example scaling factor
            rounded_weightdivine = round(weightdivine, 3)

            # Define weights for crate selection
            options = ['legendary', 'fortune', 'divine']
            weights = [0.40, 0.40, rounded_weightdivine]

            # Select a crate based on weights
            crate = randomm.choices(options, weights=weights)[0]

            # Build an embed for the final reward
            final_embed = discord.Embed(
                title="The Labyrinth Collapses",
                description=(
                    f"Before them, atop an ancient runic pedestal, rests the **{crate} crate**. "
                    f"The dust that once covered it is brushed away by gentle hands.\n\n"
                    f"Once lifted, the Labyrinth begins to crumble as they take an arcane gate to safety."
                ),
                color=discord.Color.purple()
            )

            # Add your image
            final_embed.set_image(url="https://i.imgur.com/aJUTrri.png")

            # Send with actual mention in content so it pings
            await send_to_channels(
                content=f"{winner.mention}",
                embed=final_embed
            )


            # Update the profile and clear the raid timer
            async with self.bot.pool.acquire() as conn:
                await conn.execute(
                    f'UPDATE profile SET "crates_{crate}" = "crates_{crate}" + 1 WHERE "user" = $1;',
                    winner.id,
                )

            await self.clear_raid_timer()
        except Exception as e:
            import traceback
            error_message = f"Error occurred: {e}\n"
            error_message += traceback.format_exc()
            await ctx.send(error_message)
            print(error_message)

async def setup(bot):
    await bot.add_cog(MystMaze(bot))