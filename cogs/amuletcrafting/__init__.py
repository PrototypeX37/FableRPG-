from discord.ext import commands
import discord
from utils.checks import has_char
from utils import misc as rpgtools
from utils.i18n import _
import random


class AmuletCrafting(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        # Final rebalanced amulet stats per tier
        self.TIER_STATS = {
            1: {"health": 100, "defense": 50, "attack": 50},  # Starter
            2: {"health": 250, "defense": 150, "attack": 150},  # Basic
            3: {"health": 500, "defense": 300, "attack": 300},  # Advanced
            4: {"health": 800, "defense": 500, "attack": 500},  # Refined
            5: {"health": 1200, "defense": 700, "attack": 700},  # Empowered
            6: {"health": 1800, "defense": 900, "attack": 900},  # Superior
            7: {"health": 2500, "defense": 1100, "attack": 1100},  # Exalted
            8: {"health": 3300, "defense": 1250, "attack": 1250},  # Mythical
            9: {"health": 5000, "defense": 1400, "attack": 1400},  # Legendary
            10: {"health": 7500, "defense": 1500, "attack": 1500}  # Divine
        }

        # Level requirements per tier
        self.TIER_LEVELS = {
            1: 1,  # Anyone can craft
            2: 10,  # Starting to advance
            3: 20,  # Getting stronger
            4: 30,  # Mid-game
            5: 40,  # Experienced
            6: 50,  # Veteran
            7: 60,  # Master
            8: 70,  # Elite
            9: 80,  # Champion
            10: 90  # Ultimate
        }

        self.BASE_RESOURCES = {
            "dragon_scales": "Common material from dragons",
            "frost_essence": "Common drop from ice dragons",
            "void_crystal": "Uncommon drop from void dragons",
            "dragon_heart": "Rare drop from elder dragons",
            "eternal_ice": "Rare drop from ancient ice dragons",
            "cosmic_shard": "Epic material from cosmic dragons",
            "world_stone": "Legendary material from world bosses",
            "divine_essence": "Mythical essence from divine beings"
        }

        # Resource requirements
        self.AMULET_RECIPES = {
            1: {
                "dragon_scales": 25,
                "frost_essence": 15
            },
            2: {
                "dragon_scales": 50,
                "frost_essence": 25,
                "void_crystal": 5
            },
            3: {
                "void_crystal": 15,
                "frost_essence": 50,
                "dragon_heart": 1
            },
            4: {
                "void_crystal": 25,
                "dragon_heart": 2,
                "eternal_ice": 10
            },
            5: {
                "eternal_ice": 20,
                "dragon_heart": 3,
                "cosmic_shard": 1
            },
            6: {
                "eternal_ice": 30,
                "cosmic_shard": 2,
                "dragon_heart": 5
            },
            7: {
                "cosmic_shard": 3,
                "dragon_heart": 7,
                "world_stone": 1
            },
            8: {
                "cosmic_shard": 4,
                "world_stone": 2,
                "dragon_heart": 10
            },
            9: {
                "world_stone": 3,
                "cosmic_shard": 5,
                "divine_essence": 1
            },
            10: {
                "world_stone": 4,
                "divine_essence": 2,
                "cosmic_shard": 7
            }
        }

    @commands.group(invoke_without_command=True)
    @has_char()
    async def amulet(self, ctx):
        """Base command for amulet system"""
        await ctx.send("Available commands: `craft`, `equip`, `unequip`, `view`")

    def get_amulet_stat(self, amulet):
        """Get the stat value for an amulet based on its tier and type"""
        tier_stats = {
            1: {"hp": 100, "defense": 50, "attack": 50},
            2: {"hp": 250, "defense": 150, "attack": 150},
            3: {"hp": 500, "defense": 300, "attack": 300},
            4: {"hp": 800, "defense": 500, "attack": 500},
            5: {"hp": 1200, "defense": 700, "attack": 700},
            6: {"hp": 1800, "defense": 900, "attack": 900},
            7: {"hp": 2500, "defense": 1100, "attack": 1100},
            8: {"hp": 3300, "defense": 1250, "attack": 1250},
            9: {"hp": 5000, "defense": 1400, "attack": 1400},
            10: {"hp": 7500, "defense": 1500, "attack": 1500}
        }

        return tier_stats.get(amulet['tier'], {}).get(amulet['type'], 0)


    @amulet.command(name="craft")
    @has_char()
    async def craft_amulet(self, ctx, type_: str, tier: int):
        """Craft an amulet of specified type and tier"""
        type_ = type_.lower()
        if type_ not in ['hp', 'defense', 'attack']:
            return await ctx.send("Invalid type! Choose: hp, defense, or attack")

        if tier not in range(1, 11):
            return await ctx.send("Invalid tier! Choose 1-10")

        async with self.bot.pool.acquire() as conn:
            # Check player level
            player = await conn.fetchrow(
                'SELECT xp FROM profile WHERE "user"=$1;',
                ctx.author.id
            )
            if not player:
                return await ctx.send("You need a character to craft!")

            player_level = rpgtools.xptolevel(player['xp'])
            if player_level < self.TIER_LEVELS[tier]:
                return await ctx.send(
                    f"You need to be level {self.TIER_LEVELS[tier]} to craft this!"
                )

            # Check resources
            recipe = self.AMULET_RECIPES[tier]
            missing_resources = []

            for resource, amount in recipe.items():
                has_resource = await conn.fetchval(
                    """SELECT amount FROM crafting_resources 
                    WHERE user_id=$1 AND resource_type=$2 AND amount>=$3;""",
                    ctx.author.id, resource, amount
                )

                if not has_resource:
                    missing_resources.append(f"{amount}x {resource.replace('_', ' ').title()}")

            if missing_resources:
                return await ctx.send(
                    f"Missing resources:\n" + "\n".join(missing_resources)
                )

            # Deduct resources
            for resource, amount in recipe.items():
                await conn.execute(
                    """UPDATE crafting_resources 
                    SET amount = amount - $1 
                    WHERE user_id=$2 AND resource_type=$3;""",
                    amount, ctx.author.id, resource
                )

            # Create amulet
            stat_value = self.TIER_STATS[tier][type_]
            await conn.execute(
                """INSERT INTO amulets (user_id, type, tier, hp, attack, defense)
                VALUES ($1, $2, $3, $4, $5, $6);""",
                ctx.author.id,
                type_,
                tier,
                stat_value if type_ == 'hp' else 0,
                stat_value if type_ == 'attack' else 0,
                stat_value if type_ == 'defense' else 0
            )

            # Get the emoji based on type
            emoji = "<:hpamulet:1318514711167373322>" if type_ == 'hp' else ("⚔️" if type_ == 'attack' else "🛡️")

            await ctx.send(
                f"Successfully crafted a Tier {tier} {type_.upper()} Amulet! {emoji}\n"
                f"Stats: +{stat_value} {type_.upper()}"
            )

    @amulet.command(name="equip")
    @has_char()
    async def equip_amulet(self, ctx, amulet_id: int):
        """Equip an amulet"""
        async with self.bot.pool.acquire() as conn:
            # Check if amulet exists and belongs to user
            amulet = await conn.fetchrow(
                'SELECT * FROM amulets WHERE id=$1 AND user_id=$2;',
                amulet_id, ctx.author.id
            )

            if not amulet:
                return await ctx.send("You don't own this amulet!")

            if amulet['equipped']:
                return await ctx.send("This amulet is already equipped!")

            # Check if user has another amulet of same type equipped
            existing_equipped = await conn.fetchrow(
                'SELECT * FROM amulets WHERE user_id=$1 AND type=$2 AND equipped=true;',
                ctx.author.id, amulet['type']
            )

            if existing_equipped:
                return await ctx.send(f"You already have a {amulet['type'].upper()} amulet equipped!")

            # Equip the amulet
            await conn.execute(
                'UPDATE amulets SET equipped=true WHERE id=$1;',
                amulet_id
            )

            await ctx.send(f"Successfully equipped your Tier {amulet['tier']} {amulet['type'].upper()} amulet!")

    @amulet.command(name="unequip")
    @has_char()
    async def unequip_amulet(self, ctx, amulet_id: int):
        """Unequip an amulet"""
        async with self.bot.pool.acquire() as conn:
            # Check if amulet exists and belongs to user
            amulet = await conn.fetchrow(
                'SELECT * FROM amulets WHERE id=$1 AND user_id=$2;',
                amulet_id, ctx.author.id
            )

            if not amulet:
                return await ctx.send("You don't own this amulet!")

            if not amulet['equipped']:
                return await ctx.send("This amulet is not equipped!")

            # Unequip the amulet
            await conn.execute(
                'UPDATE amulets SET equipped=false WHERE id=$1;',
                amulet_id
            )

            await ctx.send(f"Successfully unequipped your Tier {amulet['tier']} {amulet['type'].upper()} amulet!")


async def setup(bot):
    await bot.add_cog(AmuletCrafting(bot))