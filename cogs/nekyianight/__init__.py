"""
The IdleRPG Discord Bot
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

from contextlib import suppress
from datetime import datetime

import discord
from discord import Embed
from discord.ext import commands

from cogs.shard_communication import user_on_cooldown as user_cooldown
from utils import checks as checks
from utils import random
from utils.i18n import _, locale_doc
import re

# -------------------- Theming & Assets --------------------
SHOP_THUMB_URL = "https://i.imgur.com/WbsbNAQ.png"
SHOP_BANNER_URL = "https://i.imgur.com/isyuxM3.png"
# Stored in profile.backgrounds when awarded/purchased (use a whitelisted/stable URL)
EVENT_BG_URL = "https://echoesofolympus.ovh/wp-content/uploads/2025/09/evtbg.jpg"

# Provided custom emojis
E = {
    "common":    "<:c_common:1405959169747587072>",
    "divine":    "<:c_divine:1405959193407651980>",
    "fortune":   "<:c_fortune:1405959213682917629>",
    "legendary": "<:c_legendary:1405959222536966256>",
    "magic":     "<:c_magic:1405959234260045926>",
    "mats":      "<:c_mats:1405959241898004480>",
    "mystery":   "<:c_mystery:1405959250441666590>",
    "rare":      "<:c_rare:1405959260189229067>",
    "uncommon":  "<:c_uncommon:1405959270834638848>",
    "obol":      "<:obolevent:1418343752505753760>",
    "resetpotion": "<:Resetpotion2:1405960849742499982>",
}

# Greek underworld weapon prefixes (used when naming generated items)
GREEK_PREFIXES = [
    _("Hecate's"), _("Hades'"), _("Persephone's"), _("Thanatos'"), _("Morpheus'"),
    _("Charon's"), _("Nyx's"), _("Erebus'"), _("Hypnos'"), _("Melinoë's"), _("Macaria's"),
    _("Lethean"), _("Stygian"), _("Asphodel"), _("Elysian"), _("Nekyian"), _("Chthonic"),
    _("Underworld"), _("Nocturne"), _("Grave-borne"), _("Obol-Blessed"), _("Ferryman's"),
]


class Underworld(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.waiting = None  # simple in-memory pairing (no sharding required)

    # ---------- Core: Visit a burial site (replaces trickortreat) ----------

    @checks.has_char()
    @user_cooldown(3600)  # 1h default
    @commands.command(aliases=["payobol", "tot"], brief=_("Visit a burial site"))
    @locale_doc
    async def obol(self, ctx):
        _(
            """
            Visit another player's burial site. 50% the gods are pleased and grant an Obol.

            Flow: one user waits, the next user visits. No self-visits. Cooldown applies to the visitor.
            """
        )

        waiting = self.waiting
        if not waiting:
            self.waiting = ctx.author
            choices = [
                _("The cemetery is hushed… no graves accept visitors yet."),
                _("You don't have time to visit the burial sites right now."),
                _("The burial gates are locked. Return later."),
                _("You roam the tombs and find no soul awaiting you."),
                _("You wait by the gate. The next traveler who uses `{p}obol` will visit you.").format(p=ctx.clean_prefix),
            ]
            return await ctx.send(random.choice(choices))

        if waiting.id == ctx.author.id:
            self.waiting = None
            return await ctx.send(_("You cannot visit your own burial site."))

        # Consume the pairing
        self.waiting = None

        async with self.bot.pool.acquire() as conn:
            patron = random.choice(["Hecate", "Hades", "Charon"])
            await conn.execute(
                'UPDATE profile SET burial_visits=burial_visits+1 WHERE "user"=$1;',
                ctx.author.id,
            )

            favored = (random.randint(0, 1) == 1)

            if favored:
                bones_gain = random.randint(10, 20)
                bundles_gain = 1
                visited_bones = random.randint(1, 5)

                # Visitor gains
                await conn.execute(
                    'UPDATE profile SET bones_nekyia=bones_nekyia+$1, funeral_bundles=funeral_bundles+$2, obols=obols+1, obols_offered=obols_offered+1 WHERE "user"=$3;',
                    bones_gain, bundles_gain, ctx.author.id,
                )
                # Visited gains
                await conn.execute(
                    'UPDATE profile SET obols_received=obols_received+1, bones_nekyia=bones_nekyia+$1 WHERE "user"=$2;',
                    visited_bones, waiting.id,
                )

                # 1% background to visitor
                if random.randint(1, 100) == 1 and EVENT_BG_URL:
                    current = await conn.fetchval('SELECT backgrounds FROM profile WHERE "user"=$1;', ctx.author.id)
                    if current is None or EVENT_BG_URL not in current:
                        await conn.execute(
                            'UPDATE profile SET backgrounds=array_append(backgrounds,$1) WHERE "user"=$2;',
                            EVENT_BG_URL, ctx.author.id,
                        )
                        await ctx.send(
                            _("As you depart, a funerary image clings to you. It's now in `{prefix}eventbackgrounds`.").format(prefix=ctx.clean_prefix)
                        )

                # Totals for footer
                v_totals = await conn.fetchrow(
                    'SELECT bones_nekyia, funeral_bundles, obols FROM profile WHERE "user"=$1;', ctx.author.id
                )

                # Reward summary embed
                embed = Embed(
                    title=_("The gods are pleased"),
                    description=_("{e} You visit {waiting}'s burial site. {patron} is pleased. A coin shimmers into your palm; you place it for the departed and are granted one in turn.")
                        .format(e=E['obol'], waiting=waiting, patron=patron),
                    color=0x2ecc71,
                )
                embed.add_field(
                    name=_("You receive"),
                    value=_("• +{b} Event Bones\n• +1 Funeral Bundle\n• {e} +1 Obol").format(b=bones_gain, e=E['obol']),
                    inline=False,
                )
                embed.add_field(
                    name=_("{name} receives").format(name=waiting.display_name),
                    value=_("• +{b} Event Bones").format(b=visited_bones),
                    inline=False,
                )
                embed.set_footer(
                    text=_("Your totals — Bones: {b} • Bundles: {fb} • Obols: {o}")
                        .format(b=v_totals['bones_nekyia'], fb=v_totals['funeral_bundles'], o=v_totals['obols'])
                )
                await ctx.send(embed=embed)

                # DM visited with exact bones
                with suppress(discord.Forbidden):
                    await waiting.send(
                        _("{author} visited your burial site and left you an obol for passage.\nYou received **+{b}** Event Bones.")
                        .format(author=ctx.author, b=visited_bones)
                    )

            else:
                # 20% small consolation
                conso_bones = 2 if random.randint(1, 5) == 1 else 0
                if conso_bones:
                    await conn.execute(
                        'UPDATE profile SET bones_nekyia=bones_nekyia+$1 WHERE "user"=$2;',
                        conso_bones, ctx.author.id
                    )

                v_totals = await conn.fetchrow(
                    'SELECT bones_nekyia, funeral_bundles, obols FROM profile WHERE "user"=$1;', ctx.author.id
                )

                desc = _("You visit {waiting}'s burial site, but the gods are silent; no coin is granted.").format(waiting=waiting)
                embed = Embed(title=_("The gods are silent"), description=desc, color=0xe74c3c)
                if conso_bones:
                    embed.add_field(
                        name=_("Consolation"),
                        value=_("• +{b} Event Bones").format(b=conso_bones),
                        inline=False,
                    )
                embed.set_footer(
                    text=_("Your totals — Bones: {b} • Bundles: {fb} • Obols: {o}")
                        .format(b=v_totals['bones_nekyia'], fb=v_totals['funeral_bundles'], o=v_totals['obols'])
                )
                await ctx.send(embed=embed)

                with suppress(discord.Forbidden):
                    await waiting.send(
                        _("{author} visited your burial site, but left no offering.").format(author=ctx.author)
                    )

    # ---------- Open a Funeral Bundle ----------

    @checks.has_char()
    @commands.command(aliases=["openfuneral", "openbundle"], brief=_("Open a Funeral Bundle"))
    @locale_doc
    async def open_cache(self, ctx):
        _(
            """
            Open a Funeral Bundle earned by paying the obol.
            Bundles contain chthonic relics, event bones, and a small chance at extra obols.
            """
        )
        bundles = await self.bot.pool.fetchval('SELECT funeral_bundles FROM profile WHERE "user"=$1;', ctx.author.id) or 0
        if bundles < 1:
            return await ctx.send(_("You hold no Funeral Bundles. Earn them by paying the obol."))

        # Rarity tiers
        roll = random.randint(1, 100)
        if roll == 1:
            minstat, maxstat = 55, 75; tier_label = _("Elysian Artifact")
        elif roll < 10:
            minstat, maxstat = 42, 55; tier_label = _("Stygian Relic")
        elif roll < 30:
            minstat, maxstat = 38, 42; tier_label = _("Asphodel Keepsake")
        elif roll < 50:
            minstat, maxstat = 25, 38; tier_label = _("Lethean Charm")
        else:
            minstat, maxstat = 10, 25; tier_label = _("Grave Token")

        item = await self.bot.create_random_item(
            minstat=minstat, maxstat=maxstat, minvalue=1, maxvalue=200,
            owner=ctx.author, insert=False,
        )

        prefix = random.choice(GREEK_PREFIXES)
        item["name"] = f"{prefix} {item['type_']}"

        bones_gain = random.randint(5, 30)
        extra_obol = 1 if random.randint(1, 20) == 1 else 0  # 5%

        async with self.bot.pool.acquire() as conn:
            await self.bot.create_item(**item, conn=conn)
            await conn.execute(
                'UPDATE profile SET funeral_bundles=funeral_bundles-1, bones_nekyia=bones_nekyia+$1, obols=obols+$2 WHERE "user"=$3;',
                bones_gain, extra_obol, ctx.author.id,
            )

        embed = discord.Embed(
            title=_("A relic surfaces from the Styx"),
            description=_("You unfurl the Funeral Bundle and retrieve a {tier}.").format(tier=tier_label),
            color=self.bot.config.game.primary_colour,
        )
        embed.add_field(name=_("Name"), value=item["name"], inline=False)
        embed.add_field(name=_("Element"), value=item["element"], inline=True)
        embed.add_field(name=_("Type"), value=item["type_"], inline=True)
        embed.add_field(name=_("Damage"), value=item["damage"], inline=True)
        embed.add_field(name=_("Armor"), value=item["armor"], inline=True)
        embed.add_field(name=_("Value"), value=f"${item['value']}", inline=True)
        embed.add_field(name=_("Event Bones"), value=str(bones_gain), inline=True)
        if extra_obol:
            embed.add_field(name=_("Bonus"), value=_("{e} +1 Obol (Divine favor)").format(e=E['obol']), inline=True)
        embed.set_footer(text=_("Remaining bundles: {n}").format(n=bundles - 1))
        await ctx.send(embed=embed)

    # ---------- Cerberus' Den (Event Shop) ----------

    @checks.has_char()
    @commands.group(aliases=["ss", "den"], invoke_without_command=True, brief=_("Open Cerberus' Den"))
    @locale_doc
    async def cerberus(self, ctx):
        _(
            """
            Cerberus' Den — limited-time shop for Nekyia Nights.
            Pay in event Bones (bones_nekyia). Global, limited-edition stock shared by all players.
            """
        )
        if ctx.invoked_subcommand is not None:
            return

        bones_count = await self.bot.pool.fetchval(
            'SELECT bones_nekyia FROM profile WHERE "user"=$1;', ctx.author.id
        ) or 0

        rows = await self.bot.pool.fetch(
            """SELECT item_key, title, cost_bones, cost_obols, stock_remaining, emoji
            FROM event_shop
            WHERE enabled AND item_key <> 'pet_skill_reset_potion'
            ORDER BY sort_order, title;"""
        )

        if not rows:
            return await ctx.send(_("Cerberus yawns. The Den is empty—for now."))

        embed = Embed(title=_("Cerberus' Den — Limited Editions (Global Stock)"), color=0xff4500)
        embed.set_thumbnail(url=SHOP_THUMB_URL)
        embed.set_image(url=SHOP_BANNER_URL)
        embed.set_author(name=ctx.author.display_name)

        flavor_map = {
            "crate_uncommon":   _("A modest trove from the Asphodel meadows."),
            "crate_rare":       _("Rarer spoils dredged from the Lethe."),
            "crate_magic":      _("Mystic offerings blessed by Nyx."),
            "crate_legendary":  _("Relics whispered of in Elysium."),
            "crate_fortune":    _("Coin and clatter fit for a ferryman."),
            "crate_divine":     _("Boons granted by the chthonic host."),
            "underworld_bg":    _("A funerary image to mark your passage."),
            "weapon_token":     _("Rewrite destiny: change your weapon path."),
            "bundle_3":         _("Three shrouds knotted with secrets."),
            "pet_xp_potion":        _("Nourishes a companion’s experience."),
            "pet_age_potion":       _("Hastens a companion’s years."),
            "pet_speed_growth_potion": _("Quickens a companion’s growth."),
            "reset_potion": _("A draught to rewrite your fate—refunds your stat allocation once."),
        }

        for idx, r in enumerate(rows, 1):
            emoji = (r["emoji"] or "").strip()
            name = f"{emoji} {r['title']}".strip()
            cost_str = (
                _("{e} {c} Obols").format(e=E['obol'], c=r["cost_obols"])
                if int(r["cost_obols"] or 0) > 0
                else _("{} Bones").format(r["cost_bones"])
            )
            value_line = _("""Cost: {cost} • Stock: **{stock}**
            {flavor}""").format(
                cost=cost_str,
                stock=r["stock_remaining"],
                flavor=flavor_map.get(r["item_key"], _("A curious underworld trinket."))
            )

            embed.add_field(name=f"{idx}:  {name}", value=value_line, inline=False)

        word = "Boners" if ctx.author.id == 708435868842459169 else _("Bones")
        obols_count = await self.bot.pool.fetchval('SELECT obols FROM profile WHERE "user"=$1;', ctx.author.id) or 0
        embed.set_footer(
            text=_("You hold {b} Bones 💀 • {o} Obols — use `$den buy <#>` to purchase.")
                .format(b=bones_count, e=E['obol'], o=obols_count),
            icon_url="https://i.ibb.co/5GK1Ry0/vecteezy-skeleton-halloween-cartoon-colored-clipart-8823016-removebg-preview.png",
        )

        await ctx.send(embed=embed)

    def _norm(s: str) -> str:
        # normalize for matching: lowercase, remove spaces/underscores/hyphens/slashes
        return re.sub(r"[\s_\-\/]+", "", s.strip().lower())

    @cerberus.command(name="buy")
    @user_cooldown(5)
    async def _buy(self, ctx, *, choice: str):
        """Buy by index (as shown in $den) OR by item key/title."""
        # Load the current enabled list in the same order as the embed
        rows = await self.bot.pool.fetch(
            """SELECT item_key, title, cost_bones, cost_obols, stock_remaining, emoji
            FROM event_shop
            WHERE enabled AND item_key <> 'pet_skill_reset_potion'
            ORDER BY sort_order, title;"""
        )
        if not rows:
            return await ctx.send(_("Cerberus yawns. The Den is empty—for now."))

        choice_norm = choice.strip().lower()

        # Path A: numeric index (from the embed)
        if choice_norm.isdigit():
            idx = int(choice_norm)
            if idx < 1 or idx > len(rows):
                return await ctx.send(_("Invalid choice. Use the number shown in `{p}den`.").format(p=ctx.clean_prefix))
            item = rows[idx - 1]
        else:
            # Path B: match by exact key or title, then partials
            def norm(s: str) -> str: return (s or "").strip().lower()
            # try exact item_key
            found = [r for r in rows if norm(r["item_key"]) == choice_norm]
            # try exact title
            if not found:
                found = [r for r in rows if norm(r["title"]) == choice_norm]
            # try partial key/title
            if not found:
                found = [r for r in rows if choice_norm in norm(r["item_key"]) or choice_norm in norm(r["title"])]
            if not found:
                return await ctx.send(_("I couldn't find that. Try `{p}den` and use the number, or type the item key.")
                                    .format(p=ctx.clean_prefix))
            item = found[0]

        key   = item["item_key"]
        title = item["title"]
        if key == "pet_skill_reset_potion":
            return await ctx.send(_("This item has been removed from the game."))

        async with self.bot.pool.acquire() as conn:
            try:
                async with conn.transaction():
                    # Reserve stock
                    row = await conn.fetchrow(
                        """UPDATE event_shop
                        SET stock_remaining = stock_remaining - 1
                        WHERE item_key = $1 AND stock_remaining > 0
                        RETURNING stock_remaining;""",
                        key
                    )
                    if not row:
                        return await ctx.send(_("Sold out."))

                    # Charge currency
                    if int(item["cost_obols"] or 0) > 0:
                        charged = await conn.fetchrow(
                            '''UPDATE profile
                            SET obols = obols - $2
                            WHERE "user"=$1 AND obols >= $2
                            RETURNING obols;''',
                            ctx.author.id, int(item["cost_obols"])
                        )
                        if not charged:
                            await conn.execute('UPDATE event_shop SET stock_remaining = stock_remaining + 1 WHERE item_key=$1;', key)
                            return await ctx.send(_("You cannot afford this."))
                        new_balance = charged["obols"]
                        currency_label = _("Obols")
                        cost_shown = item["cost_obols"]
                    else:
                        charged = await conn.fetchrow(
                            '''UPDATE profile
                            SET bones_nekyia = bones_nekyia - $2
                            WHERE "user"=$1 AND bones_nekyia >= $2
                            RETURNING bones_nekyia;''',
                            ctx.author.id, int(item["cost_bones"])
                        )
                        if not charged:
                            await conn.execute('UPDATE event_shop SET stock_remaining = stock_remaining + 1 WHERE item_key=$1;', key)
                            return await ctx.send(_("You cannot afford this."))
                        new_balance = charged["bones_nekyia"]
                        currency_label = _("Bones")
                        cost_shown = item["cost_bones"]

                    # Deliver
                    await self._deliver_shop_item(conn, ctx.author, key)

            except Exception:
                # best-effort stock restore would have happened above on insufficient funds
                return await ctx.send(_("Purchase failed. Please try again."))

        # Receipt
        rec = discord.Embed(
            title=_("Purchase confirmed"),
            description=_("Acquired **{title}** for **{cost} {curr}**.")
                        .format(title=title, cost=cost_shown, curr=currency_label),
            color=0x3498db,
        )
        rec.add_field(name=_("Your {curr} (after)").format(curr=currency_label), value=str(new_balance), inline=True)
        await ctx.send(embed=rec)



    @cerberus.command(name="bal")
    async def _bal(self, ctx):
        n = await self.bot.pool.fetchval('SELECT bones_nekyia FROM profile WHERE "user"=$1;', ctx.author.id) or 0
        word = "Boners" if ctx.author.id == 708435868842459169 else _("Bones")
        await ctx.send(_("You currently have **{n}** {w}, {m}! ").format(n=n, w=word, m=ctx.author.mention))

    # ---------- Convenience readouts ----------

    async def _grant_consumable(self, conn, uid: int, item_key: str, qty: int):
        await conn.execute(
            """
            INSERT INTO user_consumables (user_id, consumable_type, quantity, metadata, created_at)
            VALUES ($1, $2, $3, '{}'::jsonb, NOW())
            ON CONFLICT (user_id, consumable_type)
            DO UPDATE SET quantity = user_consumables.quantity + EXCLUDED.quantity;
            """,
            uid, item_key, qty
        )

    async def _deliver_shop_item(self, conn, member: discord.Member, key: str):
        uid = member.id
        if key == "crate_uncommon":
            await conn.execute('UPDATE profile SET crates_uncommon = crates_uncommon + 1 WHERE "user"=$1;', uid)
        elif key == "crate_rare":
            await conn.execute('UPDATE profile SET crates_rare = crates_rare + 1 WHERE "user"=$1;', uid)
        elif key == "crate_magic":
            await conn.execute('UPDATE profile SET crates_magic = crates_magic + 1 WHERE "user"=$1;', uid)
        elif key == "crate_legendary":
            await conn.execute('UPDATE profile SET crates_legendary = crates_legendary + 1 WHERE "user"=$1;', uid)
        elif key == "crate_fortune":
            await conn.execute('UPDATE profile SET crates_fortune = crates_fortune + 1 WHERE "user"=$1;', uid)
        elif key == "crate_divine":
            await conn.execute('UPDATE profile SET crates_divine = crates_divine + 1 WHERE "user"=$1;', uid)
        elif key == "underworld_bg":
            if EVENT_BG_URL:
                current = await conn.fetchval('SELECT backgrounds FROM profile WHERE "user"=$1;', uid)
                if current is None or EVENT_BG_URL not in current:
                    await conn.execute('UPDATE profile SET backgrounds=array_append(backgrounds,$1) WHERE "user"=$2;', EVENT_BG_URL, uid)
        elif key == "weapon_token":
            await conn.execute('UPDATE profile SET weapontoken = weapontoken + 1 WHERE "user"=$1;', uid)
        elif key == "bundle_3":
            await conn.execute('UPDATE profile SET funeral_bundles = funeral_bundles + 3 WHERE "user"=$1;', uid)
        elif key == "reset_potion":
            await conn.execute('UPDATE profile SET resetpotion = COALESCE(resetpotion, 0) + 1 WHERE "user"=$1;', uid)
        elif key == "bundle_obol":
            await conn.execute('UPDATE profile SET funeral_bundles = funeral_bundles + 1 WHERE "user"=$1;', uid)
        elif key in {"pet_xp_potion", "pet_age_potion", "pet_speed_growth_potion"}:
            await self._grant_consumable(conn, uid, key, 1)

        else:
            # Unknown key: no-op
            pass

    # ---------- Player Balances ----------

    @checks.has_char()
    @commands.command(aliases=["funeralbundles", "bundles"], brief=_("Show your Funeral Bundles"))
    async def bundles_bal(self, ctx):
        n = await self.bot.pool.fetchval('SELECT funeral_bundles FROM profile WHERE "user"=$1;', ctx.author.id) or 0
        await ctx.send(_("You currently carry **{n}** Funeral Bundles, {m}! ").format(n=n, m=ctx.author.mention))

    @checks.has_char()
    @commands.command(aliases=["obols"], brief=_("Show your Obols"))
    async def obols_bal(self, ctx):
        n = await self.bot.pool.fetchval('SELECT obols FROM profile WHERE "user"=$1;', ctx.author.id) or 0
        await ctx.send(_("You currently hold {e} **{n}** Obols, {m}. Guard them well.").format(e=E['obol'], n=n, m=ctx.author.mention))

    # ---------- Help ----------

    @checks.has_char()
    @commands.command(name="nekyia", aliases=["nekyiahelp", "underworldhelp", "uhelp", "denhelp"], brief=_("Show Underworld event help"))
    @locale_doc
    async def nekyia_help(self, ctx):
        _(
            """
            Show a quick guide to Nekyia Nights (commands, aliases, and economy hints).
            """
        )
        prefix = ctx.clean_prefix
        bones = await self.bot.pool.fetchval('SELECT bones_nekyia FROM profile WHERE "user"=$1;', ctx.author.id) or 0
        obols = await self.bot.pool.fetchval('SELECT obols FROM profile WHERE "user"=$1;', ctx.author.id) or 0
        bundles = await self.bot.pool.fetchval('SELECT funeral_bundles FROM profile WHERE "user"=$1;', ctx.author.id) or 0

        embed = Embed(
            title=_("Nekyia Nights — Guide"),
            description=_("Pay the obol, brave the tombs, and trade bones in Cerberus' Den."),
            color=self.bot.config.game.primary_colour
        )
        embed.set_thumbnail(url=SHOP_THUMB_URL)
        embed.set_image(url=SHOP_BANNER_URL)

        visit_txt = _("""• `{p}obol` — Visit a burial site (1h CD).
