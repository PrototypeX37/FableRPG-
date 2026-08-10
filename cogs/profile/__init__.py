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
import re
from typing import Optional
import json

import discord
import io
from io import BytesIO

from aiohttp import ContentTypeError
from discord import Embed
from discord.ext import commands

from classes.badges import Badge
from classes.bot import Bot
from classes.classes import from_string as class_from_string
from classes.context import Context
from classes.converters import IntFromTo, MemberWithCharacter, UserWithCharacter
from classes.items import ALL_ITEM_TYPES, ItemType
from cogs.adventure import ADVENTURE_NAMES
from cogs.help import chunks
from cogs.shard_communication import user_on_cooldown as user_cooldown
from cogs.profilecustomization import ProfileCustomization
from utils import checks, colors, random
from utils.loot import (
    LootManagerView,
    LootRewardView,
    LootSelectionView,
    acquire_loot_locks,
    delete_user_loot,
    fetch_user_loot,
    loot_value,
    release_loot_locks,
    reserve_loot_action_cooldown,
    reset_loot_action_cooldown,
    unique_loot_ids,
)
from utils import misc as rpgtools
from utils.checks import is_gm
from utils.i18n import _, locale_doc
from classes.errors import NoChoice



class ArmoryTypeSelect(discord.ui.Select):
    def __init__(self, armory_view: "ArmoryPaginatorView"):
        self.armory_view = armory_view
        super().__init__(
            placeholder=_("Filter by item type"),
            min_values=1,
            max_values=1,
            options=armory_view.build_itemtype_options(),
            row=2,
        )

    async def callback(self, interaction: discord.Interaction):
        selected_itemtype = self.values[0]
        if selected_itemtype == self.armory_view.itemtype:
            await interaction.response.defer()
            return

        self.armory_view.itemtype = selected_itemtype
        await self.armory_view.reload_pages(reset_page=True)
        await interaction.response.edit_message(
            embed=self.armory_view.embeds[self.armory_view.current_page],
            view=self.armory_view,
        )


class ArmoryRangeSelect(discord.ui.Select):
    def __init__(self, armory_view: "ArmoryPaginatorView"):
        self.armory_view = armory_view
        super().__init__(
            placeholder=_("Filter by item power"),
            min_values=1,
            max_values=1,
            options=armory_view.build_range_options(),
            row=3,
        )

    async def callback(self, interaction: discord.Interaction):
        lowest, highest = (int(value) for value in self.values[0].split(":", maxsplit=1))
        if lowest == self.armory_view.lowest and highest == self.armory_view.highest:
            await interaction.response.defer()
            return

        self.armory_view.lowest = lowest
        self.armory_view.highest = highest
        await self.armory_view.reload_pages(reset_page=True)
        await interaction.response.edit_message(
            embed=self.armory_view.embeds[self.armory_view.current_page],
            view=self.armory_view,
        )


