import discord
from discord.ext import commands
from discord import ui
from datetime import datetime, timedelta
from typing import Optional
import asyncio

from utils.checks import is_gm

# The big list of all valid subjects
VALID_SUBJECTS = [
    "gambling BJ", "Pet Item Fetch", "Active Battle Bet", "guild invest", "Family Event",
    "daily", "Level Up!", "shop buy", "guild pay", "item", "Pet Purchase", "exchange", "item = OFFER",
    "vote", "crates", "shop buy - bot give", "Tournament Prize", "gambling BJ-Insurance",
    "Battle Bet", "spoil", "FamilyEvent Crate", "FamilyEvent Money", "RaidBattle Bet",
    "Raid Stats Upgrade DEF", "crate open item", "raid bid winner", "gambling roulette",
    "crates offercrate", "Starting out", "money", "class change", "give money", "gambling coinflip",
    "adventure", "Raid Stats Upgrade ATK", "AA Reward", "bid", "crates trade", "steal",
    "Raid Stats Upgrade HEALTH", "Torunament Winner", "buy boosters", "merch", "offer",
    "alliance", "sacrifice", "gambling", "Memorial Item", "shop"
]


class UserSelectModal(ui.Modal, title='User Filter'):
    """Modal that asks for username/ID, then updates the parent view with the user filter."""
    def __init__(self, bot):
        super().__init__()
        self.bot = bot
        self.user = None

    user_input = ui.TextInput(
        label='Enter Username, ID, or mention',
        placeholder='Example: johndoe#1234 or 123456789',
        required=False
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not self.user_input.value:
            self.user = None
            await interaction.response.send_message(
                "Filter cleared - showing all users.",
                ephemeral=True
            )
            return

        try:
            # Try different methods to find the user
            user_input = self.user_input.value.strip()

            # Remove mention formatting if present
            user_input = user_input.replace('<@!', '').replace('<@', '').replace('>', '')

            # Try to find by ID first
            if user_input.isdigit():
                self.user = await self.bot.fetch_user(int(user_input))
            else:
                # Try to find by name#discriminator
                if '#' in user_input:
                    name, discrim = user_input.rsplit('#', 1)
                    self.user = discord.utils.get(self.bot.users, name=name, discriminator=discrim)
                else:
                    # Try to find by name only (will take the first match)
                    self.user = discord.utils.get(self.bot.users, name=user_input)

            if self.user:
                await interaction.response.send_message(
                    f"Filter set to user: {self.user.name}",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "Could not find that user. Please try again with a valid username or ID.",
                    ephemeral=True
                )
        except Exception as e:
            await interaction.response.send_message(
                f"Error finding user: {str(e)}. Please try again.",
                ephemeral=True
            )


class SubjectSelectView(ui.View):
    """
    A paginated view that shows a large list of valid subjects in a Select menu.
    When the user picks a subject, we pass it back to the original TransactionView.
    """
    def __init__(self, parent_view, interaction_user: discord.User, valid_subjects, per_page=25):
        super().__init__(timeout=60)
        self.parent_view = parent_view
        self.interaction_user = interaction_user
        self.valid_subjects = valid_subjects
        self.per_page = per_page
        self.current_page = 0
        self.pages = [
            valid_subjects[i:i + per_page] for i in range(0, len(valid_subjects), per_page)
        ]
        self.message: Optional[discord.WebhookMessage] = None

        # Create the initial select menu for the current page:
        self.subject_select = ui.Select(
            placeholder="Choose a subject...",
            min_values=1,
            max_values=1,
            options=self._build_options_for_page(self.current_page)
        )
        self.subject_select.callback = self.select_callback
        self.add_item(self.subject_select)

    def _build_options_for_page(self, page_index):
        options = []
        for subject in self.pages[page_index]:
            options.append(discord.SelectOption(label=subject, value=subject))
        return options

    async def select_callback(self, interaction: discord.Interaction):
        # Only let the original user respond
        if interaction.user.id != self.interaction_user.id:
            await interaction.response.send_message(
                "You are not the user who opened this menu.",
                ephemeral=True
            )
            return

        chosen_subject = self.subject_select.values[0]  # The subject picked
        self.parent_view.subject = chosen_subject
        # Reset the main transaction view to page 0
        self.parent_view.current_page = 0

        # Refresh the main transaction view
        # We defer first, then call refresh so we can do a DB lookup safely:
        await interaction.response.defer(ephemeral=True)
        await self.parent_view.refresh_view(interaction, deferred=True)

        # Now remove this ephemeral subject picker (so user can’t interact again):
        # Because we deferred, we can do an edit_original_response:
        await interaction.delete_original_response()

    @ui.button(label="◀ Prev", style=discord.ButtonStyle.gray, row=1)
    async def previous_page(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.interaction_user.id:
            return
        self.current_page = max(0, self.current_page - 1)
        await self._update_select(interaction)

    @ui.button(label="Next ▶", style=discord.ButtonStyle.gray, row=1)
    async def next_page(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.interaction_user.id:
            return
        self.current_page = min(len(self.pages) - 1, self.current_page + 1)
        await self._update_select(interaction)

    @ui.button(label="Cancel", style=discord.ButtonStyle.danger, row=1)
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.interaction_user.id:
            return
        await interaction.delete_original_response()

    async def _update_select(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        self.subject_select.options = self._build_options_for_page(self.current_page)
        await interaction.edit_original_response(
            content=f"Select a subject (Page {self.current_page + 1}/{len(self.pages)})",
            view=self
        )

    async def on_timeout(self):
        # If user doesn't interact within 60s, we remove this ephemeral (if still around)
        if self.message:
            try:
                await self.message.delete()
            except:
                pass


class TransactionView(ui.View):
    def __init__(self, ctx, user1: discord.User):
        # Increase the timeout so the View doesn't die too soon.
        super().__init__(timeout=300)  # 5-minute timeout instead of 60s
        self.ctx = ctx
        self.user1 = user1
        self.user2 = None
        self.subject = "all"
        self.start_date = None
        self.end_date = None
        self.current_page = 0
        self.message: Optional[discord.Message] = None

    @ui.select(
        placeholder="Select transaction category",
        options=[
            discord.SelectOption(label="All Transactions", value="all", emoji="📋"),
            discord.SelectOption(label="Gambling", value="gambling", emoji="🎲"),
            discord.SelectOption(label="Shop", value="shop", emoji="🛍️"),
            discord.SelectOption(label="Trading", value="item", emoji="🔄"),
            discord.SelectOption(label="Guild", value="guild", emoji="⚔️"),
            discord.SelectOption(label="Battle", value="Battle", emoji="⚔️"),
            discord.SelectOption(label="Events", value="Family Event", emoji="🎉"),
            discord.SelectOption(label="Daily/Vote", value="daily", emoji="📅"),
        ],
        row=0
    )
    async def select_subject(self, interaction: discord.Interaction, select: ui.Select):
        """High-level category filter."""
        # Defer the response to avoid short time-limit issues
        await interaction.response.defer()
        self.subject = select.values[0]
        self.current_page = 0
        await self.refresh_view(interaction, deferred=True)

    @ui.select(
        placeholder="Select time period",
        options=[
            discord.SelectOption(label="All Time", value="all", emoji="♾️"),
            discord.SelectOption(label="Last 24 Hours", value="24h", emoji="⏰"),
            discord.SelectOption(label="Last Week", value="7d", emoji="📅"),
            discord.SelectOption(label="Last Month", value="30d", emoji="📆"),
            discord.SelectOption(label="Last 3 Months", value="90d", emoji="🗓️"),
        ],
        row=1
    )
    async def select_timeframe(self, interaction: discord.Interaction, select: ui.Select):
        await interaction.response.defer()
        now = datetime.utcnow()
        if select.values[0] == "all":
            self.start_date = None
            self.end_date = None
        else:
            days_map = {"24h": 1, "7d": 7, "30d": 30, "90d": 90}
            self.start_date = now - timedelta(days=days_map[select.values[0]])
            self.end_date = now

        self.current_page = 0
        await self.refresh_view(interaction, deferred=True)

    @ui.button(label="◀", style=discord.ButtonStyle.gray, row=2)
    async def previous_page(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer()
        self.current_page = max(0, self.current_page - 1)
        await self.refresh_view(interaction, deferred=True)

    @ui.button(label="▶", style=discord.ButtonStyle.gray, row=2)
    async def next_page(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer()
        self.current_page += 1
        await self.refresh_view(interaction, deferred=True)

    @ui.button(label="🔍 Filter User", style=discord.ButtonStyle.primary, row=2)
    async def filter_user(self, interaction: discord.Interaction, button: ui.Button):
        # Show a modal to get the user. We don't do a DB fetch, so ephemeral is safe.
        modal = UserSelectModal(self.ctx.bot)
        await interaction.response.send_modal(modal)

        # Wait for the modal to complete
        await modal.wait()

        # If user has been set, refresh
        if modal.user:
            await interaction.followup.defer()
            self.user2 = modal.user
            self.current_page = 0
            await self.refresh_view(interaction, deferred=True)
        else:
            # If user cleared or we couldn't find one, also refresh
            await interaction.followup.defer()
            self.user2 = None
            self.current_page = 0
            await self.refresh_view(interaction, deferred=True)

    @ui.button(label="❌ Clear Filter", style=discord.ButtonStyle.danger, row=2)
    async def clear_filter(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer()
        self.user2 = None
        self.subject = "all"
        self.current_page = 0
        self.start_date = None
        self.end_date = None
        await self.refresh_view(interaction, deferred=True)

    @ui.button(label="🔍 Filter Subject", style=discord.ButtonStyle.secondary, row=3)
    async def filter_subject(self, interaction: discord.Interaction, button: ui.Button):
        """
        Opens a secondary ephemeral View that lets the user pick from the full list of VALID_SUBJECTS.
        """
        await interaction.response.defer(ephemeral=True)
        view = SubjectSelectView(
            parent_view=self,
            interaction_user=interaction.user,
            valid_subjects=VALID_SUBJECTS
        )
        # ephemeral message
        msg = await interaction.followup.send(
            content=f"Select a subject (Page 1/{len(view.pages)})",
            view=view,
            ephemeral=True
        )
        view.message = msg

    @ui.button(label="🗑️ Close", style=discord.ButtonStyle.secondary, row=3)
    async def cleanup_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer()
        if self.message:
            await self.message.delete()

    async def on_timeout(self):
        """Called when the view times out (after 300s of inactivity in this code)."""
        try:
            for item in self.children:
                item.disabled = True
            if self.message and self.message.embeds:
                embed = self.message.embeds[0]
                embed.set_footer(text="This menu has timed out. Use the command again to view transactions.")
                await self.message.edit(view=self, embed=embed)
        except discord.NotFound:
            pass

    async def refresh_view(self, interaction: discord.Interaction, deferred: bool = False):
        """
        Re-query the database and re-render the embed for the current page of results.
        If `deferred=True`, we call `interaction.edit_original_response`, else we do `response.edit_message`.
        """
        try:
            async with self.ctx.bot.pool.acquire() as connection:
                # Build base query
                params = []
                param_index = 1

                if self.user2:
                    # Filter between two specific users
                    query = """
                        SELECT * FROM transactions
                        WHERE (( "from" = $1 AND "to" = $2 ) OR ( "from" = $2 AND "to" = $1 ))
                    """
                    params.extend([self.user1.id, self.user2.id])
                    param_index += 2
                else:
                    # All transactions for one user
                    query = """
                        SELECT * FROM transactions
                        WHERE ("from" = $1 OR "to" = $1)
                    """
                    params.append(self.user1.id)
                    param_index += 1

                # subject vs category
                if self.subject != "all":
                    if self.subject in VALID_SUBJECTS:
                        # exact match
                        query += f" AND subject = ${param_index}"
                        params.append(self.subject)
                        param_index += 1
                    else:
                        # partial/LIKE match
                        query += f" AND subject ILIKE ${param_index}"
                        params.append(f"%{self.subject}%")
                        param_index += 1

                # date filters
                if self.start_date:
                    query += f" AND timestamp >= ${param_index}"
                    params.append(self.start_date)
                    param_index += 1
                if self.end_date:
                    query += f" AND timestamp <= ${param_index}"
                    params.append(self.end_date)
                    param_index += 1

                query += " ORDER BY timestamp DESC"

                transactions = await connection.fetch(query, *params)

                if not transactions:
                    embed = discord.Embed(
                        title="Transaction History",
                        color=discord.Color.red()
                    )
                    if self.user2:
                        user_filter = f"📊 Transactions between **{self.user1.name}** and **{self.user2.name}**"
                    else:
                        user_filter = f"📊 All transactions for **{self.user1.name}**"

                    embed.description = (
                        f"{user_filter}\n\n"
                        "❌ No transactions found matching the criteria."
                    )
                    # Update the message
                    if deferred:
                        await interaction.edit_original_response(embed=embed, view=self)
                    else:
                        await interaction.response.edit_message(embed=embed, view=self)
                    return

                # Pagination
                per_page = 5
                pages = [transactions[i:i + per_page] for i in range(0, len(transactions), per_page)]
                if self.current_page >= len(pages):
                    self.current_page = len(pages) - 1  # clamp to last page

                current_transactions = pages[self.current_page]

                embed = discord.Embed(
                    title="Transaction History",
                    color=discord.Color.blurple()
                )

                # Create header
                if self.user2:
                    user_filter = f"📊 Transactions between **{self.user1.name}** and **{self.user2.name}**"
                else:
                    user_filter = f"📊 All transactions for **{self.user1.name}**"

                filter_info = []
                if self.subject != "all":
                    filter_info.append(f"Type: {self.subject}")
                if self.start_date:
                    date_str = f"{self.start_date.strftime('%Y-%m-%d')} to {self.end_date.strftime('%Y-%m-%d')}"
                    filter_info.append(f"Period: {date_str}")

                embed.description = f"{user_filter}\n"
                if filter_info:
                    embed.description += f"**Active Filters:** {' | '.join(filter_info)}\n"

                # Add transaction fields
                for transaction in current_transactions:
                    # Attempt to fetch 'from' user
                    try:
                        from_user = await self.ctx.bot.fetch_user(transaction['from'])
                        from_name = from_user.name if from_user else f"Unknown ({transaction['from']})"
                    except discord.NotFound:
                        from_name = f"Unknown ({transaction['from']})"

                    # Attempt to fetch 'to' user
                    try:
                        to_user = await self.ctx.bot.fetch_user(transaction['to'])
                        to_name = to_user.name if to_user else f"Unknown ({transaction['to']})"
                    except discord.NotFound:
                        to_name = f"Unknown ({transaction['to']})"

                    embed.add_field(
                        name="Transaction",
                        value=(
                            f"**From:** {from_name}\n"
                            f"**To:** {to_name}\n"
                            f"**Type:** {transaction['subject']}\n"
                            f"**Info:** {transaction.get('info', 'N/A')}\n"
                            f"**Time:** {transaction['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}"
                        ),
                        inline=False
                    )

                total_pages = len(pages)
                embed.set_footer(
                    text=(
                        f"Page {self.current_page + 1}/{total_pages} • "
                        "Menu times out after 5m of inactivity."
                    )
                )

                # Finally, edit the existing message
                if deferred:
                    await interaction.edit_original_response(embed=embed, view=self)
                else:
                    await interaction.response.edit_message(embed=embed, view=self)

        except Exception as e:
            # If something goes wrong (DB error, etc.), show an error
            embed = discord.Embed(
                title="Error",
                description=f"An error occurred: {str(e)}",
                color=discord.Color.red()
            )
            # Must handle whether we already deferred
            if deferred:
                await interaction.edit_original_response(embed=embed, view=self)
            else:
                await interaction.response.edit_message(embed=embed, view=self)


class TransactionsCog(commands.Cog):
    """A cog for viewing and managing transaction history."""

    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="transactions", aliases=["trans"], hidden=True)
    @is_gm()
    async def view_transactions(self, ctx, user: Optional[discord.User] = None):
        """
        View transaction history with an interactive interface.

        Usage:
        !transactions [optional: @user]
        !trans [optional: @user]

        Examples:
        !transactions - View your own transactions
        !transactions @user - View transactions for a specific user
        """
        target_user = user or ctx.author
        view = TransactionView(ctx, target_user)
        embed = discord.Embed(
            title="Transaction History",
            description=(
                f"📊 Loading transactions for **{target_user.name}**...\n"
                "Use the dropdowns and buttons below to filter transactions.\n"
                "Use **Filter Subject** to see all valid subjects.\n"
                "Menu times out after 5 minutes of inactivity."
            ),
            color=discord.Color.blurple()
        )
        sent_msg = await ctx.send(embed=embed, view=view)
        view.message = sent_msg
        # Immediately do a first-time load
        # (not strictly required, but nice to show data right away)
        await view.refresh_view(
            interaction=await ctx.bot.get_application_context(sent_msg),
            deferred=False
        )


async def setup(bot):
    await bot.add_cog(TransactionsCog(bot))