50% chance the gods are pleased: {e} an Obol is granted, you place it for the departed, and you gain extra rewards.
You cannot visit yourself. One user waits; the next user visits.""").format(p=prefix, e=E['obol'])

        open_txt = _("""• `{p}openbundle` — Open a Funeral Bundle (aliases: `{p}openfuneral`).
  Grants a themed item, event Bones, and a small chance of +1 Obol.""").format(p=prefix)

        shop_txt = _("""• `{p}den` (aliases: `{p}ss`) — Open Cerberus' Den.
• `{p}den buy <#>` — Purchase by item number.
• `{p}den bal` — Show your event Bones.""").format(p=prefix)

        balance_txt = _("""• `{p}bundles` — Show your Funeral Bundles.
• `{p}obols` — Show your Obols.""").format(p=prefix)

        embed.add_field(name=_("Visit"), value=visit_txt, inline=False)
        embed.add_field(name=_("Open Bundles"), value=open_txt, inline=False)
        embed.add_field(name=_("Shop"), value=shop_txt, inline=False)
        embed.add_field(name=_("Balances"), value=balance_txt, inline=False)

        stats = _("""Event Bones: **{b}**
Obols: {e} **{o}**
Funeral Bundles: **{fb}**""").format(b=bones, e=E['obol'], o=obols, fb=bundles)
        embed.add_field(name=_("Your Totals"), value=stats, inline=False)

        notes = _("""• When the gods are pleased (50% chance), you gain +1 Funeral Bundle and extra Bones; the visited player also earns Bones.