class ArmoryPaginatorView(discord.ui.View):
    RANGE_PRESETS = [
        ("Any Power (0-201)", 0, 201),
        ("Starter (0-25)", 0, 25),
        ("Uncommon (26-60)", 26, 60),
        ("Strong (61-100)", 61, 100),
        ("Epic (101-150)", 101, 150),
        ("Legendary (151-201)", 151, 201),
    ]

    def __init__(
        self,
        ctx: commands.Context,
        profile_cog: "Profile",
        itemtype: str = "All",
        lowest: int = 0,
        highest: int = 201,
        timeout: float = 240.0,
    ):
        super().__init__(timeout=timeout)
        self.ctx = ctx
        self.profile_cog = profile_cog
        self.itemtype = itemtype
        self.lowest = lowest
        self.highest = highest
        self.pages: list[list[dict]] = []
        self.embeds: list[discord.Embed] = []
        self.current_page = 0
        self.message: Optional[discord.Message] = None

        self.type_select = ArmoryTypeSelect(self)
        self.range_select = ArmoryRangeSelect(self)
        self.add_item(self.type_select)
        self.add_item(self.range_select)
        self._sync_navigation_controls()
        self._sync_filter_controls()

    def build_itemtype_options(self) -> list[discord.SelectOption]:
        options = [
            discord.SelectOption(label="All Types", value="All"),
            discord.SelectOption(label="One-Handed (1h)", value="1h"),
            discord.SelectOption(label="Two-Handed (2h)", value="2h"),
        ]
        options.extend(
            discord.SelectOption(label=item_type.name, value=item_type.name)
            for item_type in ALL_ITEM_TYPES
        )
        return options

    def build_range_options(self) -> list[discord.SelectOption]:
        return [
            discord.SelectOption(label=label, value=f"{lowest}:{highest}")
            for label, lowest, highest in self.RANGE_PRESETS
        ]

    async def start(self):
        await self.reload_pages(reset_page=True)
        self.message = await self.ctx.send(embed=self.embeds[self.current_page], view=self)

    async def reload_pages(self, reset_page: bool = False):
        ret = await self.profile_cog.fetch_armory_items(
            user_id=self.ctx.author.id,
            itemtype=self.itemtype,
            lowest=self.lowest,
            highest=self.highest,
        )

        if not ret:
            self.pages = []
            self.embeds = [
                self.profile_cog.armory_empty_embed(
                    self.ctx, self.itemtype, self.lowest, self.highest
                )
            ]
            self.current_page = 0
        else:
            self.pages = list(chunks(ret, 5))
            if reset_page:
                self.current_page = 0
            else:
                self.current_page = min(self.current_page, len(self.pages) - 1)

            maxpage = len(self.pages) - 1
            self.embeds = [
                self.profile_cog.invembed(
                    self.ctx,
                    page,
                    idx,
                    maxpage,
                    itemtype=self.itemtype,
                    lowest=self.lowest,
                    highest=self.highest,
                )
                for idx, page in enumerate(self.pages)
            ]

        self._sync_filter_controls()
        self._sync_navigation_controls()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                _("Only the command author can use this menu."),
                ephemeral=True,
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

    def _sync_filter_controls(self):
        current_itemtype = self.itemtype
        for option in self.type_select.options:
            option.default = option.value == current_itemtype
        self.type_select.placeholder = _("Type: {itemtype}").format(
            itemtype=current_itemtype
        )

        current_range_value = f"{self.lowest}:{self.highest}"
        has_matching_preset = False
        for option in self.range_select.options:
            option.default = option.value == current_range_value
            has_matching_preset = has_matching_preset or option.default
        if not has_matching_preset:
            for option in self.range_select.options:
                option.default = False
        self.range_select.placeholder = _("Power: {lowest}-{highest}").format(
            lowest=self.lowest,
            highest=self.highest,
        )

    def _sync_navigation_controls(self):
        has_multiple_pages = len(self.embeds) > 1
        has_items = bool(self.pages)

        self.go_first.disabled = not has_multiple_pages or self.current_page == 0
        self.go_previous.disabled = not has_multiple_pages or self.current_page == 0
        self.go_next.disabled = (
            not has_multiple_pages or self.current_page >= len(self.embeds) - 1
        )
        self.go_last.disabled = (
            not has_multiple_pages or self.current_page >= len(self.embeds) - 1
        )
        self.copy_ids.disabled = not has_items

    @discord.ui.button(label="First", style=discord.ButtonStyle.blurple, row=0)
    async def go_first(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page == 0:
            await interaction.response.defer()
            return
        self.current_page = 0
        self._sync_navigation_controls()
        await interaction.response.edit_message(
            embed=self.embeds[self.current_page], view=self
        )

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.blurple, row=0)
    async def go_previous(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if self.current_page == 0:
            await interaction.response.defer()
            return
        self.current_page -= 1
        self._sync_navigation_controls()
        await interaction.response.edit_message(
            embed=self.embeds[self.current_page], view=self
        )

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, row=0)
    async def stop_pages(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        try:
            await interaction.message.delete()
        except discord.HTTPException:
            pass
        self.stop()

    @discord.ui.button(label="Next", style=discord.ButtonStyle.blurple, row=0)
    async def go_next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page >= len(self.embeds) - 1:
            await interaction.response.defer()
            return
        self.current_page += 1
        self._sync_navigation_controls()
        await interaction.response.edit_message(
            embed=self.embeds[self.current_page], view=self
        )

    @discord.ui.button(label="Last", style=discord.ButtonStyle.blurple, row=0)
    async def go_last(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page >= len(self.embeds) - 1:
            await interaction.response.defer()
            return
        self.current_page = len(self.embeds) - 1
        self._sync_navigation_controls()
        await interaction.response.edit_message(
            embed=self.embeds[self.current_page], view=self
        )

    @discord.ui.button(label="Copy IDs", style=discord.ButtonStyle.green, row=1)
    async def copy_ids(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.pages:
            await interaction.response.send_message(
                _("No item IDs available for this filter."),
                ephemeral=True,
            )
            return

        current_items = self.pages[self.current_page]
        item_ids = [str(item["id"]) for item in current_items]
        joined_ids = ", ".join(item_ids)
        await interaction.response.send_message(joined_ids, ephemeral=True)


class Profile(commands.Cog):
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    @staticmethod
    def _encode_preset_amulet_id(amulet_id: int) -> int:
        return -abs(amulet_id)

    @staticmethod
    def _split_preset_item_ids(raw_item_ids):
        gear_item_ids = []
        amulet_id = None

        for raw_id in raw_item_ids or []:
            if raw_id < 0 and amulet_id is None:
                amulet_id = abs(raw_id)
                continue
            if raw_id > 0:
                gear_item_ids.append(raw_id)

        return gear_item_ids, amulet_id

    @checks.has_no_char()
    @user_cooldown(3600)
    @commands.command(aliases=["new", "c", "start"], brief=_("Create a new character"))
    @locale_doc
    async def create(self, ctx, *, name: str = None):
        _(
            """`[name]` - The name to give your character; will be interactive if not given

            Create a new character and start playing FableRPG.

            (This command has a cooldown of 1 hour.)"""
        )

        from discord import Embed

        if not name:
            # Create an embed with a title and description
            embed = Embed(
                title="Character Creation",
                description=(
                    "What shall your character's name be? (Minimum 3 Characters, Maximum 20)\n\n"
                    "**Please note that with the creation of a character, you agree to these rules:**\n"
                    "1) Only up to two characters per individual\n"
                    "2) No abusing or benefiting from bugs or exploits\n"
                    "3) Be friendly and kind to other players\n"
                    "4) Trading in-game content for anything outside of the game is prohibited\n\n"
                    "FableRPG is a global bot, your characters are valid everywhere"
                ),
                color=0x00FF00  # You can customize the color of the embed here
            )

            # Send the embed message
            await ctx.send(embed=embed)



            # Send an additional message asking for the character's name
            name_msg = await ctx.send(_("Please reply with your character's name within 60 seconds."))

            def mycheck(amsg):
                return amsg.author == ctx.author and amsg.channel == ctx.channel

            try:
                name_response = await self.bot.wait_for("message", timeout=60, check=mycheck)
            except asyncio.TimeoutError:
                await ctx.send(_("Timeout expired. Please retry!"))
                await self.bot.reset_cooldown(ctx)
                return

            name = name_response.content
        else:
            if len(name) < 3 or len(name) > 20:
                await ctx.send(_("Character names must be at least 3 characters and up to 20."))
                await self.bot.reset_cooldown(ctx)
                return

        if "`" in name:
            await ctx.send(_("Illegal character (`) found in the name. Please try again and choose another name."))
            await self.bot.reset_cooldown(ctx)
            return

        # Check if user exists in the second database and offer migration if needed
        async with self.bot.second_pool.acquire() as conn:
            result = await conn.fetchrow('SELECT "money", "xp" FROM profile WHERE "user" = $1', ctx.author.id)

        if result:
            money = result['money']
            xp = result['xp']
            money = min(money, 300000)
            xp = min(xp, 5475604)

            from discord import Embed
            embed = Embed(
                title="Migration Details",
                description=(
                    "Here's what will and won't be migrated to your new character:"
                    "\n\n**Weapons:** Won't be migrated"
                    "\n**Raid Stats:** Won't be migrated"
                    "\n**Favor:** Won't be migrated"
                    "\n**XP:** Will be migrated (Up to level 30 cap)"
                    "\n**Money:** Will be migrated ($300,000 Max)"
                    "\n\nYou will only be given this option once."
                ),
                color=0x00ff00  # You can customize the color as needed
            )

            # Send the embed message
            await ctx.send(embed=embed)
            Level = int(rpgtools.xptolevel(xp))
            # Use ctx.confirm for migration confirmation
            if not await ctx.confirm(
                    _(
                        "It looks like you already have data in Idle's database. Do you want to migrate your XP and money to this new character?"
                        f"\n\nCharacter Level: **{Level}** with a small fortune of **${money}**."
                    )
            ):
                await ctx.send(_("Migration cancelled. Creating your new character without migration."))
                await self.create_character_without_migration(ctx, name)
            else:
                await self.migrate_and_create_character(ctx, name, xp, money)
        else:
            # Create the new character if no data exists in the second database
            await self.create_character_without_migration(ctx, name)

    async def create_character_without_migration(self, ctx, name):

        async with self.bot.pool.acquire() as primary_conn:
            async with primary_conn.transaction():
                await primary_conn.execute(
                    "INSERT INTO profile VALUES ($1, $2, $3, $4);",
                    ctx.author.id,
                    name,
                    100,
                    0,
                )
                await self.bot.create_item(
                    name=_("Starter Sword"),
                    value=0,
                    type_="Sword",
                    element="fire",
                    damage=3.0,
                    armor=0.0,
                    owner=ctx.author,
                    hand="any",
                    equipped=True,
                    conn=primary_conn,
                )
                await self.bot.create_item(
                    name=_("Starter Shield"),
                    value=0,
                    type_="Shield",
                    element="fire",
                    damage=0.0,
                    armor=3.0,
                    owner=ctx.author,
                    hand="left",
                    equipped=True,
                    conn=primary_conn,
                )
                await self.bot.log_transaction(
                    ctx,
                    from_=1,
                    to=ctx.author.id,
                    subject="Starting out",
                    data={"Gold": 100},
                    conn=primary_conn,
                )
                await primary_conn.execute(
                    'UPDATE profile SET "discordtag" = $1 WHERE "user" = $2',
                    str(ctx.author), ctx.author.id
                )

                async with self.bot.second_pool.acquire() as secondary_conn:
                    await secondary_conn.execute('DELETE FROM profile WHERE "user" = $1', ctx.author.id)

        await ctx.send(
            _(
                "Successfully created your new character **{name}**! Now use"
                " `{prefix}profile` to view your character!"
            ).format(name=name, prefix=ctx.clean_prefix)
        )

    async def migrate_and_create_character(self, ctx, name, xp, money):
        try:
            Level = int(rpgtools.xptolevel(xp))
            Statpoints = rpgtools.statpoints_for_level(Level)
            # Convert user ID to integer if needed
            user_id = int(ctx.author.id)

            async with self.bot.pool.acquire() as primary_conn:
                async with primary_conn.transaction():
                    await primary_conn.execute(
                        'INSERT INTO profile ("user", name, xp, money, statpoints, resetpotion) VALUES ($1, $2, $3, $4, $5, $6);',
                        user_id,  # Integer
                        name,
                        xp,
                        money,
                        Statpoints,
                        1,
                    )
                    await self.bot.create_item(
                        name=_("Starter Sword"),
                        value=0,
                        type_="Sword",
                        element="fire",
                        damage=3.0,
                        armor=0.0,
                        owner=ctx.author,
                        hand="any",
                        equipped=True,
                        conn=primary_conn,
                    )
                    await self.bot.create_item(
                        name=_("Starter Shield"),
                        value=0,
                        type_="Shield",
                        element="fire",
                        damage=0.0,
                        armor=3.0,
                        owner=ctx.author,
                        hand="left",
                        equipped=True,
                        conn=primary_conn,
                    )
                    await self.bot.log_transaction(
                        ctx,
                        from_=1,
                        to=user_id,
                        subject="Starting out",
                        data={"Gold": 100},
                        conn=primary_conn,
                    )
                    await primary_conn.execute(
                        'UPDATE profile SET "discordtag" = $1 WHERE "user" = $2',
                        str(ctx.author),  # Integer
                        user_id
                    )

            # Delete data from the second database
            async with self.bot.second_pool.acquire() as secondary_conn:
                await secondary_conn.execute('DELETE FROM profile WHERE "user" = $1', user_id)

            await ctx.send(
                _(
                    "Successfully migrated your data and created your new character **{name}**! Now use"
                    " `{prefix}profile` to view your character!"
                ).format(name=name, prefix=ctx.clean_prefix)
            )
        except Exception as e:
            import traceback
            error_message = f"Error occurred: {e}\n"
            error_message += traceback.format_exc()
            await ctx.send(error_message)
            print(error_message)

    @is_gm()
    @commands.command(name="check_user", brief="Check if your ID exists in the second database")
    async def check_user(self, ctx):
        user_id = ctx.author.id

        # Acquire a connection from the second database pool
        async with self.bot.second_pool.acquire() as conn:
            # Check if the user's ID exists in the `profile` table
            result = await conn.fetchval('SELECT 1 FROM profile WHERE "user" = $1', user_id)

        if result:
            await ctx.send(f"Your ID `{user_id}` exists in the second database!")
        else:
            await ctx.send(f"Your ID `{user_id}` does not exist in the second database.")


    @commands.command(name="profilepref")
    async def profilepref_command(self, ctx, preference: int):
        if preference == 1:
            new_profilestyle = False
            new_profilestyleText = "the new format"
        elif preference == 2:
            new_profilestyle = True
            new_profilestyleText = "the old format"
        else:
            await ctx.send("Invalid preference value. Use `1` for the new style or `2` for the old style.")
            return

        # Update the profilestyle column in the database
        async with self.bot.pool.acquire() as conn:
            update_query = 'UPDATE profile SET profilestyle = $1 WHERE "user" = $2'
            await conn.execute(update_query, new_profilestyle, ctx.author.id)

        await ctx.send(f"Profile preference updated to {new_profilestyleText}")

    @commands.command(name="legacyprofile", hidden=True, brief=_("View someone's profile"))
    @locale_doc
    async def legacyprofile(self, ctx, *, person: str = None):
        _(
            """`[person]` - The person whose profile to view; defaults to oneself

            View someone's profile. This will send an image.`"""
        )


        try:
            if person is None:
                person = str(ctx.author.id)

            id_pattern = re.compile(r'^\d{17,19}$')
            mention_pattern = re.compile(r'<@!?(\d{17,19})>')
            match = mention_pattern.match(person)

            if match:
                person = int(match.group(1))
                person = await self.bot.fetch_user(int(person))

            elif id_pattern.match(person):
                person = await self.bot.fetch_user(int(person))
            else:
                # Try to fetch by discordtag from our database
                async with self.bot.pool.acquire() as conn:
                    query = 'SELECT "user" FROM profile WHERE discordtag = $1'
                    user_id = await conn.fetchval(query, person)
                person = await self.bot.fetch_user(int(user_id))

            targetid = person.id
            discordtag = "none"
            try:
                discordtag = person.display_name
            except Exception as e:
                pass

            async with self.bot.pool.acquire() as conn:
                # Fetch the profilestyle column for the given user by either discordtag or userid
                profilestyle_query = '''
                    SELECT profilestyle FROM profile 
                    WHERE "user" = $1 OR discordtag = $2
                '''
                profilestyle_result = await conn.fetchval(profilestyle_query, targetid, discordtag)

                # Convert the result to a boolean (assuming profilestyle is a boolean column)
                profilestyle = bool(profilestyle_result) if profilestyle_result is not None else False

            if not profilestyle:
                person = person or ctx.author
                targetid = person.id

                async with self.bot.pool.acquire() as conn:
                    profile = await conn.fetchrow(
                        'SELECT p.*, g.name AS guild_name FROM profile p LEFT JOIN guild g ON (g."id"=p."guild") WHERE "user"=$1;',
                        targetid,
                    )

                    if not profile:
                        return await ctx.send(
                            _("**{person}** does not have a character.").format(person=person)
                        )

                    items = await self.bot.get_equipped_items_for(targetid, conn=conn)
                    mission = await self.bot.get_adventure(targetid)

                right_hand = None
                left_hand = None

                any_count = sum(1 for i in items if i["hand"] == "any")
                if len(items) == 2 and any_count == 1 and items[0]["hand"] == "any":
                    items = [items[1], items[0]]

                for i in items:
                    stat = f"{int(i['damage'] + i['armor'])}"
                    if i["hand"] == "both":
                        right_hand = left_hand = i
                    elif i["hand"] == "left":
                        left_hand = i
                    elif i["hand"] == "right":
                        right_hand = i
                    elif i["hand"] == "any":
                        if right_hand is None:
                            right_hand = i
                        else:
                            left_hand = i

                color = profile["colour"]
                color = [color["red"], color["green"], color["blue"], color["alpha"]]
                embed_color = discord.Colour.from_rgb(color[0], color[1], color[2])
                classes = [class_from_string(c) for c in profile["class"]]
                icons = [c.get_class_line_name().lower() if c else "none" for c in classes]

                guild_rank = None if not profile["guild"] else profile["guildrank"]

                marriage = (
                    await rpgtools.lookup(self.bot, profile["marriage"], return_none=True)
                    if profile["marriage"]
                    else None
                )

                if mission:
                    adventure_name = ADVENTURE_NAMES[mission[0]]
                    adventure_time = f"{mission[1]}" if not mission[2] else _("Finished")
                else:
                    adventure_name = None
                    adventure_time = None

                badge_val = Badge.from_db(profile["badges"])
                if badge_val:
                    badges = badge_val.to_items_lowercase()
                else:
                    badges = []

                async with self.bot.pool.acquire() as conn:
                # Get custom positions for this user  
                    custom_positions_json = await conn.fetchval(
                        'SELECT custom_positions FROM profile WHERE "user" = $1',
                        targetid
                    )

                
                # Parse JSON string to dict before passing to get_positions_for_user
                custom_positions = json.loads(custom_positions_json) if custom_positions_json else None
                positions = ProfileCustomization.get_positions_for_user(custom_positions)


                # CASCADE: Prepare and debug the dictionary for Okapi JSON payload
                payload_for_okapi = {
                    "name": profile['name'],
                    "color": color,
                    "image": profile["background"],
                    "race": profile['race'],
                    "classes": profile['class'],        # From user's snippet for line 552 json
                    "profession": "None",
                    "class_icons": icons,           # From user's snippet
                    "left_hand_item": (left_hand['type'], left_hand['name'], str(int(left_hand['damage'] if left_hand['damage'] > 0 else left_hand['armor'] if left_hand['armor'] > 0 else 0))) if left_hand else None,
                    "right_hand_item": (right_hand['type'], right_hand['name'], str(int(right_hand['damage'] if right_hand['damage'] > 0 else right_hand['armor'] if right_hand['armor'] > 0 else 0))) if right_hand else None,
                    "level": f"{rpgtools.xptolevel(profile['xp'])}",
                    "guild_rank": guild_rank,
                    "guild_name": profile["guild_name"], # From user's snippet
                    "money": f"{profile['money']}",
                    "pvp_wins": f"{profile['pvpwins']}", # From user's snippet
                    "marriage": marriage,               # From user's snippet
                    "god": profile["god"] or _("No God"),
                    "adventure_name": adventure_name,   # From user's snippet
                    "adventure_time": adventure_time,
                    "badges": badges,                   # From user's snippet
                    "positions": positions
                }
                


                async with self.bot.trusted_session.post(
                        f"{self.bot.config.external.okapi_url}/api/genprofile",
                        json=payload_for_okapi,
                        headers={"Authorization": self.bot.config.external.okapi_token},
                ) as req:
                    if req.status == 200:

                        img = await req.text()



                    else:
                        # Error, means try reading the response JSON error
                        try:
                            error_json = await req.json()
                            async with self.bot.pool.acquire() as conn:
                                # Update the background column in the profile table for the target user
                                update_query = 'UPDATE profile SET background = 0 WHERE "user" = $1'
                                await conn.execute(update_query, targetid)

                            return await ctx.send(
                                _(
                                    "There was an error processing your image. Reason: {reason} ({detail}). (Due to this, the profile image has been reset)"
                                ).format(
                                    reason=error_json["reason"], detail=error_json["detail"]
                                )
                            )
                        except ContentTypeError:
                            return await ctx.send(
                                _("Unexpected internal error when generating image.")
                            )
                        except Exception:
                            return await ctx.send(_("Unexpected error when generating image."))

                    async with self.bot.trusted_session.get(img) as resp:
                        bytebuffer = await resp.read()
                        if resp.status != 200:
                            return await ctx.send("Error failed to fetch image")

                await ctx.send(
                    _("Your Profile:"),
                    file=discord.File(fp=io.BytesIO(bytebuffer), filename="image.png"),
                )
            else:
                person = person or ctx.author
                targetid = person.id

                async with self.bot.pool.acquire() as conn:
                    query = """
                        SELECT g.name
                        FROM profile p
                        JOIN guild g ON p.guild = g.ID
                        WHERE p.user = $1
                    """
                    db_guild_name = await conn.fetchval(query, targetid)
                    guild_name = str(db_guild_name) if db_guild_name is not None else None

                ret = await self.bot.pool.fetch(
                    "SELECT ai.*, i.equipped FROM profile p JOIN allitems ai ON"
                    " (p.user=ai.owner) JOIN inventory i ON (ai.id=i.item) WHERE"
                    ' p."user"=$1 AND ((ai."damage"+ai."armor" BETWEEN $2 AND $3) OR'
                    ' i."equipped") ORDER BY i."equipped" DESC, ai."damage"+ai."armor"'
                    " DESC;",
                    targetid,
                    0,
                    160,
                )

                # Assuming you have 'name', 'damage', 'armor', and 'type' columns in 'allitems' table
                equipped_items = [row for row in ret if row['equipped']]

                # Separate variables for up to two equipped items
                item1 = equipped_items[0] if len(equipped_items) >= 1 else {"name": "None Equipped", "damage": 0,
                                                                            "armor": 0,
                                                                            "type": "None"}
                item2 = equipped_items[1] if len(equipped_items) >= 2 else {"name": "None Equipped", "damage": 0,
                                                                            "armor": 0,
                                                                            "type": "None"}

                async with self.bot.pool.acquire() as conn:
                    profile = await conn.fetchrow(
                        'SELECT p.*, g.name AS guild_name FROM profile p LEFT JOIN guild g ON (g."id"=p."guild") WHERE "user"=$1;',
                        targetid,
                    )

                    if not profile:
                        return await ctx.send(
                            _("**{person}** does not have a character.").format(person=person)
                        )

                    items = await self.bot.get_equipped_items_for(targetid, conn=conn)
                    mission = await self.bot.get_adventure(targetid)

                # Apply race bonuses
                race = profile["race"].lower()  # Assuming the race is stored in lowercase in the database

                damage_total = item1["damage"] + item2["damage"]
                armor_total = item1["armor"] + item2["armor"]
                item1_name = item1["name"]
                item2_name = item2["name"]
                item1_type = item1["type"]
                item2_type = item2["type"]

                classes = [class_from_string(c) for c in profile["class"]]
                icons = [c.get_class_line_name().lower() if c else "none" for c in classes]

                # Assuming you have classes with specific weapon type bonuses
                classes = {
                    "raider": {"Axe": 5},
                    "mage": {"Wand": 5},
                    "warrior": {"Sword": 5},
                    "ranger": {"Bow": 10},
                    "reaper": {"Scythe": 10},
                    "paladin": {"Hammer": 5},
                    "thief": {"Knife": 5, "Dagger": 5},
                    "paragon": {"Spear": 5},
                    "tank": {"shield": 10}
                }



                # Initialize bonus
                class_bonus = 0

                # Check if the user has classes and apply the corresponding bonuses
                for class_name in icons:
                    class_info = classes.get(class_name.lower(), {})
                    for item in [item1_type, item2_type]:
                        item_bonus = class_info.get(item, 0)
                        class_bonus += item_bonus

                query_class = 'SELECT "class" FROM profile WHERE "user" = $1;'

                specified_words_values = {
                    "Novice": 1,
                    "Proficient": 2,
                    "Artisan": 3,
                    "Master": 4,
                    "Champion": 5,
                    "Vindicator": 6,
                    "Paragon": 7,
                }
                # Query data for ctx.author.id
                result_author = await self.bot.pool.fetch(query_class, ctx.author.id)
                if result_author:
                    author_classes = result_author[0]["class"]  # Assume it's a list of classes
                    for class_name in author_classes:
                        if class_name in specified_words_values:
                            class_bonus += specified_words_values[class_name]


                # Apply the class bonus to the damage total
                damage_total += class_bonus

                if race == "human":
                    armor_total += 2
                    damage_total += 2
                elif race == "orc":
                    armor_total += 4
                elif race == "dwarf":
                    armor_total += 3
                    damage_total += 1
                elif race == "jikill":
                    damage_total += 4
                elif race == "elf":
                    armor_total += 1
                    damage_total += 3
                elif race == "elf":
                    armor_total += 1
                    damage_total -= 3
                elif race == "djinn":
                    armor_total -= 1
                    damage_total += 5
                elif race == "shadeborn":
                    armor_total += 5
                    damage_total -= 1

                right_hand = None
                left_hand = None


                async with self.bot.pool.acquire() as conn:
                    # Check if the user exists in the battletower table
                    level_query = "SELECT level FROM battletower WHERE id = $1"
                    level_result = await conn.fetchval(level_query, targetid)

                    # If the user doesn't exist, set the level to 0
                    level = level_result if level_result is not None else 0

                any_count = sum(1 for i in items if i["hand"] == "any")
                if len(items) == 2 and any_count == 1 and items[0]["hand"] == "any":
                    items = [items[1], items[0]]

                for i in items:
                    stat = f"{int(i['damage'] + i['armor'])}"
                    if i["hand"] == "both":
                        right_hand = left_hand = i
                    elif i["hand"] == "left":
                        left_hand = i
                    elif i["hand"] == "right":
                        right_hand = i
                    elif i["hand"] == "any":
                        if right_hand is None:
                            right_hand = i
                        else:
                            left_hand = i

                color = profile["colour"]
                color = [color["red"], color["green"], color["blue"], color["alpha"]]
                embed_color = discord.Colour.from_rgb(color[0], color[1], color[2])

                guild_rank = None if not profile["guild"] else profile["guildrank"]

                marriage = (
                    await rpgtools.lookup(self.bot, profile["marriage"], return_none=True)
                    if profile["marriage"]
                    else None
                )

                if mission:
                    adventure_name = ADVENTURE_NAMES[mission[0]]
                    adventure_time = f"{mission[1]}" if not mission[2] else _("Finished")
                else:
                    adventure_name = None
                    adventure_time = None

                badge_val = Badge.from_db(profile["badges"])
                if badge_val:
                    badges = badge_val.to_items_lowercase()
                else:
                    badges = []

                # Prepare class names and icon names as lists of strings
                processed_classes = []
                if profile['class']:
                    for c_raw in profile['class']:
                        cls_obj = class_from_string(c_raw) # Assumes class_from_string exists and works
                        if cls_obj:
                            processed_classes.append(cls_obj)
                
                classes_str_list = [c.get_class_line_name() for c in processed_classes if c] if processed_classes else []
                class_icons_list = [c.get_class_line_name().lower() for c in processed_classes if c] if processed_classes else [] # Assuming icon is lowercase class name



                async with self.bot.trusted_session.post(
                        f"http://127.0.0.1:3010/api/genprofile",
                        json={
                            "name": profile['name'],
                            "color": color,
                            "image": profile["background"],
                            "race": profile['race'],
                            "classes": classes_str_list,
                            "profession": "None",
                            "damage": f"{damage_total}",
                            "defense": f"{armor_total}",
                            "swordName": f"{item1_name}",
                            "shieldName": f"{item2_name}",
                            "level": f"{rpgtools.xptolevel(profile['xp'])}",
                            "guild_rank": guild_rank,
                            "guild": guild_name,
                            "money": profile['money'],
                            "pvpWins": f"{profile['pvpwins']}",
                            "marriage": marriage if marriage else _("None"),
                            "god": profile["god"] or _("No God"),
                            "adventure": adventure_name or _("No Mission"),
                            "adventure_time": adventure_time,
                            "icons": class_icons_list,
                            "BT": f"{level}"

                        },
                        headers={"Authorization": self.bot.config.external.okapi_token},
                ) as req:
                    img = BytesIO(await req.read())
                    # await ctx.send(f"{profile['class']}")
                    await ctx.send(file=discord.File(fp=img, filename="Profile.png"))
        except Exception as e:
            import traceback
            error_message = f"Error occurred: {e}\n"
            error_message += traceback.format_exc()
            await ctx.send(f"An error occurred during the game. {e}")
            print(error_message)  # Log for debugging

    @user_cooldown(300)
    @commands.command(aliases=["drink"], brief=_("Consume a potion or candy"))
    @locale_doc
    async def consume(self, ctx, item_type: str, pet_id: int = None):
        """
        Consume either a reset potion or level candy
        Valid types: reset, candy, highcandy, petage <pet_id>, petspeed <pet_id>, petxp <pet_id>
        """
        try:
            item_type = item_type.lower()
            async with self.bot.pool.acquire() as conn:
                profile_query = """
                        SELECT resetpotion, levelcandy, highqualitylevelcandy, xp
                        FROM profile
                        WHERE "user" = $1;
                        """
                profile = await conn.fetchrow(profile_query, ctx.author.id)

            if not profile:
                await ctx.send("Error: Unable to retrieve profile data.")
                await self.bot.reset_cooldown(ctx)
                return

            if item_type == "reset":
                if profile['resetpotion'] < 1:
                    await ctx.send("You don't have enough reset potions.")
                    await self.bot.reset_cooldown(ctx)
                    return

                if not await ctx.confirm(
                        _(
                            f"You are about to consume a `{item_type} potion`. Proceed?"
                        ).format(
                            item_type=item_type
                        )
                ):
                    await ctx.send(_("Potion consumption cancelled."))
                    return await self.bot.reset_cooldown(ctx)

                async with self.bot.pool.acquire() as conn:
                    query = """
                            SELECT statpoints, statatk, statdef, stathp, resetpotion
                            FROM profile
                            WHERE "user" = $1
                            FOR UPDATE;
                            """
                    profile = await conn.fetchrow(query, ctx.author.id)

                if not profile:
                    return await ctx.send("Profile not found.")

                total_stats = profile["statpoints"] + profile["statatk"] + profile["statdef"] + profile["stathp"]

                async with self.bot.pool.acquire() as conn:
                    update_query = """
                            UPDATE profile
                            SET statatk = 0, statdef = 0, stathp = 0, statpoints = $1, resetpotion = resetpotion - 1
                            WHERE "user" = $2;
                            """
                    await conn.execute(update_query, total_stats, ctx.author.id)

                await ctx.send(
                    "Stats updated successfully. As you drink the reset potion, a wave of dizziness "
                    "washes over you, making the world spin for a moment. You feel disoriented but also strangely invigorated, "
                    "as if your very being has been refreshed."
                )

            elif item_type == "candy":
                if profile['levelcandy'] < 1:
                    await ctx.send("You don't have enough level candy.")
                    await self.bot.reset_cooldown(ctx)
                    return

                if not await ctx.confirm(_(f"You are about to consume a level candy. Proceed?")):
                    await ctx.send(_("Candy consumption cancelled."))
                    return await self.bot.reset_cooldown(ctx)

                current_level = rpgtools.xptolevel(profile['xp'])
                current_xp = profile['xp']

                async with self.bot.pool.acquire() as conn:
                    # Consume the candy
                    await conn.execute(
                        'UPDATE profile SET levelcandy = levelcandy - 1 WHERE "user" = $1;',
                        ctx.author.id
                    )

                    xp = int(rpgtools.xptonextlevel(current_xp))

                    # Regular candy always gives one level
                    await conn.execute(
                        'UPDATE profile SET "xp" = "xp" + $1 WHERE "user" = $2;',
                        xp,
                        ctx.author.id
                    )
                    await self.bot.process_levelup(ctx, current_level + 1, current_level)

                await ctx.send(
                    f"You gained one level! As you eat the level candy, you feel a surge of energy course through your body, "
                    f"making you stronger and more experienced."
                )

            elif item_type == "highcandy":
                if profile['highqualitylevelcandy'] < 1:
                    await ctx.send("You don't have enough high quality level candy.")
                    await self.bot.reset_cooldown(ctx)
                    return

                if not await ctx.confirm(_(f"You are about to consume a high quality level candy. Proceed?")):
                    await ctx.send(_("Candy consumption cancelled."))
                    return await self.bot.reset_cooldown(ctx)

                current_level = rpgtools.xptolevel(profile['xp'])
                current_xp = profile['xp']

                async with self.bot.pool.acquire() as conn:
                    # Consume the candy
                    await conn.execute(
                        'UPDATE profile SET highqualitylevelcandy = highqualitylevelcandy - 1 WHERE "user" = $1;',
                        ctx.author.id
                    )

                    # First level up
                    xp = int(rpgtools.xptonextlevel(current_xp))
                    await conn.execute(
                        'UPDATE profile SET "xp" = "xp" + $1 WHERE "user" = $2;',
                        xp,
                        ctx.author.id
                    )
                    await self.bot.process_levelup(ctx, current_level + 1, current_level)

                    # Second level up
                    new_xp = await conn.fetchval('SELECT xp FROM profile WHERE "user" = $1;', ctx.author.id)
                    newxp = int(rpgtools.xptonextlevel(new_xp))
                    await conn.execute(
                        'UPDATE profile SET "xp" = "xp" + $1 WHERE "user" = $2;',
                        newxp,
                        ctx.author.id
                    )
                    await self.bot.process_levelup(ctx, current_level + 2, current_level + 1)

                await ctx.send(
                    f"You gained two levels! As you eat the high quality level candy, you feel an intense surge of energy course through your body, "
                    f"making you much stronger and more experienced."
                )

            elif item_type in ["petage", "pet age potion"]:
                # Handle pet age potion consumption
                if pet_id is None:
                    await ctx.send("Please provide a pet ID: `$consume petage <pet_id>` or `$consume \"pet age potion\" <pet_id>`")
                    await self.bot.reset_cooldown(ctx)
                    return
                
                premium_cog = self.bot.get_cog("PremiumShop")
                if not premium_cog:
                    await ctx.send("Premium shop cog not found.")
                    await self.bot.reset_cooldown(ctx)
                    return
                
                success, message = await premium_cog.consume_pet_age_potion(ctx, pet_id)
                if success:
                    await ctx.send(message)
                else:
                    await ctx.send(f"Error: {message}")
                    await self.bot.reset_cooldown(ctx)
                return
                
            elif item_type in ["petspeed", "pet speed growth potion"]:
                # Handle pet speed growth potion consumption
                if pet_id is None:
                    await ctx.send("Please provide a pet ID: `$consume petspeed <pet_id>` or `$consume \"pet speed growth potion\" <pet_id>`")
                    await self.bot.reset_cooldown(ctx)
                    return
                
                premium_cog = self.bot.get_cog("PremiumShop")
                if not premium_cog:
                    await ctx.send("Premium shop cog not found.")
                    await self.bot.reset_cooldown(ctx)
                    return
                
                success, message = await premium_cog.consume_pet_speed_growth_potion(ctx, pet_id)
                if success:
                    await ctx.send(message)
                else:
                    await ctx.send(f"Error: {message}")
                    await self.bot.reset_cooldown(ctx)
                return
                
            elif item_type in ["petxp", "pet xp potion"]:
                # Handle pet XP potion consumption
                if pet_id is None:
                    await ctx.send("Please provide a pet ID: `$consume petxp <pet_id>` or `$consume \"pet xp potion\" <pet_id>`")
                    await self.bot.reset_cooldown(ctx)
                    return
                
                premium_cog = self.bot.get_cog("PremiumShop")
                if not premium_cog:
                    await ctx.send("Premium shop cog not found.")
                    await self.bot.reset_cooldown(ctx)
                    return
                
                success, message = await premium_cog.consume_pet_xp_potion(ctx, pet_id)
                if success:
                    await ctx.send(message)
                else:
                    await ctx.send(f"Error: {message}")
                    await self.bot.reset_cooldown(ctx)
                return
                
            else:
                await ctx.send("Unknown item type. Valid types are: reset, candy, highcandy, petage <pet_id>, petspeed <pet_id>, petxp <pet_id>")
                await self.bot.reset_cooldown(ctx)
                return

        except Exception as e:
            import traceback
            error_message = f"Error occurred: {e}\n"
            error_message += traceback.format_exc()
            await ctx.send(error_message)
            print(error_message)

    @commands.command(
        aliases=["p2", "pp"], brief=_("View someone's profile differently")
    )
    @locale_doc
    async def profile2(self, ctx, *, target: str = None):
        """
        [target] - The person whose profile to view

        View someone's profile. This will send an embed rather than an image and is usually faster.
        """
        # Resolve the target user by mention, user ID, or discordtag.
        try:
            # If no target is provided, default to the command author.
            if not target:
                target = str(ctx.author.id)
            else:
                # Only take the first space-separated part
                target = target.split()[0]

            id_pattern = re.compile(r"^\d{17,19}$")
            mention_pattern = re.compile(r"<@!?(\d{17,19})>")
            user = None

            # Check for a proper mention
            mention_match = mention_pattern.match(target)
            if mention_match:
                user_id = int(mention_match.group(1))
                user = await self.bot.fetch_user(user_id)
            # Check for a valid numeric ID
            elif id_pattern.match(target):
                user = await self.bot.fetch_user(int(target))
            else:
                # Try to fetch by discordtag from our database
                async with self.bot.pool.acquire() as conn:
                    query = 'SELECT "user" FROM profile WHERE discordtag = $1'
                    user_id = await conn.fetchval(query, target)
                if user_id:
                    user = await self.bot.fetch_user(int(user_id))
                else:
                    raise ValueError("User not found")

            target_user = user
            display_tag = target_user.display_name
        except Exception as e:
            await ctx.send(_("Unknown User"))
            return

        # Get extra data (ranks, equipment, profile, etc.)
        rank_money, rank_xp = await self.bot.get_ranks_for(target_user)
        items = await self.bot.get_equipped_items_for(target_user)

        async with self.bot.pool.acquire() as conn:
            p_data = await conn.fetchrow(
                '''
                SELECT * FROM profile 
                WHERE "user" = $1 OR discordtag = $2
                ''',
                target_user.id,
                display_tag,
            )
            if not p_data:
                return await ctx.send(
                    _("**{target}** does not have a character.").format(target=target_user)
                )
            mission = await self.bot.get_adventure(target_user)
            guild = await conn.fetchval('SELECT name FROM guild WHERE "id"=$1;', p_data["guild"])
            pet = await conn.fetchval(
                'SELECT default_name FROM monster_pets WHERE "user_id"=$1 AND equipped = true;',
                target_user.id,
            ) or "None"

        # Get color from profile data (use default color if not available)
        try:
            col_data = p_data.get("colour")
            colour = discord.Colour.from_rgb(col_data["red"], col_data["green"], col_data["blue"])
        except (KeyError, ValueError, TypeError):
            colour = discord.Colour.default()

        # Prepare mission time-left text if mission data exists.
        timeleft = "N/A"
        if mission:
            # mission[1] is a timedelta/duration, and mission[2] is a finished flag
            timeleft = str(mission[1]).split(".")[0] if not mission[2] else "Finished"

        # Determine equipped items for each hand.
        right_hand, left_hand = None, None
        # A quick adjustment if only one "any" exists in a pair of items.
        any_count = sum(1 for item in items if item.get("hand") == "any")
        if len(items) == 2 and any_count == 1 and items[0].get("hand") == "any":
            items = [items[1], items[0]]

        for item in items:
            hand = item.get("hand")
            if hand == "both":
                right_hand = left_hand = item
            elif hand == "left":
                left_hand = item
            elif hand == "right":
                right_hand = item
            elif hand == "any":
                if not right_hand:
                    right_hand = item
                else:
                    left_hand = item

        # Check marriage information.
        marriage_display = "None"
        if p_data.get("marriage"):
            try:
                marriage_user = await self.bot.fetch_user(p_data["marriage"])
                marriage_display = marriage_user.display_name if marriage_user else "None"
            except discord.errors.NotFound:
                marriage_display = "None"
            except Exception:
                marriage_display = "None"

        # Build equipment strings.
        right_hand_str = (
            f"{right_hand['name']} - {right_hand['damage'] + right_hand['armor']}"
            if right_hand
            else _("None Equipped")
        )
        left_hand_str = (
            f"{left_hand['name']} - {left_hand['damage'] + left_hand['armor']}"
            if left_hand
            else _("None Equipped")
        )
        level = rpgtools.xptolevel(p_data["xp"])

        # Create the embed and add well-organized fields.
        embed = discord.Embed(
            colour=colour, title=f"{target_user.display_name}'s Profile", description=f"Character: {p_data['name']}"
        )
        embed.set_thumbnail(url=target_user.display_avatar.url)

        # General Information field.
        general_info = (
            f"**Money:** ${p_data['money']}\n"
            f"**Level:** {level}\n"
            f"**Pet:** {pet}\n"
            f"**Marriage:** {marriage_display}\n"
            f"**Class:** {' / '.join(p_data.get('class', [])) or 'N/A'}\n"
            f"**Race:** {p_data['race']}\n"
            f"**PvP Wins:** {p_data['pvpwins']}\n"
            f"**Guild:** {guild or 'None'}"
        )
        embed.add_field(name=_("General"), value=general_info, inline=False)

        # Ranks as an inline field.
        ranks_info = f"**Richest:** {rank_money}\n**XP:** {rank_xp}"
        embed.add_field(name=_("Ranks"), value=ranks_info, inline=True)

        # Equipment field as another inline field.
        equipment_info = f"**Right Hand:** {right_hand_str}\n**Left Hand:** {left_hand_str}"
        embed.add_field(name=_("Equipment"), value=equipment_info, inline=True)

        # Mission field if available.
        if mission:
            embed.add_field(name=_("Mission"), value=f"{mission[0]} - {timeleft}", inline=False)

        # Optionally, include a footer.
        embed.set_footer(text=f"User ID: {target_user.id}")

        await ctx.send(embed=embed)

    @checks.has_char()
    @commands.command(brief=_("Show your current luck"))
    @locale_doc
    async def luck(self, ctx):
        _(
            """Shows your current luck value.

            Luck updates once a week for everyone, usually on Monday. It depends on your God.
            Luck influences your adventure survival chances as well as the rewards.

            Luck is decided randomly within the Gods' luck boundaries. You can find your God's boundaries [here](https://wiki.idlerpg.xyz/index.php?title=Gods#List_of_Deities).

            If you have enough favor to place in the top 25 followers, you will gain additional luck:
              - The top 25 to 21 will gain +0.1 luck
              - The top 20 to 16 will gain +0.2 luck
              - The top 15 to 11 will gain +0.3 luck
              - The top 10 to 6 will gain +0.4 luck
              - The top 5 to 1 will gain +0.5 luck

            If you follow a new God (or become Godless), your luck will not update instantly, it will update with everyone else's luck on Monday."""
        )
        try:
            luck_value = float(ctx.character_data["luck"])  # Convert Decimal to float
            if luck_value <= 0.3:
                Luck = 20
            else:
                Luck = ((luck_value - 0.3) / (1.5 - 0.3)) * 80 + 20  # Linear interpolation between 20% and 100%
            Luck = round(Luck, 2)  # Round to two decimal places
            luck_booster = await self.bot.get_booster(ctx.author, "luck")
            if luck_booster:
                Luck += Luck * 0.25  # Add 25% if luck booster is true
                Luck = min(Luck, 100)  # Cap luck at 100%

            if luck_booster:
                calcluck = luck_value * 1.25
            else:
                calcluck = luck_value

            # Assuming Luck is a decimal.Decimal object
            flipped_luck = 100 - float(Luck)
            if flipped_luck < 0:
                flipped_luck = float(0)
            await ctx.send(
                _(
                    "Your current luck multiplier is `{luck}x.` "
                    "This makes your trip chance: `{trip}%`"
                ).format(
                    luck=round(calcluck, 2),
                    trip=round(float(flipped_luck), 2),
                )
            )
        except Exception as e:
            await ctx.send(e)

    @checks.has_char()
    @commands.command(
        aliases=["money", "e", "balance", "bal"], brief=_("Shows your balance")
    )
    @locale_doc
    async def economy(self, ctx):
        _(
            """Shows the amount of money you currently have.

            Among other ways, you can get more money by:
              - Playing adventures
              - Selling unused equipment
              - Gambling"""
        )
        await ctx.send(
            _("You currently have **${money}**, {author}!").format(
                money=ctx.character_data["money"], author=ctx.author.mention
            )
        )



    @checks.has_char()
    @commands.command(brief=_("Show a player's current XP"))
    @locale_doc
    async def xp(self, ctx, user: UserWithCharacter = None):
        _(
            """`[user]` - The player whose XP and level to show; defaults to oneself

            Show a player's XP and level.

            You can gain more XP by:
              - Completing adventures
              - Exchanging loot items for XP"""
        )
        user = user or ctx.author
        if user.id == ctx.author.id:
            points = ctx.character_data["xp"]
            await ctx.send(
                _(
                    "You currently have **{points} XP**, which means you are on Level"
                    " **{level}**. Missing to next level: **{missing}**"
                ).format(
                    points=points,
                    level=rpgtools.xptolevel(points),
                    missing=rpgtools.xptonextlevel(points),
                )
            )
        else:
            points = ctx.user_data["xp"]
            await ctx.send(
                _(
                    "{user} has **{points} XP** and is on Level **{level}**. Missing to"
                    " next level: **{missing}**"
                ).format(
                    user=user,
                    points=points,
                    level=rpgtools.xptolevel(points),
                    missing=rpgtools.xptonextlevel(points),
                )
            )

    def invembed(
            self,
            ctx,
            ret,
            currentpage,
            maxpage,
            itemtype: str = "All",
            lowest: int = 0,
            highest: int = 201,
    ):
        result = discord.Embed(
            title=_("{user}'s inventory includes").format(user=ctx.disp),
            colour=discord.Colour.blurple(),
        )
        result.description = _("Use the dropdowns below to filter by type and power range.")
        for weapon in ret:
            if weapon["equipped"]:
                eq = _("(**Equipped**)")
            else:
                eq = ""

            # Check if the weapon is locked and add "(locked)" if true
            locked_status = " (locked)" if weapon.get('locked', False) else ""

            statstr = (
                _("Damage: `{damage}`").format(damage=weapon["damage"])
                if weapon["type"] != "Shield"
                else _("Armor: `{armor}`").format(armor=weapon["armor"])
            )
            signature = (
                _("\nSignature: *{signature}*").format(signature=y)
                if (y := weapon["signature"])
                else ""
            )

            result.add_field(
                name=f"{weapon['name']}{locked_status} {eq}",  # Append (locked) if the item is locked
                value=_(
                    "ID: `{id}`, Element: `{element}` Type: `{type_}` (uses {hand} hand(s)) with {statstr}."
                    " Value is **${value}**{signature}"
                ).format(
                    id=weapon["id"],
                    element=weapon["element"],
                    type_=weapon["type"],
                    hand=weapon["hand"],
                    statstr=statstr,
                    value=weapon["value"],
                    signature=signature,
                ),
                inline=False,
            )

        result.set_footer(
            text=_(
                "Page {page} of {maxpages} | Type: {itemtype} | Power: {lowest}-{highest}"
            ).format(
                page=currentpage + 1,
                maxpages=maxpage + 1,
                itemtype=itemtype,
                lowest=lowest,
                highest=highest,
            )
        )
        return result

    def invembedd(self, ctx, reset_potions_chunk, amulets, level_candies, premium_consumables, current_page, max_page):
        result = discord.Embed(
            title=_("{user}'s Inventory").format(user=ctx.author.display_name),
            colour=discord.Colour.blurple(),
        )

        # Display equipped amulets first
        if current_page == 0:  # Only show on first page
            equipped_amulets = [a for a in amulets if a['equipped']]
            for amulet in equipped_amulets:
                name = ""
                value = ""
                if amulet['type'] == 'health':
                    name = f"<:balancedamulet:1388929966397198470> HP Amulet (ID: {amulet['id']}) (Equipped)"
                    value = f"Health: **+{amulet.get('hp', 0)}**, Defense: **+{amulet.get('defense', 0)}**, Attack: **+{amulet.get('attack', 0)}**\n"
                elif amulet['type'] == 'defense':
                    name = f"<:defenseamulet:1388930014111727666> Defense Amulet (ID: {amulet['id']}) (Equipped)"
                    value = f"Health: **+{amulet.get('hp', 0)}**, Defense: **+{amulet.get('defense', 0)}**, Attack: **+{amulet.get('attack', 0)}**\n"
                elif amulet['type'] == 'attack':
                    name = f"<:attackamulet:1388930099516014803> Attack Amulet (ID: {amulet['id']}) (Equipped)"
                    value = f"Health: **+{amulet.get('hp', 0)}**, Defense: **+{amulet.get('defense', 0)}**, Attack: **+{amulet.get('attack', 0)}**\n"
                elif amulet['type'] == 'balanced':
                    name = f"<:balancedamulet:1388929966397198470> Balanced Amulet (ID: {amulet['id']}) (Equipped)"
                    value = f"Health: **+{amulet.get('hp', 0)}**, Defense: **+{amulet.get('defense', 0)}**, Attack: **+{amulet.get('attack', 0)}**\n"
                value += f"Tier: {amulet.get('tier', 0)} Value: ${amulet.get('value', 0):,}"
                result.add_field(
                    name=name,
                    value=value,
                    inline=False
                )

        # Display unequipped amulets
        if current_page == 0:  # Only show on first page
            unequipped_amulets = [a for a in amulets if not a['equipped']]
            for amulet in unequipped_amulets:
                name = ""
                value = ""
                if amulet['type'] == 'health':
                    name = f"<:hp_amulet_red_glow:1318549180293058600> HP Amulet (ID: {amulet['id']})"
                    value = f"Health: **+{amulet.get('hp', 0)}**, Defense: **+{amulet.get('defense', 0)}**, Attack: **+{amulet.get('attack', 0)}**\n"
                elif amulet['type'] == 'defense':
                    name = f"<:def_amulet_blue_glow:1318549812475596820> Defense Amulet (ID: {amulet['id']})"
                    value = f"Health: **+{amulet.get('hp', 0)}**, Defense: **+{amulet.get('defense', 0)}**, Attack: **+{amulet.get('attack', 0)}**\n"
                elif amulet['type'] == 'attack':
                    name = f"<:atk_amulet_fixed_glow:1318549263889862677> Attack Amulet (ID: {amulet['id']})"
                    value = f"Health: **+{amulet.get('hp', 0)}**, Defense: **+{amulet.get('defense', 0)}**, Attack: **+{amulet.get('attack', 0)}**\n"
                elif amulet['type'] == 'balanced':
                    name = f"<:balancedamulet:1388929966397198470> Balanced Amulet (ID: {amulet['id']})"
                    value = f"Health: **+{amulet.get('hp', 0)}**, Defense: **+{amulet.get('defense', 0)}**, Attack: **+{amulet.get('attack', 0)}**\n"
                value += f"Tier: {amulet.get('tier', 0)} Value: ${amulet.get('value', 0):,}"
                result.add_field(
                    name=name,
                    value=value,
                    inline=False
                )

        # Display level candies if they exist
        for candy in level_candies:
            if candy['levelcandy'] > 0:
                result.add_field(
                    name="🍬 Level Candy",
                    value=f"Quantity: {candy['levelcandy']}\nGrants one level on use (`$consume candy`)",
                    inline=False
                )
            if candy.get('highqualitylevelcandy', 0) > 0:
                result.add_field(
                    name="🍬 Super Level Candy",
                    value=f"Quantity: {candy['highqualitylevelcandy']}\nGrants two levels on use (`$consume highcandy`)",
                    inline=False
                )

        # Display reset potions
        for reset_potion in reset_potions_chunk:
            result.add_field(
                name="<:Resetpotion2:1245040954382090270> Reset Potion",
                value=f"Quantity: {reset_potion}",
                inline=False
            )

        # Display premium consumables
        for consumable in premium_consumables:
            consumable_type = consumable['consumable_type']
            quantity = consumable['quantity']
            
            if consumable_type == 'pet_age_potion':
                result.add_field(
                    name="<:ageup:1398715567166455969> Pet Age Potion",
                    value=f"Quantity: {quantity}\nInstantly age your pet to the next growth stage (`$consume petage <pet_id>` or `$consume \"pet age potion\" <pet_id>`)",
                    inline=False
                )
            elif consumable_type == 'pet_speed_growth_potion':
                result.add_field(
                    name="<:finalpotion:1398721503268438169> Pet Speed Growth Potion",
                    value=f"Quantity: {quantity}\nDoubles growth speed for a specific pet (`$consume petspeed <pet_id>` or `$consume \"pet speed growth potion\" <pet_id>`)",
                    inline=False
                )
            elif consumable_type == 'pet_xp_potion':
                result.add_field(
                    name="<:splicepotion:1399690724051779745> Pet XP Potion",
                    value=f"Quantity: {quantity}\nGives a pet permanent x2 XP multiplier (`$consume petxp <pet_id>` or `$consume \"pet xp potion\" <pet_id>`)",
                    inline=False
                )
            elif consumable_type == 'splice_final_potion':
                result.add_field(
                    name="🔮 Splice Final Potion",
                    value=f"Quantity: {quantity}\n15% chance for [FINAL] result on next splice (`$consume splicefinal`)",
                    inline=False
                )

        result.set_footer(
            text=_("Page {page} of {maxpages}").format(
                page=current_page + 1, maxpages=max_page + 1
            )
        )
        return result

    @checks.has_char()
    @commands.command(aliases=["i", "inv"], brief=_("Show your gear items"))
    @locale_doc
    async def inventory(self, ctx):
        try:
            await ctx.send(
                "weapons has moved to `$armory` with aliases `$ar` and `$arm` to make room for a future update."
            )
            await ctx.send("Related commands `$consume <type>`")

            async with self.bot.pool.acquire() as conn:
                # Fetch reset potions and level candies
                ret = await conn.fetch(
                    'SELECT resetpotion, levelcandy, highqualitylevelcandy FROM profile WHERE "user" = $1;',
                    ctx.author.id,
                )

                # Fetch amulets
                amulets = await conn.fetch(
                    'SELECT * FROM amulets WHERE user_id = $1 ORDER BY equipped DESC;',
                    ctx.author.id,
                )
                
                # Fetch premium consumables
                premium_consumables = await conn.fetch(
                    'SELECT consumable_type, quantity FROM user_consumables WHERE user_id = $1 AND quantity > 0;',
                    ctx.author.id,
                )

            # Check if inventory is empty
            has_reset_potions = ret and ret[0]['resetpotion'] > 0
            has_level_candy = ret and ret[0]['levelcandy'] > 0
            has_high_candy = ret and ret[0]['highqualitylevelcandy'] > 0
            has_amulets = bool(amulets)
            has_premium = bool(premium_consumables)
            
            if not (has_reset_potions or has_amulets or has_level_candy or has_high_candy or has_premium):
                return await ctx.send(_("Your inventory is empty."))

            # Handle reset potions pagination
            if has_reset_potions:
                reset_potions = [item['resetpotion'] for item in ret]
                chunks_size = 5
                reset_potions_chunks = [
                    reset_potions[i:i + chunks_size]
                    for i in range(0, len(reset_potions), chunks_size)
                ]
                max_page = len(reset_potions_chunks) - 1
            else:
                # If no reset potions, create a single empty chunk
                reset_potions_chunks = [[]]
                max_page = 0

            embeds = [
                self.invembedd(ctx, chunk, amulets, ret, premium_consumables, idx, max_page)
                for idx, chunk in enumerate(reset_potions_chunks)
            ]

            await self.bot.paginator.Paginator(extras=embeds).paginate(ctx)

        except Exception as e:
            import traceback
            error_message = f"Error occurred: {e}\n"
            error_message += traceback.format_exc()
            await ctx.send(error_message)
            print(error_message)

    def lootembed(self, ctx, ret, currentpage, maxpage):
        result = discord.Embed(
            title=_("{user} has the following loot items.").format(user=ctx.disp),
            colour=discord.Colour.blurple(),
        )
        for item in ret:
            element = item.get("element", "Unknown")  # Accessing the "element" from the item data

            result.add_field(
                name=f"<:resetpotion: 1245034461960081409> - Reset Potion",  # Including element in the name of the item
                value=_("Amount: `{id}` Value is **{value}**").format(
                    id=ret[0]['resetpotion'], value=0
                ),
                inline=False,
            )

        return result
    @checks.has_char()
    @commands.command(aliases=["sp"], brief=_("Show your gear items"))
    @locale_doc
    async def statpoints(self, ctx):
        # Fetch stat points and stats for the user
        query = 'SELECT "statpoints", "statatk", "statdef", "stathp" FROM profile WHERE "user" = $1;'
        result = await self.bot.pool.fetch(query, ctx.author.id)
        if not result:
            await ctx.send("No character data found.")
            return

        player_data = result[0]
        points = player_data["statpoints"]
        atk = player_data["statatk"]
        def_ = player_data["statdef"]
        hp = player_data["stathp"]

        # Creating a clean and structured embed
        embed = Embed(title="Stat Points", description="Overview of your stat points and how to redeem them.",
                      color=0x3498db)
        embed.add_field(name="Your Stat Points", value=f"You currently have **{points}** unused stat points.",
                        inline=False)
        embed.add_field(name="How to Redeem", value="Redeem your stat points using the commands below:", inline=False)
        embed.add_field(name="Commands",
                        value="`$spr <type> <amount>` or `$statpointredeem <type> <amount>`\n- Types: `health/hp`, `defense/def`, `attack/atk`\n- Example: `$spr atk 5`",
                        inline=False)
        embed.add_field(name="Bonuses",
                        value="Raider bonuses:\n- **Attack**: `+0.1`\n- **Defense**: `+0.1`\n- **Health**: `+50`",
                        inline=False)
        embed.add_field(name="Stat Allocation",
                        value=f"**Attack**: {atk}\n**Defense**: {def_}\n**Health**: {hp}",
                        inline=False)
        embed.set_footer(text="Ensure you have sufficient stat points before redeeming.")

        # Send the embed
        await ctx.send(embed=embed)

    @checks.has_char()
    @user_cooldown(120)
    @commands.command(aliases=["spr"], brief=_("Show your gear items"))
    @locale_doc
    async def statpointsredeem(self, ctx, type: str, amount: int):
        # Validate type
        type = type.lower()  # Handle case insensitivity
        valid_types = {
            "def": "statdef",
            "defense": "statdef",
            "attack": "statatk",
            "atk": "statatk",
            "health": "stathp",
            "hp": "stathp",
        }

        if amount <= 0:
            await ctx.send(_("Amount must be greater than 0."))
            await self.bot.reset_cooldown(ctx)
            return

        if type not in valid_types:
            await ctx.send(
                _("Invalid type specified. Please use 'def', 'defense', 'attack', 'atk', 'health', or 'hp'."))
            return

        # Fetch current stat points
        query = 'SELECT "statpoints" FROM profile WHERE "user" = $1;'
        result = await self.bot.pool.fetch(query, ctx.author.id)
        if not result:
            await ctx.send(_("No character data found."))
            return

        player_data = result[0]
        points = player_data["statpoints"]

        # Check if user has enough points
        if points < amount:
            await ctx.send(_("You do not have enough stat points to redeem."))
            return

        if not await ctx.confirm(
                _("Are you sure you want to redeem {amount} {type} points?").format(amount=amount, type=type)):
            return await ctx.send(_("Redeeming cancelled."))

        # Calculate new stat points and update the profile
        new_stat_points = points - amount
        stat_column = valid_types[type]
        update_query = f'UPDATE profile SET "statpoints" = $1, "{stat_column}" = "{stat_column}" + $2 WHERE "user" = $3;'
        await self.bot.pool.execute(update_query, new_stat_points, amount, ctx.author.id)

        # Confirmation message
        await ctx.send(
            _(f"Successfully redeemed {amount} points to {type}. You now have {new_stat_points} stat points remaining."))

    def normalize_armory_itemtype(self, itemtype: str | None) -> tuple[Optional[str], Optional[str]]:
        if itemtype is None:
            return "All", None

        cleaned_itemtype = itemtype.strip()
        if not cleaned_itemtype:
            return "All", None

        alias_map = {
            "all": "All",
            "any": "All",
            "everything": "All",
            "1h": "1h",
            "onehand": "1h",
            "one-handed": "1h",
            "one_handed": "1h",
            "2h": "2h",
            "twohand": "2h",
            "two-handed": "2h",
            "two_handed": "2h",
        }
        lowered_itemtype = cleaned_itemtype.lower()
        if lowered_itemtype in alias_map:
            return alias_map[lowered_itemtype], None

        normalized_itemtype = cleaned_itemtype.title()
        if ItemType.from_string(normalized_itemtype) is not None:
            return normalized_itemtype, None

        return (
            None,
            _(
                "Please select a valid item type or `all`, `1h`, `2h`. Available types:"
                " `{all_types}`"
            ).format(all_types=", ".join([t.name for t in ALL_ITEM_TYPES])),
        )

    async def fetch_armory_items(
            self,
            user_id: int,
            itemtype: str,
            lowest: int,
            highest: int,
    ):
        if itemtype == "All":
            return await self.bot.pool.fetch(
                "SELECT ai.*, i.equipped, i.locked "
                "FROM profile p "
                "JOIN allitems ai ON (p.user=ai.owner) "
                "JOIN inventory i ON (ai.id=i.item) "
                'WHERE p."user"=$1 AND ((ai."damage"+ai."armor" BETWEEN $2 AND $3) OR i."equipped") '
                'ORDER BY i."equipped" DESC, i.locked DESC, ai."damage"+ai."armor" DESC;',
                user_id,
                lowest,
                highest,
            )

        if itemtype == "2h":
            return await self.bot.pool.fetch(
                "SELECT ai.*, i.equipped, i.locked "
                "FROM profile p "
                "JOIN allitems ai ON (p.user=ai.owner) "
                "JOIN inventory i ON (ai.id=i.item) "
                'WHERE p."user"=$1 AND ((ai."damage"+ai."armor" BETWEEN $2 AND $3 AND ai."hand"=$4) '
                'OR i."equipped") '
                'ORDER BY i."equipped" DESC, i.locked DESC, ai."damage"+ai."armor" DESC;',
                user_id,
                lowest,
                highest,
                "both",
            )

        if itemtype == "1h":
            return await self.bot.pool.fetch(
                "SELECT ai.*, i.equipped, i.locked "
                "FROM profile p "
                "JOIN allitems ai ON (p.user=ai.owner) "
                "JOIN inventory i ON (ai.id=i.item) "
                'WHERE p."user"=$1 AND ((ai."damage"+ai."armor" BETWEEN $2 AND $3 AND ai."hand"!=$4) '
                'OR i."equipped") '
                'ORDER BY i."equipped" DESC, i.locked DESC, ai."damage"+ai."armor" DESC;',
                user_id,
                lowest,
                highest,
                "both",
            )

        return await self.bot.pool.fetch(
            "SELECT ai.*, i.equipped, i.locked "
            "FROM profile p "
            "JOIN allitems ai ON (p.user=ai.owner) "
            "JOIN inventory i ON (ai.id=i.item) "
            'WHERE p."user"=$1 AND ((ai."damage"+ai."armor" BETWEEN $2 AND $3 AND ai."type"=$4) '
            'OR i."equipped") '
            'ORDER BY i."equipped" DESC, i.locked DESC, ai."damage"+ai."armor" DESC;',
            user_id,
            lowest,
            highest,
            itemtype,
        )

    def armory_empty_embed(self, ctx, itemtype: str, lowest: int, highest: int):
        result = discord.Embed(
            title=_("{user}'s inventory includes").format(user=ctx.disp),
            description=_("No items match this filter."),
            colour=discord.Colour.blurple(),
        )
        result.set_footer(
            text=_("Type: {itemtype} | Power: {lowest}-{highest}").format(
                itemtype=itemtype,
                lowest=lowest,
                highest=highest,
            )
        )
        return result

    def armory_help_embed(self, ctx):
        prefix = ctx.clean_prefix
        result = discord.Embed(
            title=_("Armory Help"),
            description=_("Browse and filter your equipped and unequipped gear."),
            colour=discord.Colour.blurple(),
        )
        result.add_field(
            name=_("Quick Start"),
            value=f"`{prefix}arm`\n`{prefix}arm help`",
            inline=False,
        )
        result.add_field(
            name=_("Text Filters"),
            value=(
                f"`{prefix}arm <type>`\n"
                f"`{prefix}arm <type> <min_power> <max_power>`\n"
                f"`{prefix}arm 2h 80 201`"
            ),
            inline=False,
        )
        result.add_field(
            name=_("Item Types"),
            value=(
                "`all`, `1h`, `2h`, `sword`, `shield`, `axe`, `wand`, `dagger`, "
                "`knife`, `spear`, `bow`, `hammer`, `scythe`, `mace`"
            ),
            inline=False,
        )
        result.add_field(
            name=_("Interactive Controls"),
            value=_("Use dropdowns for type/power filters and buttons to navigate pages or copy IDs."),
            inline=False,
        )
        return result

    @checks.has_char()
    @commands.command(aliases=["arm", "ar"], brief=_("Show your gear items"))
    @locale_doc
    async def armory(
            self,
            ctx,
            itemtype: str | None = "All",
            lowest: IntFromTo(0, 201) = 0,
            highest: IntFromTo(0, 201) = 201,
    ):
        _(
            """`[itemtype]` - The type of item to show; defaults to all items
            `[lowest]` - The lower boundary of items to show; defaults to 0
            `[highest]` - The upper boundary of items to show; defaults to 201

            Show your gear items. Items that are in the market will not be shown.

            Gear items can be equipped, sold and given away, or upgraded and merged to make them stronger.
            You can gain gear items by completing adventures, opening crates, or having your pet hunt for them, if you are a ranger.

            Run `{prefix}armory` or `{prefix}arm` without arguments to use interactive dropdown filters.

            To sell unused items for their value, use `{prefix}merch`. To put them up on the global player market, use `{prefix}sell`."""
        )

        if isinstance(itemtype, str) and itemtype.lower() in {"help", "h", "?", "usage"}:
            return await ctx.send(embed=self.armory_help_embed(ctx))

        if highest < lowest:
            return await ctx.send(
                _("Make sure that the `highest` value is greater than `lowest`.")
            )

        normalized_itemtype, validation_error = self.normalize_armory_itemtype(itemtype)
        if validation_error:
            return await ctx.send(validation_error)

        view = ArmoryPaginatorView(
            ctx=ctx,
            profile_cog=self,
            itemtype=normalized_itemtype or "All",
            lowest=lowest,
            highest=highest,
        )
        await view.start()

    def lootembed(self, ctx, ret, currentpage, maxpage):
        result = discord.Embed(
            title=_("{user} has the following loot items.").format(user=ctx.disp),
            colour=discord.Colour.blurple(),
        )
        for item in ret:
            element = item.get("element", "Unknown")  # Accessing the "element" from the item data

            result.add_field(
                name=f"{item['name']}",  # Including element in the name of the item
                value=_("ID: {id} Value is **{value}**").format(
                    id=item["id"], value=item["value"]
                ),
                inline=False,
            )

        return result


    @checks.has_char()
    @commands.command(aliases=["loot"], brief=_("Show your loot items"))
    @locale_doc
    async def items(self, ctx):
        _(
            """Show your loot items.

            Loot items can be exchanged for money or XP, or sacrificed to your God to gain favor points.

            You can gain loot items by completing adventures. The higher the difficulty, the higher the chance to get loot.
            If you are a Ritualist, your loot chances are doubled. Check [our wiki](https://wiki.idlerpg.xyz/index.php?title=Loot#Probability) for the exact chances."""
        )
        ret = await self.bot.pool.fetch(
            'SELECT * FROM loot WHERE "user"=$1 ORDER BY "value" DESC, "id" DESC;',
            ctx.author.id,
        )
        if not ret:
            return await ctx.send(_("You do not have any loot at this moment."))
        view = LootManagerView(
            ctx,
            ret,
            exchange_callback=self.perform_exchange_loot,
            sacrifice_callback=self.perform_sacrifice_from_loot_menu,
        )
        await view.start()

    async def perform_sacrifice_from_loot_menu(
        self,
        ctx,
        selected_loot_ids: list[int],
        requested_count: int,
        reserve_cooldown: bool = False,
    ) -> None:
        if not ctx.character_data.get("god"):
            return await ctx.send(
                _("You need to follow a God before you can sacrifice loot.")
            )

        gods_cog = self.bot.get_cog("Gods")
        if gods_cog is None:
            return await ctx.send(_("Sacrifice is not available right now."))

        await gods_cog.perform_sacrifice_loot(
            ctx,
            selected_loot_ids,
            requested_count=requested_count,
            reserve_cooldown=reserve_cooldown,
        )

    async def perform_exchange_loot(
        self,
        ctx,
        selected_loot_ids: list[int],
        requested_count: int | None = None,
        reserve_cooldown: bool = False,
    ) -> None:
        if reserve_cooldown:
            retry_after = await reserve_loot_action_cooldown(self.bot, ctx.author.id)
            if retry_after:
                return await ctx.send(
                    _(
                        "You are already using loot. Try again in {seconds} seconds."
                    ).format(seconds=int(retry_after))
                )

        selected_loot_ids = unique_loot_ids(selected_loot_ids)
        requested_count = requested_count or len(selected_loot_ids)

        async with self.bot.pool.acquire() as conn:
            available_loot = await fetch_user_loot(conn, ctx.author.id, selected_loot_ids)

        if not available_loot:
            await reset_loot_action_cooldown(self.bot, ctx)
            return await ctx.send(
                _("Those loot item(s) were already used by another action.")
            )

        selected_loot_ids = [int(item["id"]) for item in available_loot]
        locks, blocked = await acquire_loot_locks(
            self.bot, ctx.author.id, selected_loot_ids, "exchange"
        )
        if blocked:
            await reset_loot_action_cooldown(self.bot, ctx)
            return await ctx.send(
                _(
                    "Loot item(s) `{loot_ids}` are already being used. Finish or"
                    " cancel the other loot action first."
                ).format(loot_ids=", ".join([str(loot_id) for loot_id in blocked]))
            )

        try:
            value = int(loot_value(available_loot))
            count = len(available_loot)

            try:
                reward = await LootRewardView(
                    ctx, item_count=count, money_value=value
                ).prompt()
            except NoChoice:
                await reset_loot_action_cooldown(self.bot, ctx)
                return await ctx.send(_("You didn't choose anything."))

            if reward is None:
                await reset_loot_action_cooldown(self.bot, ctx)
                return await ctx.send(_("Cancelled."))

            if reward == "xp":
                old_level = rpgtools.xptolevel(ctx.character_data["xp"])

            async with self.bot.pool.acquire() as conn:
                async with conn.transaction():
                    deleted_loot = await delete_user_loot(
                        conn, ctx.author.id, selected_loot_ids
                    )
                    if not deleted_loot:
                        await reset_loot_action_cooldown(self.bot, ctx)
                        return await ctx.send(
                            _("Those loot item(s) were already used by another action.")
                        )

                    actual_value = int(loot_value(deleted_loot))
                    reward_amount = actual_value // 4 if reward == "xp" else actual_value
                    await conn.execute(
                        f'UPDATE profile SET "{reward}"="{reward}"+$1 WHERE "user"=$2;',
                        reward_amount,
                        ctx.author.id,
                    )
                    await self.bot.log_transaction(
                        ctx,
                        from_=1,
                        to=ctx.author.id,
                        subject="exchange",
                        data={"Reward": reward, "Amount": reward_amount},
                        conn=conn,
                    )

            text = _(
                "You received **{reward}** when exchanging loot item(s)"
                " `{loot_ids}`. "
            ).format(
                reward=(
                    f"${reward_amount}"
                    if reward == "money"
                    else f"{reward_amount} XP"
                ),
                loot_ids=", ".join([str(item["id"]) for item in deleted_loot]),
            )
            skipped = requested_count - len(deleted_loot)
            additional = _(
                "Skipped `{amount}` because they did not belong to you or were"
                " already used."
            ).format(amount=skipped)

            await ctx.send(text + (additional if skipped else ""))

            if reward == "xp":
                new_level = int(
                    rpgtools.xptolevel(ctx.character_data["xp"] + reward_amount)
                )
                if old_level != new_level:
                    await self.bot.process_levelup(ctx, new_level, old_level)
        finally:
            await release_loot_locks(self.bot, locks)

    @checks.has_char()
    @user_cooldown(180, identifier="sacrificeexchange")
    @commands.command(aliases=["ex"], brief=_("Exchange your loot for money or XP"))
    @locale_doc
    async def exchange(self, ctx, *loot_ids: int):
        _(
            """`[loot_ids...]` - The loot IDs to exchange; defaults to all loot

            Exchange your loot for money or XP, the bot will let you choose.

            If you choose money, you will get the loots' combined value in cash. For XP, you will get 1/4th of the combined value in XP."""
        )
        none_given = len(loot_ids) == 0
        requested_loot_ids = unique_loot_ids(loot_ids)

        async with self.bot.pool.acquire() as conn:
            available_loot = await fetch_user_loot(
                conn, ctx.author.id, None if none_given else requested_loot_ids
            )

        if not available_loot:
            await reset_loot_action_cooldown(self.bot, ctx)
            if none_given:
                return await ctx.send(_("You don't have any loot."))
            return await ctx.send(
                _("You don't own any loot items with the IDs: {itemids}").format(
                    itemids=", ".join([str(loot_id) for loot_id in requested_loot_ids])
                )
            )

        if none_given:
            try:
                selected_loot_ids = await LootSelectionView(
                    ctx,
                    available_loot,
                    title=_("Select loot to exchange"),
                    placeholder=_("Choose loot to exchange"),
                ).prompt()
            except NoChoice:
                await reset_loot_action_cooldown(self.bot, ctx)
                return await ctx.send(_("You didn't choose anything."))

            if not selected_loot_ids:
                await reset_loot_action_cooldown(self.bot, ctx)
                return await ctx.send(_("Cancelled."))
        else:
            selected_loot_ids = [int(item["id"]) for item in available_loot]

        requested_count = (
            len(selected_loot_ids) if none_given else len(requested_loot_ids)
        )
        await self.perform_exchange_loot(
            ctx, selected_loot_ids, requested_count=requested_count
        )

    @user_cooldown(180)
    @checks.has_char()
    @commands.command(aliases=["use"], brief=_("Equip an item"))
    @locale_doc
    async def equip(self, ctx, itemid: int):
        _(
            """`<itemid>` - The ID of the item to equip

            Equip an item by its ID, you can find the item IDs in your inventory.

            Each item has an assigned hand slot,
              "any" meaning that the item can go in either hand,
              "both" meaning it takes both hands,
              "left" and "right" should be clear.

            You cannot equip two items that use the same hand, or a second item if the one your have equipped is two-handed."""
        )
        async with self.bot.pool.acquire() as conn:
            item = await conn.fetchrow(
                'SELECT ai.* FROM inventory i JOIN allitems ai ON (i."item"=ai."id")'
                ' WHERE ai."owner"=$1 and ai."id"=$2;',
                ctx.author.id,
                itemid,
            )
            if not item:
                await self.bot.reset_cooldown(ctx)
                return await ctx.send(
                    _("You don't own an item with the ID `{itemid}`.").format(
                        itemid=itemid
                    )
                )

            olditems = await conn.fetch(
                "SELECT ai.* FROM profile p JOIN allitems ai ON (p.user=ai.owner) JOIN"
                " inventory i ON (ai.id=i.item) WHERE i.equipped IS TRUE AND"
                " p.user=$1;",
                ctx.author.id,
            )
            put_off = []
            if olditems:
                num_any = sum(1 for i in olditems if i["hand"] == "any")
                if len(olditems) == 1 and olditems[0]["hand"] == "both":
                    await conn.execute(
                        'UPDATE inventory SET "equipped"=False WHERE "item"=$1;',
                        olditems[0]["id"],
                    )
                    put_off = [olditems[0]["id"]]
                elif item["hand"] == "both":
                    all_ids = [i["id"] for i in olditems]
                    await conn.execute(
                        'UPDATE inventory SET "equipped"=False WHERE "item"=ANY($1);',
                        all_ids,
                    )
                    put_off = all_ids
                else:
                    if len(olditems) < 2:
                        if (
                                item["hand"] != "any"
                                and olditems[0]["hand"] == item["hand"]
                        ):
                            await conn.execute(
                                'UPDATE inventory SET "equipped"=False WHERE'
                                ' "item"=$1;',
                                olditems[0]["id"],
                            )
                            put_off = [olditems[0]["id"]]
                    elif (
                            item["hand"] == "left" or item["hand"] == "right"
                    ) and num_any < 2:
                        item_to_remove = [
                            i for i in olditems if i["hand"] == item["hand"]
                        ]
                        if not item_to_remove:
                            item_to_remove = [i for i in olditems if i["hand"] == "any"]
                        item_to_remove = item_to_remove[0]["id"]
                        await conn.execute(
                            'UPDATE inventory SET "equipped"=False WHERE "item"=$1;',
                            item_to_remove,
                        )
                        put_off = [item_to_remove]
                    else:
                        item_to_remove = await self.bot.paginator.Choose(
                            title=_("Select an item to unequip"),
                            return_index=True,
                            entries=[
                                f"{i['name']}, {i['type']}, {i['damage'] + i['armor']}"
                                for i in olditems
                            ],
                            choices=[i["name"] for i in olditems],
                        ).paginate(ctx)
                        item_to_remove = olditems[item_to_remove]["id"]
                        await conn.execute(
                            'UPDATE inventory SET "equipped"=False WHERE "item"=$1;',
                            item_to_remove,
                        )
                        put_off = [item_to_remove]
            await conn.execute(
                'UPDATE inventory SET "equipped"=True WHERE "item"=$1;', itemid
            )
        await self.bot.reset_cooldown(ctx)
        if put_off:
            await ctx.send(
                _(
                    "Successfully equipped item `{itemid}` and put off item(s)"
                    " {olditems}."
                ).format(
                    olditems=", ".join(f"`{i}`" for i in put_off), itemid=item["id"]
                )
            )
        else:
            await ctx.send(
                _("Successfully equipped item `{itemid}`.").format(itemid=itemid)
            )

    @commands.group(name="preset", invoke_without_command=True)
    async def preset_cmd(self, ctx):
        """
        Base command group for preset operations.
        Usage:
          $preset create <preset_name>
          $preset use <preset_name>
          $preset list
          $preset delete <preset_name>
        """
        await ctx.send("Use `$preset create|use|list|delete` ...")

    @preset_cmd.command(name="create")
    async def preset_create(self, ctx, preset_id: str):
        """
        Creates or overwrites a preset using your currently equipped items and amulet.
        Enforces a maximum of 5 total presets per user.
        Usage:
            $preset create raid_loadout
        """
        async with self.bot.pool.acquire() as conn:
            # 1) Grab currently equipped items
            rows = await conn.fetch(
                """
                SELECT i.item
                  FROM profile p
                  JOIN allitems ai ON (p.user = ai.owner)
                  JOIN inventory i ON (ai.id = i.item)
                 WHERE i.equipped = TRUE
                   AND p.user = $1
                """,
                ctx.author.id
            )

            # 1b) Grab currently equipped amulet (if any)
            amulet_row = await conn.fetchrow(
                """
                SELECT id
                  FROM amulets
                 WHERE user_id = $1
                   AND equipped IS TRUE
                 ORDER BY id DESC
                 LIMIT 1;
                """,
                ctx.author.id,
            )

            if not rows and not amulet_row:
                return await ctx.send("You have no currently equipped items or amulet to save.")

            item_ids = [r["item"] for r in rows]
            if amulet_row:
                item_ids.append(self._encode_preset_amulet_id(amulet_row["id"]))

            # 2) Check how many presets the user currently has
            preset_count = await conn.fetchval(
                """
                SELECT COUNT(*) 
                  FROM presets
                 WHERE user_id = $1
                """,
                ctx.author.id
            )

            # 3) See if this preset already exists (overwrite scenario)
            existing_preset = await conn.fetchrow(
                """
                SELECT preset_id
                  FROM presets
                 WHERE user_id = $1
                   AND preset_id = $2
                """,
                ctx.author.id,
                preset_id
            )

            # If user is at max (5) and we are not overwriting an existing preset, block
            if preset_count >= 5 and not existing_preset:
                return await ctx.send(
                    "You already have 5 presets. Please delete one first or use the same name to overwrite."
                )

            # 4) Insert or update the preset in the DB
            await conn.execute(
                """
                INSERT INTO presets (user_id, preset_id, item_ids)
                VALUES ($1, $2, $3)
                ON CONFLICT (user_id, preset_id)
                DO UPDATE SET item_ids = EXCLUDED.item_ids;
                """,
                ctx.author.id,
                preset_id,
                item_ids
            )

        gear_item_ids, amulet_id = self._split_preset_item_ids(item_ids)
        gear_items_text = ", ".join(map(str, gear_item_ids)) if gear_item_ids else "(none)"
        amulet_text = str(amulet_id) if amulet_id is not None else "none"

        await ctx.send(
            f"Preset **{preset_id}** saved.\nGear IDs: {gear_items_text}\nAmulet ID: {amulet_text}"
        )

    @preset_cmd.command(name="use")
    async def preset_use(self, ctx, preset_id: str):
        """
        Equips all items saved in the specified preset.
        Automatically unequips currently equipped items.
        Usage: $preset use sword_shield
        """
        # 1) Fetch preset
        async with self.bot.pool.acquire() as conn:
            # Get the preset
            record = await conn.fetchrow(
                """
                SELECT item_ids
                  FROM presets
                 WHERE user_id = $1
                   AND preset_id = $2
                """,
                ctx.author.id,
                preset_id
            )
            if not record:
                return await ctx.send(f"You have no preset **{preset_id}** defined.")

            stored_item_ids = record["item_ids"]
            gear_item_ids, preset_amulet_id = self._split_preset_item_ids(stored_item_ids)
            if not gear_item_ids and preset_amulet_id is None:
                return await ctx.send(f"Preset **{preset_id}** has no items stored.")

            # 3) Check ownership of new items
            if gear_item_ids:
                owned_rows = await conn.fetch(
                    """
                    SELECT i.item, ai.type
                      FROM inventory i
                      JOIN allitems ai ON (i.item = ai.id)
                     WHERE ai.owner = $1
                       AND i.item = ANY($2::bigint[]);
                    """,
                    ctx.author.id,
                    gear_item_ids,
                )
                owned_item_ids = {r["item"] for r in owned_rows}
                missing = set(gear_item_ids) - owned_item_ids
                if missing:
                    return await ctx.send(
                        f"You no longer own these item(s): {', '.join(map(str, missing))}"
                    )

            preset_amulet = None
            if preset_amulet_id is not None:
                preset_amulet = await conn.fetchrow(
                    """
                    SELECT id, type, tier
                      FROM amulets
                     WHERE user_id = $1
                       AND id = $2;
                    """,
                    ctx.author.id,
                    preset_amulet_id,
                )
                if not preset_amulet:
                    return await ctx.send(
                        f"You no longer own the amulet saved in this preset (ID: {preset_amulet_id})."
                    )

            # 4) Begin transaction
            async with conn.transaction():
                # 5) Unequip all currently equipped items for this owner.
                await conn.execute(
                    """
                    UPDATE inventory
                       SET equipped = FALSE
                     WHERE item IN (
                         SELECT i.item
                           FROM inventory i
                           JOIN allitems ai ON (i.item = ai.id)
                          WHERE ai.owner = $1
                            AND i.equipped IS TRUE
                     );
                    """,
                    ctx.author.id,
                )

                # 6) Equip every item saved in the preset exactly as stored.
                if gear_item_ids:
                    await conn.execute(
                        """
                        UPDATE inventory
                           SET equipped = TRUE
                         WHERE item = ANY($1::bigint[]);
                        """,
                        gear_item_ids,
                    )

                # 6b) Apply amulet state from the preset.
                await conn.execute(
                    """
                    UPDATE amulets
                       SET equipped = FALSE
                     WHERE user_id = $1
                       AND equipped IS TRUE;
                    """,
                    ctx.author.id,
                )

                if preset_amulet_id is not None:
                    await conn.execute(
                        """
                        UPDATE amulets
                           SET equipped = TRUE
                         WHERE user_id = $1
                           AND id = $2;
                        """,
                        ctx.author.id,
                        preset_amulet_id,
                    )

        # 7) Send success message with item details
        item_details = []
        for item_id in gear_item_ids:
            item = await self.bot.pool.fetchrow(
                "SELECT name, type FROM allitems WHERE id = $1", item_id
            )
            if item:
                item_details.append(f"- {item['name']} ({item['type']})")

        if preset_amulet:
            item_details.append(
                f"- Amulet ID {preset_amulet['id']} (Tier {preset_amulet['tier']} {preset_amulet['type'].title()})"
            )

        await ctx.send(
            f"✅ **Equipped preset {preset_id}:**\n" + "\n".join(item_details)[:1900]
        )

    @preset_cmd.command(name="list")
    async def preset_list(self, ctx):
        """
        Lists all of your saved presets.
        Usage: $preset list
        """
        async with self.bot.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT preset_id, item_ids
                  FROM presets
                 WHERE user_id = $1
                 ORDER BY preset_id
                """,
                ctx.author.id
            )

        if not rows:
            return await ctx.send("You have no presets saved.")

        lines = []
        for r in rows:
            pid = r["preset_id"]
            gear_item_ids, amulet_id = self._split_preset_item_ids(r["item_ids"])
            items_str = ", ".join(map(str, gear_item_ids)) if gear_item_ids else "(none)"
            amulet_str = str(amulet_id) if amulet_id is not None else "none"
            lines.append(f"**Preset {pid}:** Gear: {items_str} | Amulet: {amulet_str}")

        await ctx.send("\n".join(lines))

    @preset_cmd.command(name="delete", aliases=["remove"])
    async def preset_delete(self, ctx, preset_id: str):
        """
        Deletes a preset from your list.
        Usage: $preset delete sword_shield
        """
        async with self.bot.pool.acquire() as conn:
            result = await conn.execute(
                """
                DELETE FROM presets
                      WHERE user_id = $1
                        AND preset_id = $2
                """,
                ctx.author.id,
                preset_id
            )

        if "DELETE 0" in result:
            return await ctx.send(f"No preset **{preset_id}** existed.")
        else:
            await ctx.send(f"Preset **{preset_id}** has been deleted.")

    @checks.has_char()
    @commands.command(brief=_("Unequip an item"))
    @locale_doc
    async def unequip(self, ctx, itemid: int):
        _(
            """`<itemid>` - The ID of the item to unequip

            Unequip one of your equipped items. This has no benefit whatsoever."""
        )
        async with self.bot.pool.acquire() as conn:
            item = await conn.fetchrow(
                'SELECT * FROM inventory i JOIN allitems ai ON (i."item"=ai."id") WHERE'
                ' ai."owner"=$1 and ai."id"=$2;',
                ctx.author.id,
                itemid,
            )
            if not item:
                return await ctx.send(
                    _("You don't own an item with the ID `{itemid}`.").format(
                        itemid=itemid
                    )
                )
            if not item["equipped"]:
                return await ctx.send(_("You don't have this item equipped."))
            await conn.execute(
                'UPDATE inventory SET "equipped"=False WHERE "item"=$1;', itemid
            )
        await ctx.send(
            _("Successfully unequipped item `{itemid}`.").format(itemid=itemid)
        )

    @checks.has_char()
    @user_cooldown(3600)
    @commands.command(brief=_("Merge two items to make a stronger one"))
    @locale_doc
    async def merge(self, ctx, firstitemid: int, seconditemid: int):
        _(
            """`<firstitemid>` - The ID of the first item
            `<seconditemid>` - The ID of the second item

            Merges two items to a better one.

            ⚠ The first item will be upgraded by +1, the second item will be destroyed.

            The two items must be of the same item type and within a 5 stat range of each other.
            For example, if the first item is a 23 damage Scythe, the second item must be a Scythe with damage 18 to 28.

            One handed weapons can be merged up to 41, two handed items up to 82

            (This command has a cooldown of 1 hour.)"""
        )
        if firstitemid == seconditemid:
            await self.bot.reset_cooldown(ctx)
            return await ctx.send(_("Good luck with that."))
        async with self.bot.pool.acquire() as conn:
            item = await conn.fetchrow(
                'SELECT * FROM allitems WHERE "id"=$1 AND "owner"=$2;',
                firstitemid,
                ctx.author.id,
            )
            item2 = await conn.fetchrow(
                'SELECT * FROM allitems WHERE "id"=$1 AND "owner"=$2;',
                seconditemid,
                ctx.author.id,
            )
            if not item or not item2:
                await self.bot.reset_cooldown(ctx)
                return await ctx.send(_("You don't own both of these items."))
            if item["type"] != item2["type"]:
                await self.bot.reset_cooldown(ctx)
                return await ctx.send(
                    _(
                        "The items are of unequal type. You may only merge a sword with"
                        " a sword or a shield with a shield."
                    )
                )
            stat = "damage" if item["type"] != "Shield" else "armor"
            min_ = item[stat] - 5
            main = item[stat]
            main2 = item2[stat]
            max_ = item[stat] + 5
            main_hand = item["hand"]
            if (main > 60 and main_hand != "both") or (
                    main > 122 and main_hand == "both"
            ):
                await self.bot.reset_cooldown(ctx)
                return await ctx.send(
                    _("This item is already on the maximum upgrade level.")
                )
            if not min_ <= main2 <= max_:
                await self.bot.reset_cooldown(ctx)
                return await ctx.send(
                    _(
                        "The second item's stat must be in the range of `{min_}` to"
                        " `{max_}` to upgrade an item with the stat of `{stat}`."
                    ).format(min_=min_, max_=max_, stat=main)
                )
            await conn.execute(
                f'UPDATE allitems SET "{stat}"="{stat}"+1 WHERE "id"=$1;', firstitemid
            )
            await conn.execute('DELETE FROM inventory WHERE "item"=$1;', seconditemid)
            await conn.execute('DELETE FROM allitems WHERE "id"=$1;', seconditemid)
        await ctx.send(
            _(
                "The {stat} of your **{item}** is now **{newstat}**. The other item was"
                " destroyed."
            ).format(
                stat=stat, item=item["name"], newstat=main + 1
            )
        )

    @checks.has_char()
    @user_cooldown(3600)
    @commands.command(aliases=["upgrade"], brief=_("Upgrade an item"))
    @locale_doc
    async def upgradeweapon(self, ctx, itemid: int):
        _(
            """`<itemid>` - The ID of the item to upgrade

            Upgrades an item's stat by 1.
            The price to upgrade an item is 250 times its current stat. For example, upgrading a 15 damage sword will cost $3,750.

            One handed weapons can be upgraded up to 41, two handed items up to 82.

            (This command has a cooldown of 1 hour.)"""
        )
        async with self.bot.pool.acquire() as conn:
            item = await conn.fetchrow(
                'SELECT * FROM allitems WHERE "id"=$1 AND "owner"=$2;',
                itemid,
                ctx.author.id,
            )
            if not item:
                await self.bot.reset_cooldown(ctx)
                return await ctx.send(
                    _("You don't own an item with the ID `{itemid}`.").format(
                        itemid=itemid
                    )
                )
            if item["type"] != "Shield":
                stattoupgrade = "damage"
                pricetopay = int(item["damage"] * 1500)
            elif item["type"] == "Shield":
                stattoupgrade = "armor"
                pricetopay = int(item["armor"] * 1500)
            stat = int(item[stattoupgrade])
            hand = item["hand"]
            if (stat > 60 and hand != "both") or (stat > 122 and hand == "both"):
                await self.bot.reset_cooldown(ctx)
                return await ctx.send(
                    _("Your weapon already reached the maximum upgrade level.")
                )

        if not await ctx.confirm(
                _(
                    "Are you sure you want to upgrade this item: {item}? It will cost"
                    " **${pricetopay}**."
                ).format(
                    item=item["name"], pricetopay=pricetopay
                )
        ):
            return await ctx.send(_("Weapon upgrade cancelled."))
        if not await checks.has_money(self.bot, ctx.author.id, pricetopay):
            await self.bot.reset_cooldown(ctx)
            return await ctx.send(
                _(
                    "You are too poor to upgrade this item. The upgrade costs"
                    " **${pricetopay}**, but you only have **${money}**."
                ).format(
                    pricetopay=pricetopay, money=ctx.character_data["money"]
                )
            )
        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                f'UPDATE allitems SET {stattoupgrade}={stattoupgrade}+1 WHERE "id"=$1;',
                itemid,
            )
            await conn.execute(
                'UPDATE profile SET "money"="money"-$1 WHERE "user"=$2;',
                pricetopay,
                ctx.author.id,
            )
            await self.bot.log_transaction(
                ctx,
                from_=ctx.author.id,
                to=2,
                subject="Upgrade",
                data={"Gold": pricetopay},
                conn=conn,
            )
        await ctx.send(
            _(
                "The {stat} of your **{item}** is now **{newstat}**. **${pricetopay}**"
                " has been taken off your balance."
            ).format(
                stat=stattoupgrade,
                item=item["name"],
                newstat=int(item[stattoupgrade]) + 1,
                pricetopay=pricetopay,
            )
        )

    @checks.has_char()
    @commands.command(brief=_("Give someone money"))
    @locale_doc
    async def give(
            self, ctx, money, other: MemberWithCharacter
    ):
        _(
            """`<money>` - The amount of money to give to the other person, cannot exceed 100,000,000
            `[other]` - The person to give the money to

            Gift money! It will be removed from you and added to the other person."""
        )

        if money == "all":
            money = int(ctx.character_data["money"])

        else:
            try:
                money = int(money)
            except Exception as e:
                return await ctx.send("You used a malformed argument!")
        if money < 1:
            return await ctx.send("The supplied number must be greater than 0.")

        if other == ctx.author:
            return await ctx.send(_("No cheating!"))
        elif other == ctx.me:
            return await ctx.send(
                _("For me? I'm flattered, but I can't accept this...")
            )
        if ctx.character_data["money"] < money:
            return await ctx.send(_("You are too poor."))
        async with self.bot.pool.acquire() as conn:
            authormoney = await conn.fetchval(
                'UPDATE profile SET "money"="money"-$1 WHERE "user"=$2 RETURNING'
                ' "money";',
                money,
                ctx.author.id,
            )
            othermoney = await conn.fetchval(
                'UPDATE profile SET "money"="money"+$1 WHERE "user"=$2 RETURNING'
                ' "money";',
                money,
                other.id,
            )
            await self.bot.log_transaction(
                ctx,
                from_=ctx.author,
                to=other,
                subject="give money",
                data={"Gold": money},
                conn=conn,
            )
        await ctx.send(
            _(
                "Success!\n{other} now has **${othermoney}**, you now have"
                " **${authormoney}**."
            ).format(
                other=other.mention, othermoney=othermoney, authormoney=authormoney
            )
        )

    @checks.has_char()
    @commands.command(brief=_("Rename your character"))
    @locale_doc
    async def rename(self, ctx, *, name: str = None):
        _(
            """`[name]` - The name to use; if not given, this will be interactive

            Renames your character. The name must be from 3 to 20 characters long."""
        )
        if not name:
            await ctx.send(
                _(
                    "What shall your character's name be? (Minimum 3 Characters,"
                    " Maximum 20)"
                )
            )

            def mycheck(amsg):
                return amsg.author == ctx.author

            try:
                name = await self.bot.wait_for("message", timeout=60, check=mycheck)
            except asyncio.TimeoutError:
                return await ctx.send(_("Timeout expired. Retry!"))
            name = name.content
        if len(name) > 2 and len(name) < 21:
            if "`" in name:
                return await ctx.send(
                    _(
                        "Illegal character (`) found in the name. Please try again and"
                        " choose another name."
                    )
                )
            await self.bot.pool.execute(
                'UPDATE profile SET "name"=$1 WHERE "user"=$2;', name, ctx.author.id
            )
            await ctx.send(_("Character name updated."))
        elif len(name) < 3:
            await ctx.send(_("Character names must be at least 3 characters!"))
        elif len(name) > 20:
            await ctx.send(_("Character names mustn't exceed 20 characters!"))

    @checks.has_char()
    @commands.command(aliases=["rm", "del"], brief=_("Delete your character"))
    @locale_doc
    async def delete(self, ctx):
        _(
            """Deletes your character. There is no way to get your character data back after deletion.

            Deleting your character also removes:
              - Your guild if you own one
              - Your alliance's city ownership
              - Your marriage and children"""
        )
        try:
            if not await ctx.confirm(
                    _(
                        "Are you absolutely sure you want to delete your character? React in"
                        " the next 30 seconds to confirm.\n**This cannot be undone.**"
                    )
            ):
                return await ctx.send(_("Cancelled deletion of your character."))
            async with self.bot.pool.acquire() as conn:
                g = await conn.fetchval(
                    'DELETE FROM guild WHERE "leader"=$1 RETURNING "id";', ctx.author.id
                )
                if g:
                    await conn.execute(
                        'UPDATE profile SET "guildrank"=$1, "guild"=$2 WHERE "guild"=$3;',
                        "Member",
                        0,
                        g,
                    )
                    await conn.execute('UPDATE city SET "owner"=1 WHERE "owner"=$1;', g)
                if partner := ctx.character_data["marriage"]:
                    await conn.execute(
                        'UPDATE profile SET "marriage"=$1 WHERE "user"=$2;',
                        0,
                        partner,
                    )
                await conn.execute(
                    'UPDATE children SET "mother"=$1, "father"=0 WHERE ("father"=$1 AND'
                    ' "mother"=$2) OR ("father"=$2 AND "mother"=$1);',
                    partner,
                    ctx.author.id,
                )
                await self.bot.delete_profile(ctx.author.id, conn=conn)
            await self.bot.delete_adventure(ctx.author)
            await ctx.send(
                _("Successfully deleted your character. Sorry to see you go :frowning:")
            )
        except Exception as e:
            await ctx.send(e)


    @checks.has_char()
    @commands.command(aliases=["color"], brief=_("Update your profile color"))
    @locale_doc
    async def colour(self, ctx, *, colour: str):
        _(
            """`<color>` - The color to use, see below for allowed format

            Sets your profile text colour. The format may be #RGB, #RRGGBB, CSS3 defaults like "cyan", a rgb(r, g, b) tuple or a rgba(r, g, b, a) tuple

            A tuple is a data type consisting of multiple parts. To make a tuple for this command, seperate your values with a comma, and surround them with parantheses.
            Here is an example of a tuple with four values: `(128,256,0,0.5)`

            This will change the text color in `{prefix}profile` and the embed color in `{prefix}profile2`."""
        )
        try:
            rgba = colors.parse(colour)
        except ValueError:
            return await ctx.send(
                _(
                    "Format for colour is `#RGB`, `#RRGGBB`, a colour code like `cyan`"
                    " or rgb/rgba values like (255, 255, 255, 0.5)."
                )
            )
        await self.bot.pool.execute(
            'UPDATE profile SET "colour"=$1 WHERE "user"=$2;',
            (rgba.red, rgba.green, rgba.blue, rgba.alpha),
            ctx.author.id,
        )
        await ctx.send(
            _("Successfully set your profile colour to `{colour}`.").format(
                colour=colour
            )
        )

    @checks.has_char()
    @commands.command(brief=_("Claim your profile badges"))
    @locale_doc
    async def claimbadges(self, ctx: Context) -> None:
        _(
            """Claim all badges for your profile based on your roles. This command can only be used in the support server."""
        )
        if not ctx.guild or ctx.guild.id != self.bot.config.game.support_server_id:
            await ctx.send(_("This command can only be used in the support server."))
            return

        roles = {
            "Contributor": Badge.CONTRIBUTOR,
            "Designer": Badge.DESIGNER,
            "Developer": Badge.DEVELOPER,
            "Game Designer": Badge.GAME_DESIGNER,
            "Game Masters": Badge.GAME_MASTER,
            "Support Team": Badge.SUPPORT,
            "Betasquad": Badge.TESTER,
            "Veterans": Badge.VETERAN,
        }

        badges = None

        for role in ctx.author.roles:
            if (badge := roles.get(role.name)) is not None:
                if badges is None:
                    badges = badge
                else:
                    badges |= badge

        if badges is not None:
            await self.bot.pool.execute(
                'UPDATE profile SET "badges"=$1 WHERE "user"=$2;',
                badges.to_db(),
                ctx.author.id,
            )

        await ctx.send(_("Successfully updated your badges."))

    @commands.command(brief=_("Opt out of API data visibility"))
    @locale_doc
    async def optoutapi(self, ctx):
        _(
            """Opt out of API data visibility.
            
            This will hide your user ID in API responses by replacing it with 0.
            Your profile will also return "404 Not Found" when accessed directly via API.
            This helps protect your privacy while still allowing you to play normally."""
        )
        
        # Check if user already opted out
        async with self.bot.pool.acquire() as conn:
            existing = await conn.fetchval(
                "SELECT userid FROM optout WHERE userid = $1",
                ctx.author.id
            )
            
            if existing:
                return await ctx.send(
                    _("You are already opted out of API data visibility! Use `{prefix}optin` to opt back in.").format(prefix=ctx.prefix)
                )
            
            # Add user to optout table
            await conn.execute(
                "INSERT INTO optout (userid) VALUES ($1)",
                ctx.author.id
            )
        
        await ctx.send(
            _("✅ **Privacy Protection Enabled!**\n\n"
              "Your user ID will now be replaced with `0` in all API responses to protect your privacy.\n"
              "Your profile will return `404 Not Found` when accessed directly via the API.\n\n"
              "You can still play the game normally - this only affects API data visibility.\n"
              "Use `{prefix}optinapi` if you want to opt back in later.").format(prefix=ctx.prefix)
        )

    @commands.command(brief=_("Opt back into API data visibility"))
    @locale_doc
    async def optinapi(self, ctx):
        _(
            """Opt back into API data visibility.
            
            This will restore your user ID visibility in API responses.
            Your profile will be accessible again via direct API calls."""
        )
        
        # Check if user is actually opted out
        async with self.bot.pool.acquire() as conn:
            existing = await conn.fetchval(
                "SELECT userid FROM optout WHERE userid = $1",
                ctx.author.id
            )
            
            if not existing:
                return await ctx.send(
                    _("You are not currently opted out! Your data is already visible in the API.\nUse `{prefix}optout` to enable privacy protection.").format(prefix=ctx.prefix)
                )
            
            # Remove user from optout table
            await conn.execute(
                "DELETE FROM optout WHERE userid = $1",
                ctx.author.id
            )
        
        await ctx.send(
            _("✅ **Privacy Protection Disabled!**\n\n"
              "Your user ID will now be visible again in API responses.\n"
              "Your profile can be accessed directly via the API.\n\n"
              "Use `{prefix}optoutapi` if you want to enable privacy protection again.").format(prefix=ctx.prefix)
        )


async def setup(bot):
    await bot.add_cog(Profile(bot))
