

"""
The IdleRPG Discord Bot
Copyright (C) 2018-2021 Diniboy and Gelbpunkt
Copyright (C) 2023-2024 Lunar (PrototypeX37)
Copyright (C) 2025 Danaelis

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
from dataclasses import dataclass
from typing import Optional, List

import asyncpg
import discord
from discord.ext import commands
from discord import Interaction
from discord.enums import ButtonStyle
from discord.ui import View, Button
from discord.http import handle_message_parameters

from classes.converters import IntGreaterThan, MemberWithCharacter
from utils.checks import has_char, is_gm
from utils.i18n import _, locale_doc

# ----------------------
# Configuration
# ----------------------
LOTTERY_CHANNEL_ID: int = 1404885918959140985
LOTTERY_PING_ROLE_ID: int = 1405909212424306729
HOUSE_CUT_PCT: int = 0
MONEY_TABLE = 'profile'
USER_COLUMN = 'user'
MONEY_COLUMN = 'money'

# ----------------------
# Data helpers
# ----------------------
@dataclass
class LotterySettings:
    max_tickets: int
    ticket_cost: int

async def fetch_settings(conn: asyncpg.Connection) -> Optional[LotterySettings]:
    row = await conn.fetchrow("SELECT maxtickets, ticketcost FROM lottodata")
    if not row:
        return None
    return LotterySettings(int(row['maxtickets']), int(row['ticketcost']))

async def fetch_user_tickets(conn: asyncpg.Connection, user_id: int) -> int:
    val = await conn.fetchval("SELECT tickets FROM lottery WHERE id=$1", user_id)
    return int(val or 0)

async def sum_total_tickets(conn: asyncpg.Connection) -> int:
    val = await conn.fetchval("SELECT COALESCE(SUM(tickets),0) FROM lottery")
    return int(val or 0)

async def upsert_user_tickets(conn: asyncpg.Connection, user_id: int, delta: int) -> int:
    current = await fetch_user_tickets(conn, user_id)
    new_amount = current + int(delta)
    if current == 0 and delta > 0:
        await conn.execute("INSERT INTO lottery (id, tickets) VALUES ($1,$2)", user_id, new_amount)
    else:
        await conn.execute("UPDATE lottery SET tickets=$1 WHERE id=$2", new_amount, user_id)
    return new_amount

async def credit_user_money(conn: asyncpg.Connection, user_id: int, amount: int):
    updated = await conn.fetchval(
        f'UPDATE "{MONEY_TABLE}" SET "{MONEY_COLUMN}"=COALESCE("{MONEY_COLUMN}",0)+$1 WHERE "{USER_COLUMN}"=$2 RETURNING 1;',
        amount, user_id)
    if updated is None:
        try:
            await conn.execute(f'INSERT INTO "{MONEY_TABLE}" ("{USER_COLUMN}","{MONEY_COLUMN}") VALUES ($1,0);', user_id)
        except Exception:
            pass
        await conn.execute(
            f'UPDATE "{MONEY_TABLE}" SET "{MONEY_COLUMN}"=COALESCE("{MONEY_COLUMN}",0)+$1 WHERE "{USER_COLUMN}"=$2;',
            amount, user_id)

async def debit_user_money(conn: asyncpg.Connection, user_id: int, amount: int):
    await conn.execute(
        f'UPDATE "{MONEY_TABLE}" SET "{MONEY_COLUMN}"=COALESCE("{MONEY_COLUMN}",0)-$1 WHERE "{USER_COLUMN}"=$2;',
        amount, user_id)

# ----------------------
# Confirmation view
# ----------------------
class ConfirmView(View):
    def __init__(self, author_id: int, timeout: float = 30.0):
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.value: Optional[bool] = None

    async def interaction_check(self, interaction: Interaction) -> bool:
        return interaction.user and interaction.user.id == self.author_id

    @discord.ui.button(label=_('Confirm'), style=ButtonStyle.success)
    async def confirm(self, interaction: Interaction, button: Button):
        self.value = True
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label=_('Cancel'), style=ButtonStyle.danger)
    async def cancel(self, interaction: Interaction, button: Button):
        self.value = False
        await interaction.response.defer()
        self.stop()

# ----------------------
# Cog
# ----------------------
class Lottery(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # GM: Start lotto
    @commands.command(hidden=True)
    @is_gm()
    async def gmlotto(self, ctx, amount: Optional[int]=None, tickets: Optional[int]=None):
        if amount is None or tickets is None:
            return await ctx.send('Usage: `$gmlotto <ticket_cost> <max_tickets>`')
        amount=int(amount); tickets=int(tickets)
        async with self.bot.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute('DELETE FROM lottodata')
                await conn.execute('INSERT INTO lottodata(maxtickets,ticketcost) VALUES($1,$2)', tickets, amount)
        await ctx.send(f'Lottery updated: ticket ${amount}, max {tickets}')
        channel=self.bot.get_channel(LOTTERY_CHANNEL_ID)
        if channel:
            embed=discord.Embed(title='Weekly Lotto Started!',
                description=(f'**Ticket Cost:** ${amount}\n'
                             f'**Max Tickets per Player:** {tickets}\n\n'
                             'Use:\n'
                             '`$lotto` — view info\n'
                             '`$lotto buy <num>` — buy tickets'),
                color=discord.Color.green())
            await channel.send(embed=embed)

    # Player lotto
    @commands.command()
    @has_char()
    @locale_doc
    async def lotto(self, ctx, subcommand: Optional[str]=None, num_tickets: Optional[int]=None):
        _(
            """Participate in the lottery by buying tickets or viewing information.