• Rare chance to receive an Underworld Background as you travel.
• All rewards use event-only Bones (`bones_nekyia`) so your main balance stays safe.""")
        embed.add_field(name=_("Notes"), value=notes, inline=False)

        await ctx.send(embed=embed)

    # ---------- GM tools ----------

    @commands.is_owner()
    @commands.command()
    async def reset_nekyia(self, ctx):
        """GM: zero all Underworld event fields for everyone."""
        q = '''
        UPDATE profile
        SET obols=0, bones_nekyia=0, funeral_bundles=0,
            burial_visits=0, obols_offered=0, obols_received=0;
        '''
        async with self.bot.pool.acquire() as conn:
            await conn.execute(q)
        await ctx.send("Underworld event counters reset for all players.")

    @commands.is_owner()
    @commands.command()
    async def grant_obols(self, ctx, member: discord.Member, amount: int):
        await self.bot.pool.execute('UPDATE profile SET obols=obols+$1 WHERE "user"=$2;', amount, member.id)
        await ctx.send(f"Gave {amount} Obols to {member.mention}.")

    @commands.is_owner()
    @commands.command()
    async def grant_bones(self, ctx, member: discord.Member, amount: int):
        await self.bot.pool.execute('UPDATE profile SET bones_nekyia=bones_nekyia+$1 WHERE "user"=$2;', amount, member.id)
        await ctx.send(f"Gave {amount} event Bones to {member.mention}.")

    @commands.is_owner()
    @commands.command()
    async def grant_bundles(self, ctx, member: discord.Member, amount: int):
        await self.bot.pool.execute('UPDATE profile SET funeral_bundles=funeral_bundles+$1 WHERE "user"=$2;', amount, member.id)
        await ctx.send(f"Gave {amount} Funeral Bundles to {member.mention}.")

    @commands.is_owner()
    @commands.command(name="den_stock")
    async def den_stock(self, ctx, show_all: str = None):
        """GM: View global Cerberus' Den stock. Use `all` to include disabled items."""
        include_all = (show_all or "").lower() in ("all", "a", "full")
        rows = await self.bot.pool.fetch(
            """SELECT item_key, title, cost_bones, cost_obols, stock_remaining, emoji, enabled
            FROM event_shop
            WHERE $1 OR enabled
            ORDER BY sort_order, title;""",
            include_all
        )

        lines = []
        for r in rows:
            status = "🟢" if r["enabled"] else "🔴"
            emoji = (r["emoji"] or "").strip()
            if int(r["cost_obols"] or 0) > 0:
                cost_str = f"{E['obol']} {int(r['cost_obols'])} Obols"
            else:
                cost_str = f"{int(r['cost_bones'])} Bones"
            lines.append(f"{status} {emoji} `{r['item_key']}` — {r['title']}: **{r['stock_remaining']}** (cost {cost_str})")
        desc = "\n".join(lines)


        if len(desc) <= 4000:
            embed = Embed(title=_("Cerberus' Den — Stock Overview"), description=desc, color=0x5865F2)
            await ctx.send(embed=embed)
        else:
            # Fallback chunking
            for i in range(0, len(desc), 1900):
                await ctx.send(desc[i:i+1900])

    @commands.is_owner()
    @commands.command(name="den_set")
    async def den_set(self, ctx, item_key: str, stock: int):
        """GM: Set global stock for a given item_key."""
        if stock < 0:
            return await ctx.send(_("Stock must be >= 0."))
        row = await self.bot.pool.fetchrow(
            'UPDATE event_shop SET stock_remaining=$2 WHERE item_key=$1 RETURNING title, stock_remaining;',
            item_key, stock
        )
        if not row:
            return await ctx.send(_("Unknown item_key: `{k}`").format(k=item_key))
        await ctx.send(_("Set `{k}` — **{t}** stock to **{s}**.").format(k=item_key, t=row['title'], s=row['stock_remaining']))

    @commands.is_owner()
    @commands.command(name="den_cost")
    async def den_cost(self, ctx, item_key: str, new_cost: int):
        if new_cost < 0:
            return await ctx.send(_("Cost must be >= 0."))
        row = await self.bot.pool.fetchrow(
            'UPDATE event_shop SET cost_bones=$2 WHERE item_key=$1 RETURNING title, cost_bones;',
            item_key, new_cost
        )
        if not row:
            return await ctx.send(_("Unknown item_key: `{k}`").format(k=item_key))
        await ctx.send(_("Set `{k}` — **{t}** cost to **{c} Bones**.").format(
            k=item_key, t=row['title'], c=row['cost_bones']
        ))

    @commands.is_owner()
    @commands.command(name="den_cost_obols")
    async def den_cost_obols(self, ctx, item_key: str, new_cost: int):
        """GM: Set Obol price for an item (set to 0 to disable obol pricing)."""
        if new_cost < 0:
            return await ctx.send(_("Cost must be >= 0."))
        row = await self.bot.pool.fetchrow(
            'UPDATE event_shop SET cost_obols=$2 WHERE item_key=$1 RETURNING title, cost_obols;',
            item_key, new_cost
        )
        if not row:
            return await ctx.send(_("Unknown item_key: `{k}`").format(k=item_key))
        await ctx.send(_("Set `{k}` — **{t}** Obol cost to **{c}**.").format(
            k=item_key, t=row['title'], c=row['cost_obols']
        ))


async def setup(bot):
    await bot.add_cog(Underworld(bot))
