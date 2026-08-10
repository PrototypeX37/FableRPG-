from datetime import datetime, timezone
# battles/types/tower.py
import asyncio
import random
from decimal import Decimal
import discord
import datetime

from ..core.battle import Battle
from ..core.team import Team

class TowerBattle(Battle):
    """Battle tower battle implementation"""

    def __init__(self, ctx, teams, **kwargs):
        super().__init__(ctx, teams, **kwargs)
        self.level = kwargs.get("level", 1)
        self.level_data = kwargs.get("level_data", {})
        self.current_turn = 0
        self.turn_order = []
        self.current_opponent_index = 0  # Start with first opponent
        self.pending_enemy_transition = False  # Flag for enemy transitions
        self.transition_state = 0  # 0=normal, 1=intro, 2=battle start
        self.battle_timed_out = False  # Explicit flag for timeout
        self.action_number = 1  # Initialize action counter

        # Separate teams for clarity
        self.player_team = teams[0]
        self.enemy_team = teams[1]

        # Reference to enemy combatants for easier access
        self.current_enemies = [self.enemy_team.combatants[self.current_opponent_index]]

        # Load all battle settings
        settings_cog = self.ctx.bot.get_cog("BattleSettings")
        if settings_cog:
            self.config = {
                "allow_pets": settings_cog.get_setting("tower", "allow_pets", default=True),
                "class_buffs": settings_cog.get_setting("tower", "class_buffs", default=True),
                "element_effects": settings_cog.get_setting("tower", "element_effects", default=True),
                "luck_effects": settings_cog.get_setting("tower", "luck_effects", default=True),
                "reflection_damage": settings_cog.get_setting("tower", "reflection_damage", default=True),
                "fireball_chance": settings_cog.get_setting("tower", "fireball_chance", default=0.3),
                "cheat_death": settings_cog.get_setting("tower", "cheat_death", default=True),
                "tripping": settings_cog.get_setting("tower", "tripping", default=True),
                "status_effects": settings_cog.get_setting("tower", "status_effects", default=False),
                "pets_continue_battle": settings_cog.get_setting("tower", "pets_continue_battle", default=False),
            }
        else:
            self.config = {
                "allow_pets": True,
                "class_buffs": True,
                "element_effects": True,
                "luck_effects": True,
                "reflection_damage": True,
                "fireball_chance": 0.3,
                "cheat_death": True,
                "tripping": True,
                "status_effects": False,
                "pets_continue_battle": False,
            }

    async def add_to_log(self, message, force_new_action=True):
        """Add a message to the battle log"""
        if force_new_action or not self.log:
            if not hasattr(self, "action_number"):
                self.action_number = 1
            self.log.append((self.action_number, message))
            self.action_number += 1
        else:
            action_count, old_message = self.log[-1]
            self.log[-1] = (action_count, f"{old_message}\n{message}")
        await self.capture_turn_state(message)

    async def start_battle(self):
        """Initialize and start the battle"""
        self.started = True
        self.start_time = datetime.datetime.now(timezone.utc)

        # Save initial battle data to database for replay
        await self.save_battle_to_database()

        # Build initial turn order with player team and first enemy
        self.update_turn_order()

        # Intro
        first_enemy = self.enemy_team.combatants[self.current_opponent_index]
        await self.add_to_log(f"Prepare to face {first_enemy.name}!", force_new_action=True)

        # Create and send initial embed
        embed = await self.create_battle_embed()
        self.battle_message = await self.ctx.send(embed=embed)

        await asyncio.sleep(3)

        await self.add_to_log(f"Battle against {first_enemy.name} has begun!", force_new_action=True)
        await self.update_display()
        return True

    def update_turn_order(self):
        """Update turn order based on current combatants"""
        self.turn_order = []
        for combatant in self.player_team.combatants:
            if combatant.is_alive():
                self.turn_order.append(combatant)
        current_enemy = self.enemy_team.combatants[self.current_opponent_index]
        if current_enemy.is_alive():
            self.turn_order.append(current_enemy)
        random.shuffle(self.turn_order)

    async def handle_enemy_transition(self):
        """Handle transition to the next enemy as a combined action"""
        self.current_opponent_index += 1
        current_enemy = self.enemy_team.combatants[self.current_opponent_index]

        await self.add_to_log(f"Prepare to face {current_enemy.name}!", force_new_action=True)
        await self.update_display()
        await asyncio.sleep(2)

        await self.add_to_log(f"Battle with {current_enemy.name} begins!", force_new_action=False)
        self.update_turn_order()
        await self.update_display()

        self.pending_enemy_transition = False
        self.transition_state = 0
        return True

    async def process_turn(self):
        """Process a single turn of the battle"""
        if self.pending_enemy_transition:
            return await self.handle_enemy_transition()

        if await self.is_battle_over():
            return False

        if not self.turn_order:
            self.update_turn_order()

        current_combatant = self.turn_order[self.current_turn % len(self.turn_order)]
        self.current_turn += 1

        if not current_combatant.is_alive():
            return True

        # Decide attacker/target
        if current_combatant in self.player_team.combatants:
            attacker_team = self.player_team
            current_enemy = self.enemy_team.combatants[self.current_opponent_index]
            if not current_enemy.is_alive():
                if self.current_opponent_index < len(self.enemy_team.combatants) - 1:
                    await self.update_display()
                    self.pending_enemy_transition = True
                    self.transition_state = 1
                    return True
                else:
                    return False
            target = current_enemy
        else:
            attacker_team = self.enemy_team
            alive_players = [c for c in self.player_team.combatants if c.is_alive()]
            if not alive_players:
                return False
            weighted_targets, weights = [], []
            for player in alive_players:
                weighted_targets.append(player)
                weights.append(0.4 if player.is_pet else 0.6)
            total_weight = sum(weights)
            weights = [w / total_weight for w in weights]
            target = random.choices(weighted_targets, weights=weights)[0]

        # Hit/miss
        luck_roll = random.randint(1, 100)
        if current_combatant in self.enemy_team.combatants:
            hit_success = random.random() > 0.10
        else:
            has_perfect_accuracy = getattr(current_combatant, "perfect_accuracy", False)
            hit_success = has_perfect_accuracy or (luck_roll <= current_combatant.luck)

        message = ""
        blocked_damage = Decimal("0")
        raw_damage = current_combatant.damage

        # Resolve Battles cog safely
        battles = self.ctx.bot.get_cog("Battles")
        element_ext = getattr(battles, "element_ext", None) if battles else None
        battle_factory = getattr(battles, "battle_factory", None) if battles else None
        pet_ext = getattr(battle_factory, "pet_ext", None) if battle_factory else None

        if hit_success:
            # Mage fireball
            used_fireball = False
            if (
                current_combatant.mage_evolution
                and not current_combatant.is_pet
                and self.config["class_buffs"]
                and random.random() < self.config["fireball_chance"]
            ):
                evolution_level = current_combatant.mage_evolution
                damage_multiplier = {
                    1: 1.10,
                    2: 1.20,
                    3: 1.30,
                    4: 1.50,
                    5: 1.75,
                    6: 2.00,
                }.get(evolution_level, 1.0)

                damage = (current_combatant.damage + Decimal(random.randint(0, 100)) - target.armor) * Decimal(
                    str(damage_multiplier)
                )
                damage = max(damage, Decimal("1"))

                target.take_damage(damage)
                message = f"{current_combatant.name} casts Fireball! {target.name} takes **{self.format_number(damage)} HP** damage."
                used_fireball = True
            else:
                # Regular attack
                damage_variance = random.randint(0, 50) if current_combatant.is_pet else random.randint(0, 100)
                raw_damage = current_combatant.damage

                # Element effects (safe)
                if self.config["element_effects"] and element_ext:
                    element_mod = element_ext.calculate_damage_modifier(
                        self.ctx, current_combatant.element, target.element
                    )
                    if element_mod != 0:
                        raw_damage = raw_damage * (1 + Decimal(str(element_mod)))

                raw_damage += Decimal(damage_variance)

                # Pet attack effects (safe)
                skill_messages = []
                if current_combatant.is_pet and pet_ext:
                    raw_damage, skill_messages = pet_ext.process_skill_effects_on_attack(
                        current_combatant, target, raw_damage
                    )
                    setattr(current_combatant, "attacked_this_turn", True)

                # Special damage flags
                ignore_armor = getattr(target, "ignore_armor_this_hit", False)
                true_damage = getattr(target, "true_damage", False)
                bypass_defenses = getattr(target, "bypass_defenses", False)
                ignore_all = getattr(target, "ignore_all_defenses", False)
                partial_true_damage = getattr(target, "partial_true_damage", 0)

                if ignore_all or true_damage or ignore_armor or bypass_defenses:
                    damage = raw_damage
                    blocked_damage = Decimal("0")
                elif partial_true_damage > 0:
                    normal_damage_after_armor = max(raw_damage - target.armor, Decimal("10"))
                    damage = normal_damage_after_armor + Decimal(str(partial_true_damage))
                    blocked_damage = min(raw_damage, target.armor)
                else:
                    blocked_damage = min(raw_damage, target.armor)
                    damage = max(raw_damage - target.armor, Decimal("10"))

                for flag in [
                    "ignore_armor_this_hit",
                    "true_damage",
                    "bypass_defenses",
                    "ignore_all_defenses",
                    "partial_true_damage",
                ]:
                    if hasattr(target, flag):
                        delattr(target, flag)

                # Pet defend effects (safe)
                defender_messages = []
                if target.is_pet and pet_ext:
                    damage, defender_messages = pet_ext.process_skill_effects_on_damage_taken(
                        target, current_combatant, damage
                    )

                if current_combatant.is_pet:
                    setattr(current_combatant, "last_damage_dealt", damage)

                target.take_damage(damage)
                message = f"{current_combatant.name} attacks! {target.name} takes **{self.format_number(damage)} HP** damage."

                if skill_messages:
                    message += "\n" + "\n".join(skill_messages)
                if defender_messages:
                    message += "\n" + "\n".join(defender_messages)

                # Skeleton summon (safe)
                if hasattr(current_combatant, "summon_skeleton"):
                    skeleton_data = current_combatant.summon_skeleton
                    from cogs.battles.core.combatant import Combatant

                    skeleton = Combatant(
                        user=f"Skeleton Warrior #{current_combatant.skeleton_count}",
                        hp=skeleton_data["hp"],
                        max_hp=skeleton_data["hp"],
                        damage=skeleton_data["damage"],
                        armor=skeleton_data["armor"],
                        element=skeleton_data["element"],
                        luck=50,
                        is_pet=True,
                        name=f"Skeleton Warrior #{current_combatant.skeleton_count}",
                    )
                    skeleton.is_summoned = True
                    skeleton.summoner = current_combatant

                    if current_combatant in self.player_team.combatants:
                        self.player_team.combatants.append(skeleton)
                        message += f"\n💀 A skeleton warrior joins your side!"
                    else:
                        self.enemy_team.combatants.append(skeleton)
                        message += f"\n💀 A skeleton warrior joins the enemy side!"

                    delattr(current_combatant, "summon_skeleton")

            # Lifesteal for non-pets
            if self.config["class_buffs"] and not current_combatant.is_pet and current_combatant.lifesteal_percent > 0:
                lifesteal_amount = (float(damage) * float(current_combatant.lifesteal_percent) / 100.0)
                current_combatant.heal(lifesteal_amount)
                message += f" Lifesteals: **{self.format_number(lifesteal_amount)} HP**"

            # Reflection
            reflection_value = target.damage_reflection
            if self.config["class_buffs"] and getattr(target, "tank_evolution", 0) and not target.is_pet:
                tank_reflection = 0.03 * target.tank_evolution
                reflection_value = max(reflection_value, tank_reflection)

            if self.config["reflection_damage"] and reflection_value > 0 and blocked_damage > 0:
                reflection_base = min(raw_damage, target.armor)
                reflected = reflection_base * Decimal(str(reflection_value))
                current_combatant.take_damage(reflected)
                message += f"\n{target.name}'s armor reflects **{self.format_number(reflected)} HP** damage back!"
                if not current_combatant.is_alive():
                    message += f" {current_combatant.name} has been defeated by reflected damage!"

            # Death checks / transition
            if not target.is_alive():
                if getattr(target, "water_immortality", False):
                    target.hp = Decimal("1")
                    message += f"\n💧 {target.name} is protected by Immortal Waters and refuses to fall!"
                elif (
                    self.config["class_buffs"]
                    and self.config["cheat_death"]
                    and not target.is_pet
                    and target in self.player_team.combatants
                    and target.death_cheat_chance > 0
                    and not getattr(target, "has_cheated_death", False)
                ):
                    cheat_roll = random.randint(1, 100)
                    if cheat_roll <= target.death_cheat_chance:
                        target.hp = Decimal("75")
                        target.has_cheated_death = True
                        message += f"\n{target.name} cheats death and survives with **75 HP**!"
                    else:
                        message += f" {target.name} has been defeated!"
                else:
                    message += f" {target.name} has been defeated!"
                    if target in self.enemy_team.combatants:
                        if target == self.enemy_team.combatants[self.current_opponent_index]:
                            if self.current_opponent_index < len(self.enemy_team.combatants) - 1:
                                self.pending_enemy_transition = True
                                self.transition_state = 1
        else:
            # Miss / trip
            if self.config.get("tripping", False):
                damage = Decimal("10")
                current_combatant.take_damage(damage)
                message = f"{current_combatant.name} tripped and took **{self.format_number(damage)} HP** damage. Bad luck!"
            else:
                message = f"{current_combatant.name}'s attack missed!"

        # Log action
        await self.add_to_log(message, force_new_action=True)

        # Per-turn pet effects (safe)
        if pet_ext:
            for combatant in self.player_team.combatants:
                if combatant.is_pet and combatant.is_alive():
                    setattr(combatant, "team", self.player_team)
                    setattr(combatant, "enemy_team", self.enemy_team)
                    turn_messages = pet_ext.process_skill_effects_per_turn(combatant)
                    if turn_messages:
                        for turn_msg in turn_messages:
                            await self.add_to_log(turn_msg)

            for combatant in self.enemy_team.combatants:
                if combatant.is_pet and combatant.is_alive():
                    setattr(combatant, "team", self.enemy_team)
                    setattr(combatant, "enemy_team", self.player_team)
                    turn_messages = pet_ext.process_skill_effects_per_turn(combatant)
                    if turn_messages:
                        for turn_msg in turn_messages:
                            await self.add_to_log(turn_msg)

        if hasattr(target, "is_alive") and not target.is_alive():
            if current_combatant.is_pet:
                setattr(current_combatant, "killed_enemy_this_turn", True)

        await self.update_display()
        await asyncio.sleep(1)
        return True

    async def create_battle_embed(self):
        """Create the battle status embed"""
        current_enemy = self.enemy_team.combatants[self.current_opponent_index]
        embed = discord.Embed(
            title=f"Battle Tower: Level {self.level} - {self.ctx.author.display_name} vs {current_enemy.name}",
            color=self.ctx.bot.config.game.primary_colour,
        )

        # Safe element emoji map
        battles = self.ctx.bot.get_cog("Battles")
        element_emoji_map = getattr(battles, "emoji_to_element", {}) if battles else {}

        # Player team
        for combatant in self.player_team.combatants:
            current_hp = max(0, float(combatant.hp))
            max_hp = float(combatant.max_hp)
            hp_bar = self.create_hp_bar(current_hp, max_hp)

            element_emoji = "❌"
            for emoji, element in element_emoji_map.items():
                if element == combatant.element:
                    element_emoji = emoji
                    break

            if combatant.is_pet:
                field_name = f"{combatant.name} {element_emoji}"
            else:
                field_name = f"**[TEAM A]** \n{combatant.display_name} {element_emoji}"

            field_value = f"HP: {current_hp:.1f}/{max_hp:.1f}\n{hp_bar}"
            if getattr(combatant, "damage_reflection", 0) > 0:
                reflection_percent = float(combatant.damage_reflection) * 100
                field_value += f"\nDamage Reflection: {reflection_percent:.1f}%"
            embed.add_field(name=field_name, value=field_value, inline=False)

        # Enemy
        current_hp = max(0, float(current_enemy.hp))
        max_hp = float(current_enemy.max_hp)
        hp_bar = self.create_hp_bar(current_hp, max_hp)

        element_emoji = "❌"
        for emoji, element in element_emoji_map.items():
            if element == current_enemy.element:
                element_emoji = emoji
                break

        field_name = f"**[TEAM B]** \n{current_enemy.name} {element_emoji}"
        field_value = f"HP: {current_hp:.1f}/{max_hp:.1f}\n{hp_bar}"
        embed.add_field(name=field_name, value=field_value, inline=False)

        # Log
        log_text = "\n\n".join([f"**Action #{i}**\n{msg}" for i, msg in self.log])
        embed.add_field(name="Battle Log", value=log_text or "Battle starting...", inline=False)

        embed.set_footer(text=f"Battle ID: {self.battle_id}")
        return embed

    async def update_display(self):
        """Update the battle display"""
        embed = await self.create_battle_embed()
        if self.battle_message:
            await self.battle_message.edit(embed=embed)
        else:
            self.battle_message = await self.ctx.send(embed=embed)

    async def end_battle(self):
        """End the battle and determine outcome"""
        self.finished = True

        player_char_defeated = not any(not c.is_pet and c.is_alive() for c in self.player_team.combatants)
        if player_char_defeated and not self.config.get("pets_continue_battle", False):
            await self.save_battle_to_database()
            return self.enemy_team

        if self.player_team.is_defeated():
            await self.save_battle_to_database()
            return self.enemy_team

        if all(not enemy.is_alive() for enemy in self.enemy_team.combatants):
            if not player_char_defeated or self.config.get("pets_continue_battle", False):
                await self.save_battle_to_database()
                return self.player_team
            else:
                await self.save_battle_to_database()
                return self.enemy_team

        if await self.is_timed_out():
            self.battle_timed_out = True
            await self.save_battle_to_database()
            return None

        if not player_char_defeated or self.config.get("pets_continue_battle", False):
            player_health_percent = sum(c.hp / c.max_hp for c in self.player_team.combatants) / len(
                self.player_team.combatants
            )
            enemy_health_percent = sum(c.hp / c.max_hp for c in self.enemy_team.combatants) / len(
                self.enemy_team.combatants
            )
            if player_health_percent > enemy_health_percent:
                await self.save_battle_to_database()
                return self.player_team

        await self.save_battle_to_database()
        return self.enemy_team

    async def is_battle_over(self):
        """Check if the battle is over"""
        if self.pending_enemy_transition:
            return False
        if self.finished:
            return True

        enemy_defeated = all(not c.is_alive() for c in self.enemy_team.combatants)
        if enemy_defeated:
            return True

        player_char_defeated = not any(not c.is_pet and c.is_alive() for c in self.player_team.combatants)
        if player_char_defeated:
            if self.config.get("pets_continue_battle", False) and self.config.get("allow_pets", True):
                all_defeated = all(not c.is_alive() for c in self.player_team.combatants)
                if all_defeated:
                    return True
            else:
                return True

        if await self.is_timed_out():
            self.battle_timed_out = True
            return True

        return False
