from datetime import datetime, timezone
import asyncio
import datetime
from decimal import Decimal, ROUND_HALF_UP, getcontext
import random as pyrandom
import discord
from discord.ext import commands
from discord.ui import Button
from discord.enums import ButtonStyle

# Adjust these imports if your project paths differ
from classes.converters import IntGreaterThan
from utils.checks import is_god
from utils.divine_familiars import DIVINE_FAMILIARS, award_divine_shards
from utils.i18n import _
from utils.joins import JoinView

GOD_NAME = "Morpheus"
FOLLOWERS_ROLE_ID = 1415389046137422014
ANNOUNCE_CHANNEL_IDS = [1415389485822251159]

START_IMAGE = "https://i.imgur.com/etbvwBT.png"
ATTACK_IMAGE = "https://i.imgur.com/Dl3eFJm.png"

# Reward config
RARITIES = {
    "legendary": {"column": "crates_legendary", "fallback": "🎁", "emote": "<:c_legendary:1405959222536966256>"},
    "fortune":   {"column": "crates_fortune",   "fallback": "💠", "emote": "<:c_fortune:1405959213682917629>"},
    "divine":    {"column": "crates_divine",    "fallback": "✨", "emote": "<:c_divine:1405959193407651980>"},
}
WEIGHTS = {"legendary": 1, "fortune": 1, "divine": 1}  # tweak to weight the roll
DIVINE_SHARD_RAID_PROC_CHANCE = 0.15
DIVINE_SHARD_FAMILIAR_KEY = "drakath_familiar"