**Usage:**
- `$lotto`: View current lottery settings and your ticket count.
- `$lotto buy <num_tickets>`: Purchase lottery tickets.

**Notes:** Max tickets per player apply; you must have enough money to buy.
"""
        )
        try:
            async with self.bot.pool.acquire() as conn:
                settings = await fetch_settings(conn)
            if not settings:
                return await ctx.send('No lottery is currently running.')

            if (subcommand or '').lower()=='buy':
                if not num_tickets or num_tickets<=0:
                    return await ctx.send('Please provide a positive number of tickets.')
                async with self.bot.pool.acquire() as conn:
                    current=await fetch_user_tickets(conn, ctx.author.id)
                if current+num_tickets>settings.max_tickets:
                    return await ctx.send(f'Max {settings.max_tickets} tickets; you have {current}.')
                total_cost=num_tickets*settings.ticket_cost
                if ctx.character_data['money']<total_cost:
                    return await ctx.send(_('You are too poor.'))
                view=ConfirmView(author_id=ctx.author.id)
                msg=await ctx.send(f'Buy **{num_tickets}** tickets for **${total_cost}**? You have **{current}** now.',view=view)
                await view.wait(); await msg.edit(view=None)
                if not view.value:
                    return await ctx.send('Purchase cancelled.')
                async with self.bot.pool.acquire() as conn:
                    async with conn.transaction():
                        await debit_user_money(conn, ctx.author.id, total_cost)
                        updated=await upsert_user_tickets(conn, ctx.author.id, num_tickets)
                await ctx.send(f'You purchased {num_tickets} tickets. You now have **{updated}**.')
            else:
                async with self.bot.pool.acquire() as conn:
                    my=await fetch_user_tickets(conn, ctx.author.id)
                    total=await sum_total_tickets(conn)
                gross=total*settings.ticket_cost
                house=(gross*HOUSE_CUT_PCT)//100
                prize=gross-house
                embed=discord.Embed(title='Lottery Information',color=discord.Color.green())
                embed.add_field(name='Ticket Cost',value=f'${settings.ticket_cost}')
                embed.add_field(name='Max Per Player',value=str(settings.max_tickets))
                embed.add_field(name='Your Tickets',value=str(my),inline=False)
                embed.add_field(name='Total Tickets',value=str(total),inline=False)
                embed.add_field(name='Current Prize Pool',value=f'${prize}',inline=False)
                await ctx.send(embed=embed)
        except Exception as e:
            await ctx.send(f'Error: {e}')

    # GM Draw
    @commands.command(hidden=True)
    @is_gm()
    async def gmdraw(self, ctx):
        async with self.bot.pool.acquire() as conn:
            settings=await fetch_settings(conn)
        if not settings:
            return await ctx.send('No settings found.')
        async with self.bot.pool.acquire() as conn:
            participants=await conn.fetch('SELECT id,tickets FROM lottery')
        if not participants:
            async with self.bot.pool.acquire() as conn:
                await conn.execute('DELETE FROM lottodata')
            return await ctx.send('No participants.')
        weighted=[]; total=0
        for p in participants:
            t=int(p['tickets'] or 0)
            total+=t
            weighted.extend([int(p['id'])]*t)
        if not weighted:
            async with self.bot.pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute('DELETE FROM lottodata')
                    await conn.execute('DELETE FROM lottery')
            return await ctx.send('No valid tickets.')
        import random as _r
        winner_id=_r.choice(weighted)
        gross=total*settings.ticket_cost; cut=(gross*HOUSE_CUT_PCT)//100; prize=gross-cut
        try:
            async with self.bot.pool.acquire() as conn:
                async with conn.transaction():
                    await credit_user_money(conn, winner_id, prize)
                    try:
                        async with conn.transaction():
                            await conn.execute('INSERT INTO lottery_payouts(winner_id,prize,drawn_at) VALUES($1,$2,NOW())',winner_id,prize)
                    except Exception: pass
                    await conn.execute('DELETE FROM lottodata')
                    await conn.execute('DELETE FROM lottery')
        except Exception as e:
            diag=getattr(e,'message',str(e))
            return await ctx.send(f'Error paying winner: {diag}')
        try:
            user=await self.bot.fetch_user(winner_id)
            mention=user.mention if user else f'<@{winner_id}>'
        except Exception:
            mention=f'<@{winner_id}>'
        await ctx.send(f'🎉 Congratulations {mention}! You’ve won **${prize}**!\n(Tickets: {total}, Cost: ${settings.ticket_cost}, Cut: {HOUSE_CUT_PCT}%)')

async def setup(bot):
    await bot.add_cog(Lottery(bot))
