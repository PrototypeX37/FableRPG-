"""
Pet Skills Cog
Provides: XP/leveling helpers, skill trees, learn/unlearn, listings, testing utilities.
"""

from __future__ import annotations

import json
import datetime as dt
from typing import Any, Dict, List, Optional

import discord
from discord.ext import commands

# Cooldown decorator you already use elsewhere
from cogs.shard_communication import user_on_cooldown as user_cooldown

# i18n helper
from utils.i18n import _


class PetsSkills(commands.Cog):
    """All pet skills, leveling helpers, listings, and testing utilities."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # ---- TRUST TABLE (informational for future use) ----
        self.TRUST_LEVELS = {
            0: {"name": "Distrustful", "bonus": -20, "emoji": "😠"},
            21: {"name": "Cautious", "bonus": 0, "emoji": "😐"},
            41: {"name": "Trusting", "bonus": 10, "emoji": "😊"},
            61: {"name": "Loyal", "bonus": 20, "emoji": "😍"},
            81: {"name": "Devoted", "bonus": 30, "emoji": "🥰"},
        }

        # ---- SKILL TREES (8 elements × 3 branches × 5 skills) ----
        # Keep names EXACT, they’re used to match lookups and Battery Life reductions.
        self.SKILL_TREES: Dict[str, Dict[str, Dict[int, Dict[str, Any]]]] = {
            "Fire": {
                "Inferno": {
                    1: {"name": "Flame Burst", "description": "15% chance to deal 1.5x damage on attacks", "cost": 1},
                    3: {"name": "Burning Rage", "description": "Below 30% HP: +25% attack until healed", "cost": 2},
                    5: {"name": "Phoenix Strike", "description": "Crits heal pet for 15% of damage dealt", "cost": 3},
                    7: {"name": "Molten Armor", "description": "20% reflect 40% of received damage", "cost": 4},
                    10: {"name": "Inferno Mastery", "description": "ULT: 2x fire skills + 30% fire resist (low HP)", "cost": 5},
                },
                "Ember": {
                    1: {"name": "Warmth", "description": "Owner heals 5% pet max HP when pet attacks", "cost": 1},
                    3: {"name": "Fire Shield", "description": "20% chance to block an attack completely", "cost": 2},
                    5: {"name": "Combustion", "description": "On death: 200% attack fire AOE", "cost": 3},
                    7: {"name": "Eternal Flame", "description": "While owner >50% HP, pet cannot die (min 1 HP)", "cost": 4},
                    10: {"name": "Phoenix Rebirth", "description": "ULT: revive once at 50% HP (low HP trigger)", "cost": 5},
                },
                "Blaze": {
                    1: {"name": "Fire Affinity", "description": "+20% vs Nature/Water", "cost": 1},
                    3: {"name": "Heat Wave", "description": "AOE: 70% splash to nearby enemies", "cost": 2},
                    5: {"name": "Flame Barrier", "description": "Shield = 300% of defense", "cost": 3},
                    7: {"name": "Burning Spirit", "description": "30% chance to burn (10% max HP ×3 turns)", "cost": 4},
                    10: {"name": "Sun God's Blessing", "description": "ULT: 2.5x AOE + team +25% all stats 5 turns", "cost": 5},
                },
            },
            "Water": {
                "Tidal": {
                    1: {"name": "Water Jet", "description": "25% ignore armor/shields", "cost": 1},
                    3: {"name": "Tsunami Strike", "description": "+1% dmg / 2% current HP (max +50%)", "cost": 2},
                    5: {"name": "Deep Pressure", "description": "Targets <50% HP take +25% dmg", "cost": 3},
                    7: {"name": "Abyssal Grip", "description": "20% chance to stun 1 turn", "cost": 4},
                    10: {"name": "Ocean's Wrath", "description": "ULT: 2x AOE + heal allies 30% pet max HP", "cost": 5},
                },
                "Healing": {
                    1: {"name": "Purify", "description": "Start of turn: remove one random debuff from owner", "cost": 1},
                    3: {"name": "Healing Rain", "description": "Allies heal 8% pet max HP per turn", "cost": 2},
                    5: {"name": "Life Spring", "description": "Attacks heal owner 20% of damage dealt", "cost": 3},
                    7: {"name": "Guardian Wave", "description": "35% chance to reduce damage by 60%", "cost": 4},
                    10: {"name": "Immortal Waters", "description": "ULT: owner cannot die while pet lives", "cost": 5},
                },
                "Flow": {
                    1: {"name": "Water Affinity", "description": "+20% vs Fire/Electric", "cost": 1},
                    3: {"name": "Fluid Movement", "description": "25% chance to dodge", "cost": 2},
                    5: {"name": "Tidal Force", "description": "Push enemies back; delay next action", "cost": 3},
                    7: {"name": "Ocean's Embrace", "description": "Pet absorbs 50% of owner damage", "cost": 4},
                    10: {"name": "Poseidon's Call", "description": "ULT: Team +40% stats, enemies -30% stats 6 turns", "cost": 5},
                },
            },
            "Electric": {
                "Lightning": {
                    1: {"name": "Static Shock", "description": "30% paralyze 1 turn", "cost": 1},
                    3: {"name": "Thunder Strike", "description": "Crits chain to 2 enemies (60% each)", "cost": 2},
                    5: {"name": "Voltage Surge", "description": "+15% dmg per consecutive attack (max +75%)", "cost": 3},
                    7: {"name": "Lightning Rod", "description": "Absorb electric damage -> +25% attack 3 turns", "cost": 4},
                    10: {"name": "Storm Lord", "description": "ULT: 2.5x chain lightning + team acts twice 3 turns", "cost": 5},
                },
                "Energy": {
                    1: {"name": "Power Surge", "description": "Owner +15% attack 4 turns when pet attacks", "cost": 1},
                    3: {"name": "Energy Shield", "description": "Shield = 250% of defense", "cost": 2},
                    5: {"name": "Battery Life", "description": "Reduce skill costs by 1 (or 2 if cost≥4), min 1", "cost": 3},
                    7: {"name": "Overcharge", "description": "Pet -25% HP -> owner +50% all stats 3 turns", "cost": 4},
                    10: {"name": "Infinite Energy", "description": "ULT: Team +60% stats + unlimited abilities 4 turns", "cost": 5},
                },
                "Spark": {
                    1: {"name": "Electric Affinity", "description": "+20% vs Water/Nature", "cost": 1},
                    3: {"name": "Quick Charge", "description": "Always acts first", "cost": 2},
                    5: {"name": "Chain Lightning", "description": "Bounce to 3: 100%→75%→50%", "cost": 3},
                    7: {"name": "Electromagnetic Field", "description": "Enemies -25% accuracy", "cost": 4},
                    10: {"name": "Zeus's Wrath", "description": "ULT: 3x AOE + team debuff immunity 5 turns", "cost": 5},
                },
            },
            "Nature": {
                "Growth": {
                    1: {"name": "Vine Whip", "description": "25% root: -50% dmg 2 turns", "cost": 1},
                    3: {"name": "Photosynthesis", "description": "+20% attack in day fights", "cost": 2},
                    5: {"name": "Nature's Fury", "description": "+1% dmg / 2% happiness (max +50%)", "cost": 3},
                    7: {"name": "Thorn Shield", "description": "Return 35% damage as true poison", "cost": 4},
                    10: {"name": "Gaia's Wrath", "description": "ULT: 2x AOE + team heal 150% pet HP", "cost": 5},
                },
                "Life": {
                    1: {"name": "Natural Healing", "description": "Regen 6% max HP each turn", "cost": 1},
                    3: {"name": "Growth Spurt", "description": "+3% all stats/turn (max +30%)", "cost": 2},
                    5: {"name": "Life Force", "description": "Pet -30% HP -> owner heal 60% pet max HP", "cost": 3},
                    7: {"name": "Nature's Blessing", "description": "Team +20% all stats in nature areas", "cost": 4},
                    10: {"name": "Immortal Growth", "description": "ULT: Team regen 15%/turn + anti-poison 5 turns", "cost": 5},
                },
                "Harmony": {
                    1: {"name": "Nature Affinity", "description": "+20% vs Electric/Wind", "cost": 1},
                    3: {"name": "Forest Camouflage", "description": "30% avoid being targeted", "cost": 2},
                    5: {"name": "Symbiotic Bond", "description": "Share 50% healing/damage with owner", "cost": 3},
                    7: {"name": "Natural Balance", "description": "Transfer buffs/debuffs between allies/enemies", "cost": 4},
                    10: {"name": "World Tree's Gift", "description": "ULT: 2 turns control + team debuff immunity", "cost": 5},
                },
            },
            "Wind": {
                "Storm": {
                    1: {"name": "Wind Slash", "description": "25% true damage", "cost": 1},
                    3: {"name": "Gale Force", "description": "-30% enemy accuracy 1 turn on hit", "cost": 2},
                    5: {"name": "Tornado Strike", "description": "Persistent tornado 80% AOE ×3 turns", "cost": 3},
                    7: {"name": "Wind Shear", "description": "All enemies -40% defense 4 turns", "cost": 4},
                    10: {"name": "Storm Lord", "description": "ULT: 2.5x tornado + control positions 3 turns", "cost": 5},
                },
                "Freedom": {
                    1: {"name": "Wind Walk", "description": "+20% dodge chance", "cost": 1},
                    3: {"name": "Air Shield", "description": "Block projectiles + 50% other DR", "cost": 2},
                    5: {"name": "Wind's Guidance", "description": "Redirect 1 attack per turn", "cost": 3},
                    7: {"name": "Freedom's Call", "description": "Team +35% speed", "cost": 4},
                    10: {"name": "Sky's Blessing", "description": "ULT: Team 40% dodge + enemies lose 2 turns", "cost": 5},
                },
                "Breeze": {
                    1: {"name": "Wind Affinity", "description": "+20% vs Electric/Nature", "cost": 1},
                    3: {"name": "Swift Strike", "description": "Highest priority actions", "cost": 2},
                    5: {"name": "Wind Tunnel", "description": "Pull (+50% dmg) or push (-30% enemy dmg)", "cost": 3},
                    7: {"name": "Air Currents", "description": "Manipulate turn order for allies", "cost": 4},
                    10: {"name": "Zephyr's Dance", "description": "ULT: Team speed ×2; enemies 25% speed 6 turns", "cost": 5},
                },
            },
            "Light": {
                "Radiance": {
                    1: {"name": "Light Beam", "description": "30% blind: -50% acc 2 turns", "cost": 1},
                    3: {"name": "Holy Strike", "description": "+50% vs Dark/Corrupted", "cost": 2},
                    5: {"name": "Divine Wrath", "description": "Attacks remove enemy buffs", "cost": 3},
                    7: {"name": "Light Burst", "description": "120% primary, 60% splash AOE", "cost": 4},
                    10: {"name": "Solar Flare", "description": "ULT: 3x AOE + cleanse team", "cost": 5},
                },
                "Protection": {
                    1: {"name": "Divine Shield", "description": "40% dark resist +10% all resist", "cost": 1},
                    3: {"name": "Healing Light", "description": "Heal allies 12% pet max HP/turn", "cost": 2},
                    5: {"name": "Purification", "description": "Cleanse all team debuffs each turn", "cost": 3},
                    7: {"name": "Guardian Angel", "description": "Sacrifice to prevent owner death", "cost": 4},
                    10: {"name": "Divine Protection", "description": "ULT: Team invincible 3 turns + big heals", "cost": 5},
                },
                "Grace": {
                    1: {"name": "Light Affinity", "description": "+40% vs Dark/Corrupted", "cost": 1},
                    3: {"name": "Holy Aura", "description": "Team +20% dark resist + debuff resist", "cost": 2},
                    5: {"name": "Divine Favor", "description": "25% chance to bless ally (+30% stats 3 turns)", "cost": 3},
                    7: {"name": "Light's Guidance", "description": "Predict/counter enemy abilities", "cost": 4},
                    10: {"name": "Celestial Blessing", "description": "ULT: Team +50% stats + phys immunity 4 turns", "cost": 5},
                },
            },
            "Dark": {
                "Shadow": {
                    1: {"name": "Shadow Strike", "description": "25% bypass 50% defense", "cost": 1},
                    3: {"name": "Dark Embrace", "description": "+50% dmg when owner <50% HP", "cost": 2},
                    5: {"name": "Soul Drain", "description": "Lifesteal: heal 25% of dealt damage", "cost": 3},
                    7: {"name": "Shadow Clone", "description": "30% extra hit (75% damage)", "cost": 4},
                    10: {"name": "Void Mastery", "description": "ULT: 2.5x + invert enemy buffs to debuffs", "cost": 5},
                },
                "Corruption": {
                    1: {"name": "Dark Shield", "description": "Absorb dmg; convert 50% into attack 2 turns", "cost": 1},
                    3: {"name": "Soul Bind", "description": "Transfer 50% damage between allies", "cost": 2},
                    5: {"name": "Dark Pact", "description": "Pet -40% HP -> owner +100% dark power 4 turns", "cost": 3},
                    7: {"name": "Shadow Form", "description": "Intangible 2 turns (phys immune)", "cost": 4},
                    10: {"name": "Eternal Night", "description": "ULT: Team +75% dmg + global lifesteal 5 turns", "cost": 5},
                },
                "Night": {
                    1: {"name": "Dark Affinity", "description": "+40% vs Light/Corrupted", "cost": 1},
                    3: {"name": "Night Vision", "description": "See stealth + +20% acc in darkness", "cost": 2},
                    5: {"name": "Shadow Step", "description": "Teleport behind foe; guaranteed crit", "cost": 3},
                    7: {"name": "Dark Ritual", "description": "Sacrifice ally HP for massive pet power", "cost": 4},
                    10: {"name": "Lord of Shadows", "description": "ULT: Summon up to 2 skeleton warriors", "cost": 5},
                },
            },
            "Corrupted": {
                "Chaos": {
                    1: {"name": "Chaos Strike", "description": "Random 50–150% dmg + random element", "cost": 1},
                    3: {"name": "Reality Warp", "description": "Random effects (buff/debuff/heal/DoT)", "cost": 2},
                    5: {"name": "Void Touch", "description": "Permanent -10% all stats on hit (stack rules TBD)", "cost": 3},
                    7: {"name": "Chaos Storm", "description": "Chaos AOE with random effects", "cost": 4},
                    10: {"name": "Apocalypse", "description": "ULT: 3.5x global + chaos realm 5 turns", "cost": 5},
                },
                "Corruption": {
                    1: {"name": "Corrupt Shield", "description": "Absorb dmg; 25% corrupt attackers", "cost": 1},
                    3: {"name": "Reality Distortion", "description": "Manipulate mechanics (swap/reverse)", "cost": 2},
                    5: {"name": "Void Pact", "description": "Team +40% dmg; all -20% defense 5 turns", "cost": 3},
                    7: {"name": "Chaos Form", "description": "Unpredictable random effects each turn", "cost": 4},
                    10: {"name": "End of Days", "description": "ULT: Team chaos powers + reality breaks", "cost": 5},
                },
                "Void": {
                    1: {"name": "Corrupted Affinity", "description": "+30% vs ALL elements", "cost": 1},
                    3: {"name": "Void Sight", "description": "See illusions + 40% dodge", "cost": 2},
                    5: {"name": "Reality Tear", "description": "200% attack true damage (ignores all)", "cost": 3},
                    7: {"name": "Chaos Control", "description": "Swap positions / reverse damage, etc.", "cost": 4},
                    10: {"name": "Void Lord", "description": "ULT: 3x dmg + 50% DR + control 3 turns", "cost": 5},
                },
            },
        }

        # Kick off the schema updater (non-blocking)
        self.bot.loop.create_task(self.initialize_enhanced_tables())

    # ------------------------------------------------------------------------
    #                           DB / LEVEL HELPERS
    # ------------------------------------------------------------------------

    async def initialize_enhanced_tables(self):
        """Ensure enhanced skill columns exist on monster_pets."""
        try:
            async with self.bot.pool.acquire() as conn:
                await conn.execute(
                    """
                    ALTER TABLE monster_pets
                    ADD COLUMN IF NOT EXISTS trust_level INTEGER DEFAULT 0,
                    ADD COLUMN IF NOT EXISTS experience INTEGER DEFAULT 0,
                    ADD COLUMN IF NOT EXISTS level INTEGER DEFAULT 1,
                    ADD COLUMN IF NOT EXISTS skill_points INTEGER DEFAULT 0,
                    ADD COLUMN IF NOT EXISTS learned_skills JSONB DEFAULT '[]',
                    ADD COLUMN IF NOT EXISTS skill_tree_progress JSONB DEFAULT '{}',
                    ADD COLUMN IF NOT EXISTS xp_multiplier DECIMAL(3,1) DEFAULT 1.0
                    """
                )
        except Exception as e:
            print(f"[PetsSkills] Table init error: {e}")

    def calculate_level_requirements(self, level: int) -> int:
        """XP requirement for the given level (1..50)."""
        return int(100 * (level ** 3))

    def get_skill_points_for_level(self, level: int) -> int:
        """Grant 1 SP every 5 levels."""
        return 1 if level % 5 == 0 else 0

    async def gain_experience(self, pet_id: int, xp_amount: int, trust_gain: int = 0) -> Optional[dict]:
        """Apply XP (with multiplier) and trust; handle level-ups & SP awards."""
        async with self.bot.pool.acquire() as conn:
            pet = await conn.fetchrow(
                "SELECT experience, level, trust_level, skill_points, xp_multiplier FROM monster_pets WHERE id=$1",
                pet_id,
            )
            if not pet:
                return None

            xp_mult = float(pet.get("xp_multiplier", 1.0))
            adjusted = int(xp_amount * xp_mult)

            new_exp = int(pet["experience"]) + adjusted
            new_trust = min(100, int(pet["trust_level"]) + int(trust_gain))
            cur_level = int(pet["level"])
            new_level = cur_level
            new_sp = int(pet["skill_points"])

            while new_level < 50 and new_exp >= self.calculate_level_requirements(new_level + 1):
                new_level += 1
                new_sp += self.get_skill_points_for_level(new_level)

            await conn.execute(
                "UPDATE monster_pets SET experience=$1, level=$2, trust_level=$3, skill_points=$4 WHERE id=$5",
                new_exp,
                new_level,
                new_trust,
                new_sp,
                pet_id,
            )

        return {
            "leveled_up": new_level > cur_level,
            "new_level": new_level,
            "skill_points_gained": new_sp - int(pet["skill_points"]),
            "xp_multiplier_applied": xp_mult > 1.0,
            "original_xp": xp_amount,
            "adjusted_xp": adjusted,
        }

    async def award_battle_experience(self, pet_id: int, battle_xp: int, trust_gain: int = 1) -> Optional[dict]:
        """Wrapper used by combat systems."""
        try:
            return await self.gain_experience(pet_id, battle_xp, trust_gain)
        except Exception as e:
            print(f"[PetsSkills] award_battle_experience error for pet {pet_id}: {e}")
            return None

    # ------------------------------------------------------------------------
    #                      COST / JSON UTIL HELPERS
    # ------------------------------------------------------------------------

    def _extract_learned(self, learned_field: Any) -> List[str]:
        """Safely convert JSONB -> list[str]."""
        if isinstance(learned_field, list):
            return [str(x) for x in learned_field]
        if isinstance(learned_field, str):
            try:
                data = json.loads(learned_field)
                return [str(x) for x in data] if isinstance(data, list) else []
            except Exception:
                return []
        return []

    def calculate_skill_cost_with_battery_life(self, pet_record: Any, base_cost: int) -> int:
        """
        If pet knows 'Battery Life', reduce cost by 1 (or 2 if base_cost >= 4). Min 1.
        """
        learned = self._extract_learned(pet_record.get("learned_skills"))
        has_battery = any(s.lower() == "battery life" for s in learned)
        if not has_battery:
            return base_cost
        if base_cost >= 4:
            return max(1, base_cost - 2)
        return max(1, base_cost - 1)

    async def _resolve_target_pet(self, conn, ctx: commands.Context, pet_id: Optional[int]):
        """Resolve an explicit pet id, or fall back to the equipped pet."""
        if pet_id is not None:
            pet = await conn.fetchrow(
                "SELECT * FROM monster_pets WHERE user_id=$1 AND id=$2",
                ctx.author.id,
                pet_id,
            )
            if not pet:
                await ctx.send(f"❌ You don't have a pet with ID {pet_id}.")
                return None, None
            return int(pet_id), pet

        pet = await conn.fetchrow(
            "SELECT * FROM monster_pets WHERE user_id=$1 AND equipped=TRUE",
            ctx.author.id,
        )
        if not pet:
            await ctx.send(
                "❌ No pet ID provided and no pet is equipped. "
                "Use `$pets equip <pet_id>` or pass a pet ID."
            )
            return None, None
        return int(pet["id"]), pet

    # ------------------------------------------------------------------------
    #                                COMMANDS
    # ------------------------------------------------------------------------

    @user_cooldown(1800)
    @commands.command(name="train", brief=_("Train your pet to gain experience and trust"))
    async def train(self, ctx: commands.Context, pet_id: Optional[int] = None):
        async with self.bot.pool.acquire() as conn:
            resolved_pet_id, pet = await self._resolve_target_pet(conn, ctx, pet_id)
        if not pet:
            return
        pet_id = resolved_pet_id

        xp_gain, trust_gain = 50, 2
        async with self.bot.pool.acquire() as conn:
            await conn.execute("UPDATE monster_pets SET last_update=$1 WHERE id=$2", dt.datetime.utcnow(), pet_id)

        level_result = await self.gain_experience(pet_id, xp_gain, trust_gain)

        msgs = [
            f"🏋️ {pet['name']} trains hard and shows great improvement!",
            f"🎯 {pet['name']} masters a new technique during training!",
            f"⚡ {pet['name']} pushes its limits and grows stronger!",
            f"🌟 {pet['name']} learns valuable skills from the training session!",
            f"💪 {pet['name']} becomes more disciplined and focused!",
        ]
        import random as _r

        response = _r.choice(msgs)
        embed = discord.Embed(title="🏋️ Training Session", description=response, color=discord.Color.red())

        xp_mult_text = ""
        if level_result and level_result.get("xp_multiplier_applied"):
            xp_mult_text = f"\n**XP Multiplier:** x{level_result.get('original_xp', xp_gain)} → x{level_result.get('adjusted_xp', xp_gain)}"

        embed.add_field(
            name="📈 Effects",
            value=f"**XP Gained:** +{xp_gain}\n**Trust:** +{trust_gain}{xp_mult_text}",
            inline=True,
        )

        if level_result and level_result["leveled_up"]:
            embed.add_field(
                name="🎉 Level Up!",
                value=f"**{pet['name']}** reached level {level_result['new_level']}!\n**Skill Points:** +{level_result['skill_points_gained']}",
                inline=False,
            )
            embed.color = discord.Color.gold()

        await ctx.send(embed=embed)

    @commands.command(name="skilllist", brief=_("View all available skills for an element"))
    async def skilllist(self, ctx: commands.Context, element: Optional[str] = None):
        if element is None:
            embed = discord.Embed(
                title="🌳 Pet Element Skill Trees",
                description="Choose an element to view its complete skill tree:",
                color=discord.Color.blue(),
            )
            emap = {"Fire": "🔥", "Water": "💧", "Electric": "⚡", "Nature": "🌿", "Wind": "💨", "Light": "🌟", "Dark": "🌑", "Corrupted": "🌀"}
            elements_text = "\n".join(f"{emap.get(elem,'❓')} **{elem}**" for elem in self.SKILL_TREES.keys())
            embed.add_field(name="Available Elements", value=elements_text, inline=False)
            embed.set_footer(text="Use $pets skilllist <element> to view specific skills")
            await ctx.send(embed=embed)
            return

        element = element.capitalize()
        if element not in self.SKILL_TREES:
            await ctx.send(f"❌ Unknown element: {element}. Use `$pets skilllist` to see all elements.")
            return

        tree = self.SKILL_TREES[element]
        embed = discord.Embed(
            title=f"🌳 {element} Element - Complete Skill Tree",
            description=f"All skills available for {element} pets",
            color=discord.Color.purple(),
        )
        for branch_name, skills in tree.items():
            lines = []
            for lvl, data in skills.items():
                nm = data["name"]
                cost = data["cost"]
                desc = data["description"]
                lines.append(f"**{nm}** (Lv.{lvl} | {cost}SP)\n*{desc[:80]}{'...' if len(desc)>80 else ''}*")
            embed.add_field(name=f"🌿 {branch_name} Branch", value="\n\n".join(lines), inline=False)

        embed.set_footer(text="Use $pets skillinfo <skill_name> for detailed information!")
        await ctx.send(embed=embed)

    @commands.command(name="skills", brief=_("View your pet's skill tree and progress"))
    async def skills(self, ctx: commands.Context, pet_id: Optional[int] = None):
        async with self.bot.pool.acquire() as conn:
            resolved_pet_id, pet = await self._resolve_target_pet(conn, ctx, pet_id)
        if not pet:
            return
        pet_id = resolved_pet_id

        element = str(pet["element"])
        if element not in self.SKILL_TREES:
            await ctx.send(f"❌ {pet['name']} has an unknown element: {element}")
            return

        learned = self._extract_learned(pet.get("learned_skills"))
        emap = {"Fire": "🔥", "Water": "💧", "Electric": "⚡", "Nature": "🌿", "Wind": "💨", "Light": "🌟", "Dark": "🌑", "Corrupted": "🌀"}
        eemoji = emap.get(element, "❓")

        embed = discord.Embed(
            title=f"🌳 {pet['name']}'s Skill Tree ({eemoji} {element})",
            description=f"**Level:** {pet['level']}/50 | **Skill Points:** {pet['skill_points']} | **Trust:** {pet['trust_level']}/100\n"
                        f"**Learned Skills:** {len(learned)}/15",
            color=discord.Color.blue(),
        )

        tree = self.SKILL_TREES[element]
        for branch_name, skills in tree.items():
            branch_text = []
            learned_in_branch = 0
            for lvl, data in skills.items():
                nm = data["name"]
                base_cost = data["cost"]
                actual_cost = self.calculate_skill_cost_with_battery_life(pet, base_cost)
                if nm in learned:
                    branch_text.append(f"✅ **{nm}** (Lv.{lvl})\n   *{data['description']}*")
                    learned_in_branch += 1
                elif pet["level"] >= lvl and pet["skill_points"] >= actual_cost:
                    cost_disp = f"{actual_cost}SP" + (f" (was {base_cost}SP)" if actual_cost < base_cost else "")
                    branch_text.append(f"🔓 **{nm}** (Lv.{lvl} | {cost_disp})\n   *{data['description']}*")
                elif pet["level"] >= lvl:
                    cost_disp = f"{actual_cost}SP" + (f" (was {base_cost}SP)" if actual_cost < base_cost else "")
                    branch_text.append(f"💰 **{nm}** (Lv.{lvl} | **{cost_disp} needed**)\n   *{data['description']}*")
                else:
                    cost_disp = f"{actual_cost}SP" + (f" (was {base_cost}SP)" if actual_cost < base_cost else "")
                    branch_text.append(f"🔒 **{nm}** (Lv.{lvl} | {cost_disp})\n   *Reach level {lvl} to unlock*")

            if branch_text:
                embed.add_field(
                    name=f"🌿 {branch_name} Branch ({learned_in_branch}/5 learned)",
                    value="\n\n".join(branch_text),
                    inline=False,
                )

        embed.add_field(
            name="📚 Quick Help",
            value="**Legend:**\n✅ = Learned | 🔓 = Can Learn | 💰 = Need SP | 🔒 = Need Level",
            inline=True,
        )
        embed.set_footer(text="Use $pets skillinfo <skill_name> for detailed info, or $pets learn [pet_id] <skill>.")
        await ctx.send(embed=embed)

    @commands.command(name="skillinfo", brief=_("View detailed information about a specific skill"))
    async def skillinfo(self, ctx: commands.Context, *args):
        if not args:
            await ctx.send("❌ Please provide a skill name.")
            return

        pet_id: Optional[int] = None
        parts = list(args)
        if len(parts) > 1 and parts[0].isdigit():
            pet_id = int(parts.pop(0))
        skill_query = " ".join(parts).strip().lower()

        # locate skill
        found, element, branch, req_lvl = None, None, None, None
        for elem, branches in self.SKILL_TREES.items():
            for br, skills in branches.items():
                for lvl, data in skills.items():
                    if data["name"].lower() == skill_query:
                        found, element, branch, req_lvl = data, elem, br, lvl
                        break
                if found:
                    break
            if found:
                break

        if not found:
            await ctx.send(f"❌ Skill '{' '.join(parts)}' not found in any skill tree.")
            return

        cost_disp = f"{found['cost']} SP"
        pet = None
        if pet_id:
            async with self.bot.pool.acquire() as conn:
                pet = await conn.fetchrow(
                    "SELECT * FROM monster_pets WHERE user_id=$1 AND id=$2",
                    ctx.author.id,
                    pet_id,
                )
            if pet:
                reduced = self.calculate_skill_cost_with_battery_life(pet, int(found["cost"]))
                cost_disp = f"{reduced} SP" + (f" (reduced from {found['cost']} SP by Battery Life!)" if reduced < found["cost"] else "")

        embed = discord.Embed(
            title=f"📖 Skill Information: {found['name']}",
            description=f"**Element:** {element} | **Branch:** {branch}",
            color=discord.Color.blue(),
        )
        embed.add_field(name="📝 Description", value=found["description"], inline=False)
        embed.add_field(name="🔍 Requirements", value=f"**Level Required:** {req_lvl}\n**Skill Points Required:** {cost_disp}", inline=True)
        if pet:
            can = "✅ Yes" if (pet["skill_points"] >= (self.calculate_skill_cost_with_battery_life(pet, int(found["cost"]))) and pet["level"] >= req_lvl) else "❌ No"
            embed.add_field(
                name="🐾 Pet Context",
                value=f"**Pet:** {pet['name']}\n**Current SP:** {pet['skill_points']}\n**Can Learn:** {can}",
                inline=False,
            )
        embed.set_footer(text=f"Use $pets learn [pet_id] \"{found['name']}\" to learn this skill!")
        await ctx.send(embed=embed)

    @commands.command(name="learn", brief=_("Learn a skill for your pet"))
    async def learn(self, ctx: commands.Context, pet_id: Optional[int] = None, *, skill_name: str):
        async with self.bot.pool.acquire() as conn:
            resolved_pet_id, pet = await self._resolve_target_pet(conn, ctx, pet_id)
        if not pet:
            return
        pet_id = resolved_pet_id

        element = str(pet["element"])
        if element not in self.SKILL_TREES:
            await ctx.send(f"❌ {pet['name']} has an unknown element: {element}")
            return

        target = skill_name.strip().lower()
        found, branch, req_lvl = None, None, None
        for br, skills in self.SKILL_TREES[element].items():
            for lvl, data in skills.items():
                if data["name"].lower() == target:
                    found, branch, req_lvl = data, br, lvl
                    break
            if found:
                break

        if not found:
            await ctx.send(f"❌ Skill '{skill_name}' not found in {element} tree.")
            return

        learned = self._extract_learned(pet.get("learned_skills"))
        if found["name"] in learned:
            await ctx.send(f"❌ {pet['name']} already knows **{found['name']}**.")
            return

        base_cost = int(found["cost"])
        actual_cost = self.calculate_skill_cost_with_battery_life(pet, base_cost)
        if int(pet["skill_points"]) < actual_cost:
            await ctx.send(f"❌ {pet['name']} needs {actual_cost} skill points to learn **{found['name']}**.")
            return
        if int(pet["level"]) < int(req_lvl):
            await ctx.send(f"❌ {pet['name']} needs to be level {req_lvl} to learn **{found['name']}**.")
            return

        new_learned = learned + [found["name"]]
        try:
            async with self.bot.pool.acquire() as conn:
                await conn.execute(
                    "UPDATE monster_pets SET learned_skills=$1, skill_points=skill_points-$2 WHERE id=$3",
                    json.dumps(new_learned),
                    actual_cost,
                    pet_id,
                )
        except Exception as e:
            await ctx.send(f"❌ Failed to learn skill: {e}")
            return

        cost_display = f"{actual_cost} SP" + (f" (reduced from {base_cost} SP by Battery Life!)" if actual_cost < base_cost else "")
        embed = discord.Embed(
            title="🎓 Skill Learned!",
            description=f"**{pet['name']}** has learned **{found['name']}**!",
            color=discord.Color.green(),
        )
        embed.add_field(
            name="📚 Skill Details",
            value=f"**Branch:** {branch}\n**Level Required:** {req_lvl}\n**Cost:** {cost_display}\n**Description:** {found['description']}",
            inline=False,
        )
        await ctx.send(embed=embed)

    @commands.command(name="unlearn", brief=_("Unlearn a skill from your pet and refund SP"))
    async def unlearn(self, ctx: commands.Context, pet_id: Optional[int] = None, *, skill_name: str):
        """
        Refund uses *current* cost rules (including Battery Life if present now).
        """
        async with self.bot.pool.acquire() as conn:
            resolved_pet_id, pet = await self._resolve_target_pet(conn, ctx, pet_id)
        if not pet:
            return
        pet_id = resolved_pet_id

        element = str(pet["element"])
        if element not in self.SKILL_TREES:
            await ctx.send(f"❌ {pet['name']} has an unknown element: {element}")
            return

        wanted = skill_name.strip().lower()
        found, branch, req_lvl = None, None, None
        for br, skills in self.SKILL_TREES[element].items():
            for lvl, data in skills.items():
                if data["name"].lower() == wanted:
                    found, branch, req_lvl = data, br, lvl
                    break
            if found:
                break

        if not found:
            await ctx.send(f"❌ Skill '{skill_name}' not found in {element} tree.")
            return

        learned = self._extract_learned(pet.get("learned_skills"))
        lower_map = {s.lower(): s for s in learned}
        if wanted not in lower_map:
            await ctx.send(f"❌ {pet['name']} hasn't learned **{found['name']}**.")
            return

        # (Optional) Dependency checks for branch ordering would go here.

        base_cost = int(found["cost"])
        refund_sp = self.calculate_skill_cost_with_battery_life(pet, base_cost)
        refund_sp = max(1, min(base_cost, refund_sp))  # safety clamp

        new_learned = [s for s in learned if s.lower() != wanted]
        try:
            async with self.bot.pool.acquire() as conn:
                await conn.execute(
                    "UPDATE monster_pets SET learned_skills=$1, skill_points=skill_points+$2 WHERE id=$3",
                    json.dumps(new_learned),
                    refund_sp,
                    pet_id,
                )
        except Exception as e:
            await ctx.send(f"❌ Failed to unlearn skill: {e}")
            return

        note = f"{refund_sp} SP"
        if refund_sp < base_cost:
            note += " (reduced cost due to Battery Life)"
        embed = discord.Embed(
            title="♻️ Skill Unlearned",
            description=f"**{pet['name']}** forgot **{found['name']}**",
            color=discord.Color.orange(),
        )
        embed.add_field(
            name="📚 Details",
            value=f"**Branch:** {branch}\n**Required Lv:** {req_lvl}\n**Original Cost:** {base_cost} SP\n**Refund:** {note}",
            inline=False,
        )
        await ctx.send(embed=embed)

        # --- inside class PetsSkills(...): add this ---
    @commands.command(name="skillshelp", brief=_("Help for pet skills & leveling"))
    async def skillshelp(self, ctx: commands.Context):
        embed = discord.Embed(
            title="📚 Pet Skills & Leveling — Quick Help",
            description=(
                "Manage your pet's skills, see trees, and learn/unlearn abilities.\n"
                "All commands are subcommands of **$pets**."
            ),
            color=discord.Color.blurple()
        )
        embed.add_field(
            name="🔎 Browse & Inspect",
            value=(
                "• **$pets skillshelp** — this help\n"
                "• **$pets skilllist** — list all elements\n"
                "• **$pets skilllist Fire** — full Fire tree\n"
                "• **$pets skillinfo 123 Battery Life** — details for a skill (optional pet id)\n"
                "• **$pets skills [pet_id]** — your pet’s tree & progress"
            ),
            inline=False
        )
        embed.add_field(
            name="🎓 Learn / Unlearn",
            value=(
                "• **$pets learn [pet_id] \"Battery Life\"** — learn skill\n"
                "• **$pets unlearn [pet_id] \"Battery Life\"** — refund SP (current-cost rules)\n"
                "_Tip:_ Knowing **Battery Life** reduces other skill SP costs (−1, or −2 if cost ≥ 4)."
            ),
            inline=False
        )
        embed.add_field(
            name="⚔️ Progression",
            value=(
                "• Levels 1–50, **1 Skill Point every 5 levels** (5, 10, 15…)\n"
                "• **Trust** tiers give battle bonuses (−20% → +30%)\n"
                "• XP can be boosted by an **XP Multiplier** if active"
            ),
            inline=False
        )
        embed.set_footer(text="Example: $pets learn \"Phoenix Strike\" (uses equipped pet)")
        await ctx.send(embed=embed)

        

    # ------------------------------------------------------------------------
    #               OPTIONAL DEV / QA TEST COMMANDS (owner-only)
    # ------------------------------------------------------------------------

    @commands.is_owner()
    @commands.command(name="testbatterylife", brief=_("Test Battery Life cost reduction"))
    async def testbatterylife(self, ctx: commands.Context, pet_id: Optional[int] = None):
        async with self.bot.pool.acquire() as conn:
            resolved_pet_id, pet = await self._resolve_target_pet(conn, ctx, pet_id)
        if not pet:
            return
        pet_id = resolved_pet_id

        learned = self._extract_learned(pet.get("learned_skills"))
        has_battery = any(s.lower() == "battery life" for s in learned)

        embed = discord.Embed(
            title="🔋 Battery Life Test",
            description=f"Testing cost reduction for **{pet['name']}**",
            color=discord.Color.blue(),
        )
        embed.add_field(
            name="📊 Pet Status",
            value=f"**Has Battery Life:** {'✅ Yes' if has_battery else '❌ No'}\n**Skill Points:** {pet['skill_points']}\n**Learned Skills:** {len(learned)}",
            inline=False,
        )

        tests = [1, 2, 3, 4, 5]
        lines = []
        for c in tests:
            r = self.calculate_skill_cost_with_battery_life(pet, c)
            if r < c:
                lines.append(f"**{c} SP** → **{r} SP** (reduced by {c-r})")
            else:
                lines.append(f"**{c} SP** → **{r} SP** (no change)")
        embed.add_field(name="💰 Cost Reduction Test", value="\n".join(lines), inline=False)
        await ctx.send(embed=embed)


# ------------------------------------------------------------------------
#                                 SETUP
# ------------------------------------------------------------------------

async def setup(bot: commands.Bot):
    cog = PetsSkills(bot)
    await bot.add_cog(cog)

    parent = bot.get_cog("Pets")
    if not parent or not hasattr(parent, "pets"):
        bot.logger.warning("[PetsSkill] Parent 'Pets' cog or group missing. Load 'cogs.pets' before 'cogs.petsskill'.")
        return

    group: commands.Group = parent.pets  # type: ignore

    desired = (
        "train",
        "skills",
        "skillinfo",
        "skilllist",
        "learn",
        "unlearn",
        "skillshelp",
        "testbatterylife",  # owner-only
    )

    # 1) If the names already exist under $pets, remove them to avoid duplicates.
    for name in desired:
        existing_in_group = group.get_command(name)
        if existing_in_group:
            group.remove_command(existing_in_group.name)

    # 2) Grab the top-level commands that were registered by @commands.command,
    #    remove them from the bot root, then re-attach under $pets.
    for name in desired:
        cmd = bot.get_command(name)
        if cmd:
            bot.remove_command(name)
            group.add_command(cmd)
        else:
            bot.logger.warning(f"[PetsSkill] Command '{name}' not found on bot; check the method/decorator.")