class ShatteredVeil(commands.Cog):
    """The Shattered Veil — Phobetor, The Dreambreaker (stand-alone)."""

    def __init__(self, bot):
        self.bot = bot
        self.raid_active = False

    # ---------------------- helpers ----------------------

    async def set_raid_timer(self):
        self.raid_active = True

    async def clear_raid_timer(self):
        self.raid_active = False

    @staticmethod
    def getfinaldmg(base: int, armor: Decimal) -> int:
        """Damage reduced by armor, rounded half-up; never negative."""
        getcontext().prec = 28
        dmg = Decimal(base) - (Decimal(base) * Decimal(armor))
        return max(0, int(dmg.to_integral_value(rounding=ROUND_HALF_UP)))

    @staticmethod
    def pick_attacker_avatar(user, raid: dict):
        """If a specific follower attacks: use their PFP; else pick a random survivor."""
        try:
            if user is not None and user in raid:
                return user.display_avatar.url
        except Exception:
            pass
        if raid:
            any_survivor = pyrandom.choice(list(raid.keys()))
            return any_survivor.display_avatar.url
        return None

    def _is_beta(self) -> bool:
        return False  # always force full countdown

    async def _get_raidstats(self, u, profile, conn):
        """
        Preferred: use project's get_raidstats; fallback: simple defaults.
        Should return (damage:int|Decimal, armor:Decimal)
        """
        if hasattr(self.bot, "get_raidstats"):
            return await self.bot.get_raidstats(
                u,
                atkmultiply=profile.get("atkmultiply"),
                defmultiply=profile.get("defmultiply"),
                classes=profile.get("class"),
                race=profile.get("race"),
                guild=profile.get("guild"),
                god=profile.get("god"),
                xp=profile.get("xp"),
                conn=conn,
            )
        # Fallback values if get_raidstats is unavailable
        base_dmg = int(profile.get("atk", 60) or 60) if isinstance(profile, dict) else 60
        base_arm = Decimal(str(profile.get("arm", "0.15"))) if isinstance(profile, dict) else Decimal("0.15")
        return base_dmg, base_arm

    async def _send_to_channels(self, *, embed=None, content=None, view=None, ctx=None):
        sent_any = False
        for cid in ANNOUNCE_CHANNEL_IDS:
            ch = self.bot.get_channel(cid)
            if not ch:
                continue
            try:
                await ch.send(embed=embed, content=content, view=view)
                sent_any = True
            except Exception as e:
                if ctx:
                    await ctx.send(f"[ShatteredVeil] Failed to send to <#{cid}>: {e}")
        if not sent_any and ctx:
            await ctx.send(embed=embed, content=content, view=view)

    # ---------------------- command ----------------------

    @is_god()  # comment during local testing if needed
    @commands.command(hidden=True, brief=_("Start The Shattered Veil (Morpheus)"))
    async def shatteredveil(self, ctx, oneiroi: IntGreaterThan(1)):
        """[Morpheus only] Stats-based raid against Phobetor and his Oneiroi."""
        try:
            await self.set_raid_timer()

            # Enemy pack: N Oneiroi + boss
            enemies = [{"hp": pyrandom.randint(80, 100), "id": i + 1, "name": "Oneiros"} for i in range(oneiroi)]
            enemies.append({"hp": 1200, "id": 9999, "name": "Phobetor, the Dreambreaker"})  # consistent with intro

            # Join view
            view = JoinView(
                Button(style=ButtonStyle.primary, label=_("Enter the Shattered Veil")),
                message=_("You stepped into the Veil."),
                timeout=60 * 15,
            )

            # Start embed
            intro = (
                "**The Shattered Veil Opens**\n"
                "The air grows heavy, the veil between waking and dream begins to crack.\n"
                "Starlight bends and shadows bleed, as visions coil into nightmare.\n"
                "From the dominion of Morpheus, where dream and prophecy entwine, a rift tears wide—\n"
                "and from its depths, the Oneiroi of Phobetor emerge, faceless and relentless.\n"
                "The Nightmare Prince calls. His voice is thunder, his form ever-shifting.\n"
                "He is **Phobetor, the Dreambreaker**, born of Nyx, herald of terror,\n"
                "and he hungers for the souls of mortals who dare stand against him.\n\n"
                "⚔️ **Phobetor the Dreambreaker has awakened with 1200 HP**\n"
                "and will breach our realm in **15 minutes**.\n\n"
                "*Do you dare to walk the Shattered Veil, or will you surrender to eternal sleep?*"
            )
            note = f"**Only followers of {GOD_NAME} may join.**"

            em = discord.Embed(
                title="The Shattered Veil — Phobetor, The Dreambreaker",
                color=discord.Color.dark_blue(),
                timestamp=discord.utils.utcnow(),
            )
            em.add_field(name="Omen in the Dark", value=intro, inline=False)
            em.add_field(name="Edict of Dreams", value=note, inline=False)
            em.set_footer(text="Sleep is a door. Choose how you open it.")
            em.set_image(url=START_IMAGE)

            # Ping Morpheus followers role (if present)
            role = ctx.guild.get_role(FOLLOWERS_ROLE_ID)
            if role:
                for cid in ANNOUNCE_CHANNEL_IDS:
                    ch = self.bot.get_channel(cid)
                    if ch:
                        await ch.send(content=f"{role.mention}", allowed_mentions=discord.AllowedMentions(roles=True))

            # Announce + join button
            await self._send_to_channels(embed=em, view=view, ctx=ctx)

            # Countdown
            if not self._is_beta():
                await asyncio.sleep(300); await self._send_to_channels(content="**The Veil thins in 10 minutes**", ctx=ctx)
                await asyncio.sleep(300); await self._send_to_channels(content="**The Veil quivers in 5 minutes**", ctx=ctx)
                await asyncio.sleep(180); await self._send_to_channels(content="**Shadows coil in 2 minutes**", ctx=ctx)
                await asyncio.sleep(60);  await self._send_to_channels(content="**The breach yawns in 1 minute**", ctx=ctx)
                await asyncio.sleep(30);  await self._send_to_channels(content="**Thirty heartbeats remain…**", ctx=ctx)
                await asyncio.sleep(20);  await self._send_to_channels(content="**Ten heartbeats…**", ctx=ctx)
            else:
                await asyncio.sleep(60)

            view.stop()
            await self._send_to_channels(content="**The breach tears open! Fetching dreamers…**", ctx=ctx)

            # Build roster
            raid = {}
            async with self.bot.pool.acquire() as conn:
                for u in view.joined:
                    profile = await conn.fetchrow('SELECT * FROM profile WHERE "user"=$1;', u.id)
                    if not profile:
                        continue
                    if (profile["god"] or "").strip().lower() != GOD_NAME.lower():
                        continue
                    dmg, deff = await self._get_raidstats(u, dict(profile), conn)
                    raid[u] = {"hp": 100, "armor": deff, "damage": dmg, "kills": 0}
            participants = list(raid.keys())

            await self._send_to_channels(content=f"**Dreamers gathered: {len(raid)}**", ctx=ctx)

            # Combat with RNG + boons/banes
            boon_timers, bane_timers = {}, {}
            start = datetime.datetime.now(timezone.utc)
            limit = datetime.timedelta(minutes=45)

            def apply_variance(v: int, low=0.85, high=1.20) -> int:
                return max(0, int(round(v * pyrandom.uniform(low, high))))

            while enemies and raid and datetime.datetime.now(timezone.utc) < start + limit:
                # ---- Enemy attacks a random dreamer ----
                target, tdata = pyrandom.choice(list(raid.items()))

                # tick buffs/debuffs counters
                if boon_timers.get(target):
                    boon_timers[target] -= 1
                    if boon_timers[target] <= 0:
                        boon_timers.pop(target, None)
                if bane_timers.get(target):
                    bane_timers[target] -= 1
                    if bane_timers[target] <= 0:
                        bane_timers.pop(target, None)

                # Incoming damage armor multiplier (Decimal)
                armor_mult = Decimal("1.0")
                if target in boon_timers:
                    armor_mult *= Decimal("0.9")
                if target in bane_timers:
                    armor_mult *= Decimal("1.15")

                cur = enemies[0]
                cur_name = cur["name"]

                base_enemy = apply_variance(pyrandom.randint(35, 65))
                reduced = self.getfinaldmg(
                    base_enemy,
                    tdata["armor"] * Decimal(pyrandom.choice(["0.4", "0.5"])) * armor_mult,
                )
                tdata["hp"] -= reduced

                em_att = discord.Embed(
                    title=f"{cur_name} assails the Veil — Enemies left: `{len(enemies)}`",
                    colour=0x2f1142,
                )
                em_att.add_field(name=f"{cur_name} HP", value=f"{cur['hp']} HP")
                em_att.add_field(
                    name="Nightmare Assault",
                    value=(f"{cur_name} claws at {target.mention}" if tdata["hp"] > 0 else f"{cur_name} drags {target.mention} into nightmare"),
                )
                em_att.add_field(name="Nightmare Damage", value=f"Dealt `{reduced}` to {target.mention}")
                em_att.set_image(url=ATTACK_IMAGE)
                await self._send_to_channels(embed=em_att, ctx=ctx)

                if tdata["hp"] <= 0:
                    del raid[target]
                    if not raid:
                        break

                # RNG boon/bane procs
                roll = pyrandom.random()
                if roll < 0.08 and target in raid:
                    boon_timers[target] = pyrandom.randint(2, 4)
                    await self._send_to_channels(content=f"🌙 **Lucid Clarity** blesses {target.mention}: sharper strikes and calmer mind.", ctx=ctx)
                elif roll < 0.16 and target in raid:
                    bane_timers[target] = pyrandom.randint(2, 4)
                    await self._send_to_channels(content=f"😵 **Creeping Dread** grips {target.mention}: strikes waver, fear thickens.", ctx=ctx)

                # ---- Dreamer counter-attack ----
                # 20% chance of a group/regroup moment
                group_attack = pyrandom.random() < 0.20
                attacker_user = None if group_attack else target
                avatar_url = self.pick_attacker_avatar(attacker_user, raid)

                # Build hero damage using Decimal multipliers to avoid Decimal*float issues
                dmg_mult_dec = Decimal("1.0")
                if target in boon_timers:
                    dmg_mult_dec *= Decimal("1.20")
                if target in bane_timers:
                    dmg_mult_dec *= Decimal("0.90")

                base_hero_dec = Decimal(int(tdata["damage"])) * dmg_mult_dec
                base_hero_int = int(base_hero_dec.to_integral_value(rounding=ROUND_HALF_UP))

                hero_dmg = apply_variance(base_hero_int)

                # Apply damage to current enemy
                cur["hp"] -= hero_dmg

                await asyncio.sleep(7)

                em_hero = discord.Embed(
                    title=f"Dreamers left: `{len(raid)}`",
                    colour=0x8a2be2,
                )
                author_name = "Dreamers (Regroup)" if group_attack else f"Dreamer ({(attacker_user or target).display_name})"
                if avatar_url:
                    em_hero.set_author(name=author_name, icon_url=avatar_url)
                else:
                    em_hero.set_author(name=author_name)

                em_hero.add_field(name="Dreamer HP", value=f"{target.mention} has {tdata['hp']} HP left")
                if cur["hp"] > 0:
                    em_hero.add_field(name="Dreamer Strike", value=f"Rends the {cur_name} for `{hero_dmg}`")
                else:
                    money = pyrandom.randint(250, 750)
                    receiver = (attacker_user or target)
                    async with self.bot.pool.acquire() as conn:
                        await conn.execute(
                            'UPDATE profile SET "money"="money"+$1 WHERE "user"=$2;',
                            money,
                            receiver.id,
                        )
                    enemies.pop(0)
                    em_hero.add_field(name="Dreamer Strike", value=f"Shatters the {cur_name} and receives ${money}")
                    if raid.get(receiver):
                        raid[receiver]["kills"] += 1

                    # Boss reveal if next up is Phobetor
                    if enemies and enemies[0]["id"] == 9999:
                        await self._send_to_channels(
                            content="⚡ **The Nightmare Prince steps through the breach… Phobetor reveals his true form! 1200 HP stands before you.**",
                            ctx=ctx,
                        )

                await self._send_to_channels(embed=em_hero, ctx=ctx)
                await asyncio.sleep(7)

            # ---------------------- Endings / rewards ----------------------
            if not enemies and raid:
                most_kills = sorted(raid.items(), key=lambda x: -(x[1]["kills"]))[0][0]

                # roll a rarity
                try:
                    pool = [r for r, w in WEIGHTS.items() for _ in range(int(w))]
                    rarity = pyrandom.choice(pool) if pool else "legendary"
                except Exception:
                    rarity = "legendary"

                cfg = RARITIES[rarity]
                col = cfg["column"]

                # update the correct crate column (column name is whitelisted above)
                async with self.bot.pool.acquire() as conn:
                    await conn.execute(
                        f'UPDATE profile SET "{col}"="{col}"+$1 WHERE "user"=$2;',
                        1,
                        most_kills.id,
                    )
                    if hasattr(self.bot, "log_transaction"):
                        await self.bot.log_transaction(
                            ctx,
                            from_=1,
                            to=most_kills.id,
                            subject="crates",
                            data={"Rarity": rarity, "Amount": 1},
                            conn=conn,
                        )

                emote = cfg["emote"] or cfg["fallback"]
                pretty = rarity.capitalize()
                await self._send_to_channels(
                    content=(
                        f"The Veil stills. {most_kills.mention} tears a trophy from Phobetor’s fading hush — "
                        f"a {emote} blessed by **{GOD_NAME}**. ({pretty})"
                    ),
                    ctx=ctx,
                )

                if participants and pyrandom.random() < DIVINE_SHARD_RAID_PROC_CHANCE:
                    shard_target = pyrandom.choice(participants)
                    familiar_key = DIVINE_SHARD_FAMILIAR_KEY
                    familiar_name = DIVINE_FAMILIARS[familiar_key]["name"]
                    async with self.bot.pool.acquire() as conn:
                        shard_total = await award_divine_shards(
                            conn,
                            shard_target.id,
                            familiar_key,
                            1,
                        )
                    await self._send_to_channels(
                        content=(
                            f"✨ A dream-fragment settles on {shard_target.mention}: "
                            f"**1 {familiar_name} shard**. Now: **{shard_total}/20**"
                        ),
                        ctx=ctx,
                    )
            elif not raid:
                await self._send_to_channels(content="💀 The Veil implodes. Sleep devours all who entered…", ctx=ctx)

        except Exception:
            import traceback
            await ctx.send(f"ERR in shatteredveil:\n```py\n{traceback.format_exc()}\n```")
            raise
        finally:
            await self.clear_raid_timer()


async def setup(bot):
    await bot.add_cog(ShatteredVeil(bot))
