# battles/types/pve.py
import asyncio
import random
from collections import defaultdict
from decimal import Decimal
import discord

# >>> Use timezone-aware datetimes everywhere
from datetime import datetime, timezone, timedelta

from ..core.battle import Battle
from contextlib import suppress


class PvEBattle(Battle):
    """Player vs Environment (monster) battle implementation"""

    def __init__(self, ctx, teams, **kwargs):
        super().__init__(ctx, teams, **kwargs)
        self.player_team = teams[0]
        self.monster_team = teams[1]
        self.monster_level = kwargs.get("monster_level", 1)
        self.macro_penalty_level = kwargs.get("macro_penalty_level", 0)
        self.current_turn = 0
        self.attacker = None
        self.defender = None
        self.turn_order = []

        # Load all battle settings
        settings_cog = self.ctx.bot.get_cog("BattleSettings")
        if settings_cog:
            # Ensure all settings are loaded, with defaults if not found
            self.config = {
                "allow_pets": settings_cog.get_setting("pve", "allow_pets", default=True),
                "class_buffs": settings_cog.get_setting("pve", "class_buffs", default=True),
                "element_effects": settings_cog.get_setting("pve", "element_effects", default=True),
                "luck_effects": settings_cog.get_setting("pve", "luck_effects", default=True),
                "reflection_damage": settings_cog.get_setting("pve", "reflection_damage", default=True),
                "fireball_chance": settings_cog.get_setting("pve", "fireball_chance", default=0.3),
                "cheat_death": settings_cog.get_setting("pve", "cheat_death", default=True),
                "tripping": settings_cog.get_setting("pve", "tripping", default=True),
                "status_effects": settings_cog.get_setting("pve", "status_effects", default=False),
                "pets_continue_battle": settings_cog.get_setting("pve", "pets_continue_battle", default=False),
            }
        else:
            # Fallback default settings if settings cog is unavailable
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

        self.monster_abilities = kwargs.get("monster_abilities") or []
        self.special_ai = str(kwargs.get("special_ai", "")).strip().lower()
        self.spring_specials_enabled = (
            self.special_ai == "dreambound_spring" or bool(self.monster_abilities)
        )
        self.special_effects = defaultdict(list)

    @staticmethod
    def _to_decimal(value, default=Decimal("0")):
        try:
            return Decimal(str(value))
        except Exception:
            return default

    @staticmethod
    def _pct_to_ratio(value):
        pct = PvEBattle._to_decimal(value, Decimal("0"))
        if pct > 1:
            pct = pct / Decimal("100")
        return max(Decimal("0"), pct)

    def _is_monster(self, combatant):
        return combatant in self.monster_team.combatants

    def _get_active_effects(self, combatant, effect_type=None):
        if not self.spring_specials_enabled:
            return []
        bucket = self.special_effects.get(combatant.name, [])
        effects = [e for e in bucket if int(e.get("duration", 0)) != 0]
        if effect_type is None:
            return effects
        return [e for e in effects if str(e.get("type", "")) == effect_type]

    def _add_special_effect(
        self,
        target,
        effect_type,
        *,
        value=0,
        duration=1,
        name=None,
        stacks=1,
        max_stacks=1,
        stack=False,
    ):
        if not self.spring_specials_enabled:
            return None

        if duration != -1:
            duration = max(1, int(duration))

        value = self._to_decimal(value, Decimal("0"))
        stacks = max(1, int(stacks))
        max_stacks = max(1, int(max_stacks))
        effect_name = str(name or effect_type).strip()
        bucket = self.special_effects[target.name]

        existing = None
        for effect in bucket:
            if str(effect.get("type", "")) == effect_type:
                existing = effect
                break

        if existing:
            existing["value"] = value
            if duration == -1 or int(existing.get("duration", 0)) == -1:
                existing["duration"] = -1
            else:
                existing["duration"] = max(int(existing.get("duration", 1)), int(duration))
            if stack or max_stacks > 1:
                effective_max = max(
                    int(existing.get("max_stacks", 1)),
                    max_stacks,
                )
                existing["stacks"] = min(
                    effective_max,
                    int(existing.get("stacks", 1)) + stacks,
                )
                existing["max_stacks"] = effective_max
            else:
                existing["stacks"] = 1
                existing["max_stacks"] = max_stacks
            existing["name"] = effect_name
            return existing

        effect = {
            "type": effect_type,
            "value": value,
            "duration": duration,
            "name": effect_name,
            "stacks": stacks if (stack or max_stacks > 1) else 1,
            "max_stacks": max_stacks,
        }
        bucket.append(effect)
        return effect

    def _consume_special_effect(self, target, effect_type):
        bucket = self.special_effects.get(target.name, [])
        for effect in list(bucket):
            if str(effect.get("type", "")) != effect_type:
                continue
            if int(effect.get("duration", 0)) == 0:
                continue
            bucket.remove(effect)
            if not bucket:
                self.special_effects.pop(target.name, None)
            return effect
        return None

    def _get_outgoing_damage_multiplier(self, combatant):
        multiplier = Decimal("1")
        for effect in self._get_active_effects(combatant, "damage_mult"):
            value = self._to_decimal(effect.get("value", 1), Decimal("1"))
            if value <= 0:
                continue
            multiplier *= value
        return max(Decimal("0.1"), multiplier)

    def _get_incoming_damage_multiplier(self, combatant):
        multiplier = Decimal("1")
        for effect in self._get_active_effects(combatant, "damage_taken_mult"):
            value = self._to_decimal(effect.get("value", 1), Decimal("1"))
            if value <= 0:
                continue
            multiplier *= value
        return max(Decimal("0.1"), multiplier)

    def _get_shield_reduction_ratio(self, combatant):
        reduction = Decimal("0")
        for effect in self._get_active_effects(combatant, "shield_reduction"):
            reduction += self._to_decimal(effect.get("value", 0), Decimal("0"))
        return max(Decimal("0"), min(Decimal("0.95"), reduction))

    def _get_extra_miss_chance(self, combatant):
        miss_chance = Decimal("0")
        for effect in self._get_active_effects(combatant, "miss_chance"):
            miss_chance += self._to_decimal(effect.get("value", 0), Decimal("0"))
        for effect in self._get_active_effects(combatant, "accuracy_reduction"):
            stacks = max(1, int(effect.get("stacks", 1)))
            miss_chance += self._to_decimal(effect.get("value", 0), Decimal("0")) * Decimal(stacks)
        return max(Decimal("0"), min(Decimal("0.95"), miss_chance))

    def _process_special_turn_start_effects(self, combatant):
        state = {"messages": [], "stunned": False, "force_trip": False}
        if not self.spring_specials_enabled:
            return state

        bucket = self.special_effects.get(combatant.name)
        if not bucket:
            return state

        expired = []
        for effect in list(bucket):
            duration = int(effect.get("duration", 0))
            if duration == 0:
                expired.append(effect)
                continue

            effect_type = str(effect.get("type", ""))
            value = self._to_decimal(effect.get("value", 0), Decimal("0"))
            stacks = max(1, int(effect.get("stacks", 1)))

            if effect_type == "stun":
                state["stunned"] = True
            elif effect_type == "permanent_trip":
                state["force_trip"] = True
            elif effect_type in {"dot_current_hp", "dot_starting_hp", "burn_stack"}:
                base_hp = combatant.hp if effect_type == "dot_current_hp" else combatant.max_hp
                damage = max(Decimal("1"), base_hp * value * Decimal(stacks))
                damage = self._apply_defender_damage_modifiers(combatant, damage)
                combatant.take_damage(damage)
                state["messages"].append(
                    f"{combatant.name} suffers **{self.format_number(damage)} HP** from {effect.get('name', effect_type)}."
                )

            if duration > 0:
                effect["duration"] = duration - 1
                if effect["duration"] <= 0:
                    expired.append(effect)

        for effect in expired:
            if effect in bucket:
                bucket.remove(effect)
            effect_name = str(effect.get("name", effect.get("type", "Effect"))).strip()
            if effect_name:
                state["messages"].append(f"{effect_name} on {combatant.name} has worn off.")

        if not bucket:
            self.special_effects.pop(combatant.name, None)

        return state

    def _apply_defender_damage_modifiers(self, defender, damage):
        adjusted = self._to_decimal(damage, Decimal("0"))
        adjusted *= self._get_incoming_damage_multiplier(defender)
        reduction = self._get_shield_reduction_ratio(defender)
        if reduction > 0:
            adjusted *= (Decimal("1") - reduction)
        return max(Decimal("1"), adjusted)

    def _calculate_regular_damage(
        self,
        attacker,
        defender,
        *,
        variance_max=None,
        damage_multiplier=Decimal("1"),
        ignore_armor=False,
        flat_damage=None,
        min_damage=Decimal("10"),
        apply_element=True,
    ):
        if flat_damage is None:
            variance_cap = variance_max
            if variance_cap is None:
                variance_cap = 50 if getattr(attacker, "is_pet", False) else 100
            raw_damage = self._to_decimal(attacker.damage, Decimal("0")) + Decimal(
                random.randint(0, int(max(0, variance_cap)))
            )
            if apply_element and self.config.get("element_effects", True):
                battles_cog = self.ctx.bot.get_cog("Battles")
                if battles_cog and hasattr(battles_cog, "element_ext"):
                    try:
                        element_mod = battles_cog.element_ext.calculate_damage_modifier(
                            self.ctx,
                            attacker.element,
                            defender.element,
                        )
                        if element_mod:
                            raw_damage *= (Decimal("1") + Decimal(str(element_mod)))
                    except Exception:
                        pass
        else:
            raw_damage = self._to_decimal(flat_damage, Decimal("0"))

        raw_damage *= self._get_outgoing_damage_multiplier(attacker)
        raw_damage *= self._to_decimal(damage_multiplier, Decimal("1"))

        blocked_damage = Decimal("0")
        if ignore_armor:
            damage = raw_damage
        else:
            defender_armor = self._to_decimal(defender.armor, Decimal("0"))
            blocked_damage = min(raw_damage, defender_armor)
            damage = raw_damage - defender_armor

        damage = max(self._to_decimal(min_damage, Decimal("1")), damage)
        damage = self._apply_defender_damage_modifiers(defender, damage)
        return damage, blocked_damage

    def _resolve_player_attack_redirect(self, attacker, defender, damage):
        if not self.spring_specials_enabled:
            return damage, ""
        if attacker not in self.player_team.combatants:
            return damage, ""
        if defender not in self.monster_team.combatants:
            return damage, ""
        if self._to_decimal(damage, Decimal("0")) <= 0:
            return damage, ""

        if self._consume_special_effect(defender, "negate_next_player_damage_or_heal"):
            return Decimal("0"), f"{defender.name} negates the next incoming strike."

        if self._consume_special_effect(defender, "reverse_next_player_damage"):
            attacker.take_damage(damage)
            return (
                Decimal("0"),
                f"{defender.name} reverses the attack and {attacker.name} takes **{self.format_number(damage)} HP**.",
            )

        return damage, ""

    def _choose_monster_ability(self):
        if not self.spring_specials_enabled:
            return None
        abilities = [a for a in self.monster_abilities if isinstance(a, dict)]
        if not abilities:
            return None
        weights = []
        for ability in abilities:
            try:
                weights.append(max(0.0, float(ability.get("chance_pct", 0) or 0)))
            except Exception:
                weights.append(0.0)
        if sum(weights) <= 0:
            return random.choice(abilities)
        return random.choices(abilities, weights=weights, k=1)[0]

    def _apply_configured_status(self, target, status_name, payload):
        status = str(status_name or "").strip().lower()
        duration = int(payload.get("duration_turns", 1) or 1)
        if status == "player_miss_chance":
            value = self._pct_to_ratio(payload.get("value_pct", payload.get("value", 0)))
            self._add_special_effect(
                target,
                "miss_chance",
                value=value,
                duration=duration,
                name="Impaired Accuracy",
            )
            return f"{target.name}'s accuracy drops."
        if status == "player_damage_taken_mult":
            value = self._to_decimal(payload.get("value", 1), Decimal("1"))
            self._add_special_effect(
                target,
                "damage_taken_mult",
                value=value,
                duration=duration,
                name="Vulnerability",
            )
            return f"{target.name} now takes amplified damage."
        if status == "player_damage_mult":
            value = self._to_decimal(payload.get("value", 1), Decimal("1"))
            self._add_special_effect(
                target,
                "damage_mult",
                value=value,
                duration=duration,
                name="Damage Shift",
            )
            return f"{target.name}'s damage output shifts."
        if status == "permanent_trip":
            self._add_special_effect(
                target,
                "permanent_trip",
                value=1,
                duration=-1,
                name="Endless Fall",
            )
            return f"{target.name} is afflicted with permanent instability."
        return None

    def _execute_spring_monster_ability(self, attacker, defender):
        total_damage = Decimal("0")
        total_blocked = Decimal("0")
        details = []

        def record_damage(damage, blocked=Decimal("0")):
            nonlocal total_damage, total_blocked
            damage = self._to_decimal(damage, Decimal("0"))
            blocked = self._to_decimal(blocked, Decimal("0"))
            if damage > 0:
                defender.take_damage(damage)
                total_damage += damage
            if blocked > 0:
                total_blocked += blocked
            return damage

        queued_multi = self._get_active_effects(attacker, "next_turn_multiattack")
        if queued_multi:
            hits = max(1, int(self._to_decimal(queued_multi[0].get("value", 2), Decimal("2"))))
            for _ in range(hits):
                if not defender.is_alive():
                    break
                damage, blocked = self._calculate_regular_damage(attacker, defender)
                record_damage(damage, blocked)
            message = (
                f"{attacker.name}'s lingering assault strikes {hits} time(s)! "
                f"{defender.name} takes **{self.format_number(total_damage)} HP**."
            )
            return {
                "message": message,
                "damage": total_damage,
                "blocked_damage": total_blocked,
            }

        ability = self._choose_monster_ability()
        if not ability:
            damage, blocked = self._calculate_regular_damage(attacker, defender)
            record_damage(damage, blocked)
            return {
                "message": (
                    f"{attacker.name} attacks! {defender.name} takes "
                    f"**{self.format_number(damage)} HP** damage."
                ),
                "damage": total_damage,
                "blocked_damage": total_blocked,
            }

        ability_name = str(ability.get("name", "Special Attack"))
        effect = ability.get("effect", {})
        effect_type = str(effect.get("type", "basic_attack")).strip().lower()

        if effect_type == "basic_attack":
            damage, blocked = self._calculate_regular_damage(attacker, defender)
            record_damage(damage, blocked)
            details.append(f"{defender.name} takes **{self.format_number(damage)} HP** damage.")

        elif effect_type == "damage_and_debuff":
            scale = self._to_decimal(effect.get("damage_scale", 1), Decimal("1"))
            damage, blocked = self._calculate_regular_damage(
                attacker,
                defender,
                damage_multiplier=scale,
            )
            record_damage(damage, blocked)
            details.append(f"{defender.name} takes **{self.format_number(damage)} HP** damage.")
            debuff = effect.get("debuff", {})
            if isinstance(debuff, dict):
                debuff_note = self._apply_configured_status(defender, debuff.get("type"), debuff)
                if debuff_note:
                    details.append(debuff_note)

        elif effect_type == "dot_percent_current_hp":
            pct = self._pct_to_ratio(effect.get("pct", 0))
            duration = int(effect.get("duration_turns", 1) or 1)
            self._add_special_effect(
                defender,
                "dot_current_hp",
                value=pct,
                duration=duration,
                name=ability_name,
            )
            details.append(f"{defender.name} is afflicted for {duration} turn(s).")

        elif effect_type == "damage_and_stun":
            damage_ratio = self._pct_to_ratio(effect.get("damage_pct_of_attack", 10))
            base_damage = self._to_decimal(attacker.damage, Decimal("0")) * damage_ratio
            damage, blocked = self._calculate_regular_damage(
                attacker,
                defender,
                flat_damage=base_damage,
                variance_max=0,
            )
            record_damage(damage, blocked)
            stun_turns = int(effect.get("stun_turns", 1) or 1)
            self._add_special_effect(defender, "stun", duration=stun_turns, name="Stun")
            details.append(
                f"{defender.name} takes **{self.format_number(damage)} HP** damage and is stunned for {stun_turns} turn(s)."
            )

        elif effect_type == "apply_status":
            status_note = self._apply_configured_status(defender, effect.get("status"), effect)
            details.append(status_note or "The effect fizzles out.")

        elif effect_type == "self_buff":
            buff_type = str(effect.get("buff", "")).strip().lower()
            if buff_type == "attack_damage_mult":
                value = self._to_decimal(effect.get("value", 1), Decimal("1"))
                duration = int(effect.get("duration_turns", 1) or 1)
                self._add_special_effect(
                    attacker,
                    "damage_mult",
                    value=value,
                    duration=duration,
                    name=ability_name,
                )
                details.append(f"{attacker.name}'s power is amplified for {duration} turn(s).")
            else:
                details.append("Power gathers, but nothing changes.")

        elif effect_type == "stun_player":
            duration = int(effect.get("duration_turns", 1) or 1)
            self._add_special_effect(defender, "stun", duration=duration, name="Stun")
            details.append(f"{defender.name} is stunned for {duration} turn(s).")

        elif effect_type == "heal_player_percent_current_hp":
            pct = self._pct_to_ratio(effect.get("pct", 0))
            heal_amount = max(Decimal("1"), defender.hp * pct)
            defender.heal(heal_amount)
            details.append(f"{defender.name} recovers **{self.format_number(heal_amount)} HP**.")

        elif effect_type == "reverse_next_player_damage":
            duration = int(effect.get("duration_turns", 1) or 1)
            self._add_special_effect(
                attacker,
                "reverse_next_player_damage",
                value=1,
                duration=duration,
                name=ability_name,
            )
            details.append("The next incoming player strike will be reversed.")

        elif effect_type == "player_loses_percent_max_hp":
            pct = self._pct_to_ratio(effect.get("pct", 0))
            damage = max(Decimal("1"), defender.max_hp * pct)
            damage = self._apply_defender_damage_modifiers(defender, damage)
            record_damage(damage)
            details.append(f"{defender.name} loses **{self.format_number(damage)} HP**.")

        elif effect_type == "lifesteal_percent_max_hp":
            pct = self._pct_to_ratio(effect.get("pct", 0))
            damage = max(Decimal("1"), defender.max_hp * pct)
            damage = self._apply_defender_damage_modifiers(defender, damage)
            dealt = record_damage(damage)
            attacker.heal(dealt)
            details.append(
                f"{defender.name} loses **{self.format_number(dealt)} HP** and {attacker.name} heals the same amount."
            )

        elif effect_type == "damage_percent_max_hp_and_stun":
            pct = self._pct_to_ratio(effect.get("pct", 0))
            damage = max(Decimal("1"), defender.max_hp * pct)
            damage = self._apply_defender_damage_modifiers(defender, damage)
            record_damage(damage)
            stun_turns = int(effect.get("stun_turns", 1) or 1)
            self._add_special_effect(defender, "stun", duration=stun_turns, name="Stun")
            details.append(
                f"{defender.name} takes **{self.format_number(damage)} HP** and is stunned for {stun_turns} turn(s)."
            )

        elif effect_type == "true_damage_flat":
            amount = self._to_decimal(effect.get("amount", 0), Decimal("0"))
            damage = self._apply_defender_damage_modifiers(defender, max(Decimal("1"), amount))
            record_damage(damage)
            details.append(f"{defender.name} takes **{self.format_number(damage)} HP** true damage.")

        elif effect_type == "multiattack_next_turns":
            hits_per_turn = max(1, int(effect.get("hits_per_turn", 2) or 2))
            duration = int(effect.get("duration_turns", 2) or 2)
            self._add_special_effect(
                attacker,
                "next_turn_multiattack",
                value=hits_per_turn,
                duration=duration,
                name=ability_name,
            )
            details.append(f"{attacker.name} prepares {hits_per_turn} hits per turn for {duration} turn(s).")

        elif effect_type == "damage_percent_max_hp":
            pct = self._pct_to_ratio(effect.get("pct", 0))
            damage = max(Decimal("1"), defender.max_hp * pct)
            damage = self._apply_defender_damage_modifiers(defender, damage)
            record_damage(damage)
            details.append(f"{defender.name} takes **{self.format_number(damage)} HP**.")

        elif effect_type == "negate_last_player_damage_or_heal":
            self._add_special_effect(
                attacker,
                "negate_next_player_damage_or_heal",
                value=1,
                duration=1,
                name=ability_name,
            )
            details.append("The next player damage or heal is negated.")

        elif effect_type == "self_shield":
            reduction = self._pct_to_ratio(effect.get("damage_reduction_pct", 0))
            duration = int(effect.get("duration_turns", 1) or 1)
            self._add_special_effect(
                attacker,
                "shield_reduction",
                value=reduction,
                duration=duration,
                name=ability_name,
            )
            details.append(
                f"{attacker.name} gains {int(reduction * 100)}% damage reduction for {duration} turn(s)."
            )

        elif effect_type == "self_heal_percent_current_hp":
            pct = self._pct_to_ratio(effect.get("pct", 0))
            heal_amount = max(Decimal("1"), attacker.hp * pct)
            attacker.heal(heal_amount)
            details.append(f"{attacker.name} heals **{self.format_number(heal_amount)} HP**.")

        elif effect_type == "permanent_burn_stack_percent_max_hp":
            burn_pct = self._pct_to_ratio(effect.get("pct", 0))
            max_stacks = max(1, int(effect.get("max_stacks", 1) or 1))
            burn = self._add_special_effect(
                defender,
                "burn_stack",
                value=burn_pct,
                duration=-1,
                name=ability_name,
                max_stacks=max_stacks,
                stack=True,
            )
            current_stacks = int((burn or {}).get("stacks", 1))
            details.append(f"{defender.name} gains a burn stack ({current_stacks}/{max_stacks}).")

        elif effect_type == "stacking_player_accuracy_reduction":
            reduction = self._pct_to_ratio(effect.get("reduction_pct", 0))
            max_stacks = max(1, int(effect.get("max_stacks", 1) or 1))
            accuracy = self._add_special_effect(
                defender,
                "accuracy_reduction",
                value=reduction,
                duration=-1,
                name=ability_name,
                max_stacks=max_stacks,
                stack=True,
            )
            current_stacks = int((accuracy or {}).get("stacks", 1))
            details.append(
                f"{defender.name}'s accuracy drops further ({current_stacks}/{max_stacks} stack(s))."
            )

        elif effect_type == "execute_percent_starting_hp":
            pct = self._pct_to_ratio(effect.get("pct", 0))
            damage = max(Decimal("1"), defender.max_hp * pct)
            damage = self._apply_defender_damage_modifiers(defender, damage)
            record_damage(damage)
            details.append(f"{defender.name} takes **{self.format_number(damage)} HP** execute damage.")

        elif effect_type == "dot_percent_starting_hp":
            pct = self._pct_to_ratio(effect.get("pct", 0))
            duration = int(effect.get("duration_turns", 1) or 1)
            self._add_special_effect(
                defender,
                "dot_starting_hp",
                value=pct,
                duration=duration,
                name=ability_name,
            )
            details.append(f"{defender.name} is afflicted for {duration} turn(s).")

        elif effect_type == "multiattack":
            hits = max(1, int(effect.get("hits", 2) or 2))
            for _ in range(hits):
                if not defender.is_alive():
                    break
                damage, blocked = self._calculate_regular_damage(attacker, defender)
                record_damage(damage, blocked)
            details.append(
                f"{defender.name} is struck {hits} time(s) for **{self.format_number(total_damage)} HP** total."
            )

        else:
            damage, blocked = self._calculate_regular_damage(attacker, defender)
            record_damage(damage, blocked)
            details.append(f"{defender.name} takes **{self.format_number(damage)} HP** damage.")

        if not details:
            details.append("Nothing happens.")

        return {
            "message": f"{attacker.name} uses **{ability_name}**! {' '.join(details)}",
            "damage": total_damage,
            "blocked_damage": total_blocked,
        }

    async def start_battle(self):
        """Initialize and start the battle"""
        self.started = True
        # >>> timezone-aware timestamp
        self.start_time = datetime.now(timezone.utc)

        # Determine turn order (randomized)
        self.turn_order = []
        for team in self.teams:
            for combatant in team.combatants:
                self.turn_order.append(combatant)

        random.shuffle(self.turn_order)

        if self.simulation_mode:
            return True

        # Save initial battle data to database for replay
        await self.save_battle_to_database()

        monster_name = self.monster_team.combatants[0].name
        await self.add_to_log(f"Battle against {monster_name} started!")

        # Create and send initial battle embed
        embed = await self.create_battle_embed()
        self.battle_message = await self.ctx.send(embed=embed)
        await asyncio.sleep(2)

        return True

    async def process_turn(self):
        """Process a single turn of the battle"""
        if await self.is_battle_over():
            return False

        # Get attacker for this turn
        self.attacker = self.turn_order[self.current_turn % len(self.turn_order)]

        # Skip if attacker is dead
        if not self.attacker.is_alive():
            self.current_turn += 1
            return True

        # Determine which team the attacker is on
        attacker_team = None
        for team in self.teams:
            if self.attacker in team.combatants:
                attacker_team = team
                break

        # Get the opposing team
        defending_team = self.monster_team if attacker_team == self.player_team else self.player_team

        # Get an alive defender from the defending team
        alive_defenders = [c for c in defending_team.combatants if c.is_alive()]
        if not alive_defenders:
            return False

        self.defender = random.choice(alive_defenders)

        # Apply Dreambound/Special status effects at the start of the combatant turn.
        turn_state = self._process_special_turn_start_effects(self.attacker)
        for turn_msg in turn_state["messages"]:
            await self.add_to_log(turn_msg)

        if not self.attacker.is_alive():
            await self.add_to_log(f"{self.attacker.name} is defeated before acting!")
            if not self.simulation_mode:
                await self.update_display()
                await asyncio.sleep(1)
            self.current_turn += 1
            return True

        if turn_state["stunned"]:
            await self.add_to_log(f"{self.attacker.name} is stunned and cannot act!")
            if not self.simulation_mode:
                await self.update_display()
                await asyncio.sleep(1)
            self.current_turn += 1
            return True

        forced_trip = bool(turn_state["force_trip"])

        # Different hit/miss logic for monsters and players
        hits = True
        extra_miss = self._get_extra_miss_chance(self.attacker)

        if forced_trip:
            hits = False
        elif self.attacker in self.monster_team.combatants:
            # Monsters: 10% chance to miss
            monster_miss = Decimal("0.10") + extra_miss
            monster_miss = max(Decimal("0"), min(Decimal("0.95"), monster_miss))
            if random.random() < float(monster_miss):
                hits = False
        else:
            # Players: Use luck-based system
            # Check for perfect accuracy from Night Vision skill
            has_perfect_accuracy = getattr(self.attacker, "perfect_accuracy", False)
            if not has_perfect_accuracy and random.random() < float(extra_miss):
                hits = False
            else:
                luck_roll = random.randint(1, 100)
                if not has_perfect_accuracy and luck_roll > self.attacker.luck:
                    hits = False

        damage = Decimal("0")
        blocked_damage = Decimal("0")
        message = ""

        if hits:
            # Attack hits

            # Special case for mage fireball
            used_fireball = False
            if self._is_monster(self.attacker) and self.spring_specials_enabled and self.monster_abilities:
                ability_result = self._execute_spring_monster_ability(self.attacker, self.defender)
                damage = self._to_decimal(ability_result.get("damage", 0), Decimal("0"))
                blocked_damage = self._to_decimal(
                    ability_result.get("blocked_damage", 0),
                    Decimal("0"),
                )
                message = str(ability_result.get("message", "")).strip() or (
                    f"{self.attacker.name} attacks! {self.defender.name} takes no damage."
                )
            elif (
                self.attacker.mage_evolution
                and not self.attacker.is_pet
                and self.config["class_buffs"]
                and random.random() < self.config["fireball_chance"]
            ):

                # Calculate fireball damage
                evolution_level = self.attacker.mage_evolution
                damage_multiplier = {
                    1: 1.10,  # 110%
                    2: 1.20,  # 120%
                    3: 1.30,  # 130%
                    4: 1.50,  # 150%
                    5: 1.75,  # 175%
                    6: 2.00,  # 200%
                }.get(evolution_level, 1.0)

                damage = (self.attacker.damage + Decimal(random.randint(0, 100)) - self.defender.armor) * Decimal(
                    str(damage_multiplier)
                )
                damage = max(damage, Decimal("10"))
                damage = self._apply_defender_damage_modifiers(self.defender, damage)
                damage, redirect_msg = self._resolve_player_attack_redirect(
                    self.attacker,
                    self.defender,
                    damage,
                )

                if damage > 0:
                    self.defender.take_damage(damage)

                message = f"{self.attacker.name} casts Fireball! {self.defender.name} takes **{self.format_number(damage)} HP** damage."
                if redirect_msg:
                    message += f" {redirect_msg}"
                used_fireball = True
            else:
                # Regular attack
                damage_variance = random.randint(0, 50) if self.attacker.is_pet else random.randint(0, 100)

                # Start with base damage
                raw_damage = self.attacker.damage

                # PROCESS PET SKILL EFFECTS ON ATTACK
                skill_messages = []
                redirect_msg = ""
                if self.attacker.is_pet and hasattr(self.ctx.bot.cogs["Battles"], "battle_factory"):
                    pet_ext = self.ctx.bot.cogs["Battles"].battle_factory.pet_ext
                    raw_damage, skill_messages = pet_ext.process_skill_effects_on_attack(self.attacker, self.defender, raw_damage)
                    # Set flag for turn processing (damage will be set after final calculation)
                    setattr(self.attacker, "attacked_this_turn", True)

                    # Apply element effects to base damage if enabled
                    if self.config["element_effects"] and hasattr(self.ctx.bot.cogs["Battles"], "element_ext"):
                        element_mod = self.ctx.bot.cogs["Battles"].element_ext.calculate_damage_modifier(
                            self.ctx, self.attacker.element, self.defender.element
                        )

                        # Apply void affinity protection to defender
                        if hasattr(self.ctx.bot.cogs["Battles"], "battle_factory"):
                            pet_ext = self.ctx.bot.cogs["Battles"].battle_factory.pet_ext
                            element_mod = pet_ext.apply_void_affinity_protection(self.defender, element_mod)

                        if element_mod != 0:
                            raw_damage = raw_damage * (1 + Decimal(str(element_mod)))

                    # Add variance
                    raw_damage += Decimal(damage_variance)

                    # Check for special damage types
                    ignore_armor = getattr(self.defender, "ignore_armor_this_hit", False)
                    true_damage = getattr(self.defender, "true_damage", False)
                    bypass_defenses = getattr(self.defender, "bypass_defenses", False)
                    ignore_all = getattr(self.defender, "ignore_all_defenses", False)

                    if ignore_all or true_damage or ignore_armor or bypass_defenses:
                        damage = raw_damage  # No armor reduction
                        blocked_damage = Decimal("0")
                    else:
                        blocked_damage = min(raw_damage, self.defender.armor)
                        damage = max(raw_damage - self.defender.armor, Decimal("10"))

                    # Clear special damage flags
                    for flag in ["ignore_armor_this_hit", "true_damage", "bypass_defenses", "ignore_all_defenses"]:
                        if hasattr(self.defender, flag):
                            delattr(self.defender, flag)

                    # PROCESS PET SKILL EFFECTS ON DAMAGE TAKEN
                    defender_messages = []
                    if self.defender.is_pet and hasattr(self.ctx.bot.cogs["Battles"], "battle_factory"):
                        pet_ext = self.ctx.bot.cogs["Battles"].battle_factory.pet_ext
                        damage, defender_messages = pet_ext.process_skill_effects_on_damage_taken(
                            self.defender, self.attacker, damage
                        )

                    damage = self._apply_defender_damage_modifiers(self.defender, damage)
                    damage, redirect_msg = self._resolve_player_attack_redirect(
                        self.attacker,
                        self.defender,
                        damage,
                    )

                    # Store the actual final damage dealt (for skills like Soul Drain)
                    if self.attacker.is_pet:
                        setattr(self.attacker, "last_damage_dealt", damage)

                    if damage > 0:
                        self.defender.take_damage(damage)

                    message = (
                        f"{self.attacker.name} attacks! {self.defender.name} takes **{self.format_number(damage)} HP** damage."
                    )
                    if redirect_msg:
                        message += f" {redirect_msg}"

                    # Add skill effect messages
                    if skill_messages:
                        message += "\n" + "\n".join(skill_messages)
                    if defender_messages:
                        message += "\n" + "\n".join(defender_messages)

                    # Check for skeleton summoning after skill processing
                    if hasattr(self.attacker, "summon_skeleton"):
                        skeleton_data = self.attacker.summon_skeleton

                        # Create skeleton combatant
                        from cogs.battles.core.combatant import Combatant

                        skeleton = Combatant(
                            user=f"Skeleton Warrior #{self.attacker.skeleton_count}",  # User/name
                            hp=skeleton_data["hp"],
                            max_hp=skeleton_data["hp"],  # Same as current HP
                            damage=skeleton_data["damage"],
                            armor=skeleton_data["armor"],
                            element=skeleton_data["element"],
                            luck=50,  # Base luck
                            is_pet=True,
                            name=f"Skeleton Warrior #{self.attacker.skeleton_count}",
                        )
                        skeleton.is_summoned = True
                        skeleton.summoner = self.attacker

                        # Add skeleton to player team
                        self.player_team.combatants.append(skeleton)
                        # Also add to turn order
                        self.turn_order.append(skeleton)
                        message += f"\n💀 A skeleton warrior joins your side!"

                        # Clear the summon flag
                        delattr(self.attacker, "summon_skeleton")
                else:
                    # Non-pet regular attack - apply element effects to base damage if enabled
                    if self.config["element_effects"] and hasattr(self.ctx.bot.cogs["Battles"], "element_ext"):
                        element_mod = self.ctx.bot.cogs["Battles"].element_ext.calculate_damage_modifier(
                            self.ctx, self.attacker.element, self.defender.element
                        )

                        if element_mod != 0:
                            raw_damage = raw_damage * (1 + Decimal(str(element_mod)))

                    # Add variance
                    raw_damage += Decimal(damage_variance)

                    # Calculate damage with armor
                    blocked_damage = min(raw_damage, self.defender.armor)
                    damage = max(raw_damage - self.defender.armor, Decimal("10"))
                    damage = self._apply_defender_damage_modifiers(self.defender, damage)
                    damage, redirect_msg = self._resolve_player_attack_redirect(
                        self.attacker,
                        self.defender,
                        damage,
                    )

                    if damage > 0:
                        self.defender.take_damage(damage)

                    message = (
                        f"{self.attacker.name} attacks! {self.defender.name} takes **{self.format_number(damage)} HP** damage."
                    )
                    if redirect_msg:
                        message += f" {redirect_msg}"

            # Handle lifesteal if applicable
            if (
                self.config["class_buffs"]
                and not self.attacker.is_pet
                and self.attacker.lifesteal_percent > 0
                and damage > 0
            ):
                lifesteal_amount = float(damage) * float(self.attacker.lifesteal_percent) / 100.0
                self.attacker.heal(lifesteal_amount)
                message += f" Lifesteals: **{self.format_number(lifesteal_amount)} HP**"

            # Handle damage reflection if applicable
            # Apply tank evolution reflection multiplier if applicable
            reflection_value = self.defender.damage_reflection

            # Apply tank evolution-based reflection if defender has tank evolution
            if self.config["class_buffs"] and self.defender.tank_evolution and not self.defender.is_pet:
                # Use the standard tank evolution reflection multiplier from classes.py
                tank_reflection = 0.03 * self.defender.tank_evolution  # 3% per level, so 21% at level 7
                reflection_value = max(reflection_value, tank_reflection)  # Use higher of item reflection or tank reflection

            if self.config["reflection_damage"] and reflection_value > 0 and blocked_damage > 0:
                reflected = blocked_damage * Decimal(str(reflection_value))
                self.attacker.take_damage(reflected)
                message += f"\n{self.defender.name}'s armor reflects **{self.format_number(reflected)} HP** damage back!"

                if not self.attacker.is_alive():
                    message += f" {self.attacker.name} has been defeated by reflected damage!"

            # Check if defender is defeated
            if not self.defender.is_alive():
                # Check for cheat death ability
                if (
                    self.config["class_buffs"]
                    and self.config["cheat_death"]
                    and not self.defender.is_pet
                    and self.defender.death_cheat_chance > 0
                    and not self.defender.has_cheated_death
                ):
                    cheat_roll = random.randint(1, 100)
                    if cheat_roll <= self.defender.death_cheat_chance:
                        self.defender.hp = Decimal("75")
                        self.defender.has_cheated_death = True
                        message += f"\n{self.defender.name} cheats death and survives with **75 HP**!"
                    else:
                        message += f" {self.defender.name} has been defeated!"
                else:
                    message += f" {self.defender.name} has been defeated!"
        else:
            # Attack misses
            if self.config.get("tripping", False) or forced_trip:
                if self.attacker in self.player_team.combatants:
                    # Players: Always trip on miss
                    damage = Decimal("10")
                    self.attacker.take_damage(damage)
                    message = f"{self.attacker.name} tripped and took **{self.format_number(damage)} HP** damage. Bad luck!"
                else:
                    # Monsters have already had their 10% miss chance applied earlier
                    # When they miss, they always trip (since total miss+trip is 10%)
                    damage = Decimal("10")
                    self.attacker.take_damage(damage)
                    message = f"{self.attacker.name} tripped and took **{self.format_number(damage)} HP** damage."
            else:
                message = f"{self.attacker.name}'s attack missed!"

        # Add message to battle log
        await self.add_to_log(message)

        # PROCESS PET SKILL EFFECTS PER TURN
        if hasattr(self.ctx.bot.cogs["Battles"], "battle_factory"):
            pet_ext = self.ctx.bot.cogs["Battles"].battle_factory.pet_ext

            # Process player team combatants
            for combatant in self.player_team.combatants:
                if combatant.is_pet and combatant.is_alive():
                    # Set team references for skills that need them
                    setattr(combatant, "team", self.player_team)
                    setattr(combatant, "enemy_team", self.monster_team)

                    # Process per-turn effects
                    turn_messages = pet_ext.process_skill_effects_per_turn(combatant)
                    if turn_messages:
                        for turn_msg in turn_messages:
                            await self.add_to_log(turn_msg)

            # Process monster team combatants (if any pets)
            for combatant in self.monster_team.combatants:
                if combatant.is_pet and combatant.is_alive():
                    # Set team references for skills that need them
                    setattr(combatant, "team", self.monster_team)
                    setattr(combatant, "enemy_team", self.player_team)

                    # Process per-turn effects
                    turn_messages = pet_ext.process_skill_effects_per_turn(combatant)
                    if turn_messages:
                        for turn_msg in turn_messages:
                            await self.add_to_log(turn_msg)

        # Check for death from turn effects
        if not self.defender.is_alive():
            # Mark if pet killed an enemy for Soul Harvest
            if self.attacker.is_pet:
                setattr(self.attacker, "killed_enemy_this_turn", True)

        # Update the battle display
        if not self.simulation_mode:
            await self.update_display()
            await asyncio.sleep(1)

        # Move to next turn
        self.current_turn += 1

        return True

    async def create_battle_embed(self):
        """Create the battle status embed"""
        monster_name = self.monster_team.combatants[0].name
        embed = discord.Embed(
            title=f"PvE Battle: {self.ctx.author.display_name} vs {monster_name}",
            color=self.ctx.bot.config.game.primary_colour,
        )

        # Get element emoji mapping
        element_emoji_map = {}
        if hasattr(self.ctx.bot.cogs["Battles"], "emoji_to_element"):
            element_emoji_map = self.ctx.bot.cogs["Battles"].emoji_to_element

        # Add player team info
        for combatant in self.player_team.combatants:
            current_hp = max(0, float(combatant.hp))
            max_hp = float(combatant.max_hp)
            hp_bar = self.create_hp_bar(current_hp, max_hp)

            # Get element emoji
            element_emoji = "❌"
            for emoji, element in element_emoji_map.items():
                if element == combatant.element:
                    element_emoji = emoji
                    break

            field_name = f"**[TEAM A]** \n{combatant.name} {element_emoji}"
            field_value = f"HP: {current_hp:.1f}/{max_hp:.1f}\n{hp_bar}"

            # Add reflection info if applicable
            if combatant.damage_reflection > 0:
                reflection_percent = float(combatant.damage_reflection) * 100
                field_value += f"\nDamage Reflection: {reflection_percent:.1f}%"

            embed.add_field(name=field_name, value=field_value, inline=False)

        # Add monster team info
        for combatant in self.monster_team.combatants:
            current_hp = max(0, float(combatant.hp))
            max_hp = float(combatant.max_hp)
            hp_bar = self.create_hp_bar(current_hp, max_hp)

            # Get element emoji
            element_emoji = "❌"
            for emoji, element in element_emoji_map.items():
                if element == combatant.element:
                    element_emoji = emoji
                    break

            field_name = f"**[TEAM B]** \n{combatant.name} {element_emoji}"
            field_value = f"HP: {current_hp:.1f}/{max_hp:.1f}\n{hp_bar}"
            embed.add_field(name=field_name, value=field_value, inline=False)

        # Add battle log
        log_text = "\n\n".join([f"**Action #{i}**\n{msg}" for i, msg in self.log])
        embed.add_field(name="Battle Log", value=log_text or "Battle starting...", inline=False)

        # Add battle ID to footer for GM replay functionality
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
        """End the battle, handle rewards, and persist final state."""
        self.finished = True

        # Timeout / draw
        if await self.is_timed_out():
            await self.ctx.send("The battle ended in a draw due to timeout.")
            # Persist final state for replay/debug
            with suppress(Exception):
                await self.save_battle_to_database()
            self.winner = None
            return None

        # Work out winner/loser
        player_defeated = all(not c.is_alive() for c in self.player_team.combatants)
        monster_defeated = all(not c.is_alive() for c in self.monster_team.combatants)

        # Double KO edge-case -> draw
        if player_defeated and monster_defeated:
            await self.ctx.send("Both sides fell at the same time. It's a draw!")
            with suppress(Exception):
                await self.save_battle_to_database()
            self.winner = None
            return None

        if player_defeated:
            # Loss path
            self.winner = self.monster_team
            await self.ctx.send(f"You were defeated by the **{self.monster_team.combatants[0].name}**. Better luck next time!")
            with suppress(Exception):
                await self.save_battle_to_database()
            # (Optionally dispatch a loss event if you want)
            return self.monster_team

        # Win path
        self.winner = self.player_team

        # ----- XP reward -----
        if self.monster_level == 11:  # Legendary
            xp_gain = random.randint(75000, 125000)
        else:
            xp_gain = random.randint(self.monster_level * 300, self.monster_level * 1000)

        # Macro penalty: divide XP by 10 when active
        if getattr(self, "macro_penalty_level", 0) >= 24:
            xp_gain = xp_gain // 10

        # ----- Award XP -----
        async with self.ctx.bot.pool.acquire() as conn:
            await conn.execute(
                'UPDATE profile SET "xp" = "xp" + $1 WHERE "user" = $2;',
                xp_gain,
                self.ctx.author.id,
            )

        # ----- Crafting resources (only if no macro penalty) -----
        crafting_resources_awarded = []
        if getattr(self, "macro_penalty_level", 0) == 0:
            amulet_cog = self.ctx.bot.get_cog("AmuletCrafting")
            if amulet_cog:
                # Reduced amounts by monster level, same tiers as before
                if self.monster_level == 11:
                    resource_count = random.randint(2, 3)
                    amount_range = (1, 2)
                elif self.monster_level >= 8:
                    resource_count = random.randint(1, 2)
                    amount_range = (1, 2)
                elif self.monster_level >= 5:
                    resource_count = 1
                    amount_range = (1, 2)
                else:
                    # 70% chance to get 1 resource, else none
                    if random.random() < 0.7:
                        resource_count = 1
                        amount_range = (1, 1)
                    else:
                        resource_count = 0
                        amount_range = (0, 0)

                for _ in range(resource_count):
                    resource_name, amount = await amulet_cog.give_random_resource(
                        self.ctx.author.id, amount_range=amount_range, category=None, respect_level=True
                    )
                    if resource_name:
                        display_name = resource_name.replace("_", " ").title()
                        crafting_resources_awarded.append(f"{amount}x {display_name}")

        # ----- Victory message (no more Nekyia bones) -----
        monster_name = self.monster_team.combatants[0].name
        victory_message = f"You defeated the **{monster_name}** and gained **{xp_gain} XP**!"
        if crafting_resources_awarded:
            victory_message += f"\n🔨 **Crafting Resources Found:** {', '.join(crafting_resources_awarded)}"

        await self.ctx.send(victory_message)

        # ----- Level-up check -----
        from utils import misc as rpgtools

        player_xp = self.ctx.character_data.get("xp", 0)
        player_level = rpgtools.xptolevel(player_xp)
        new_level = rpgtools.xptolevel(player_xp + xp_gain)
        if new_level > player_level:
            await self.ctx.bot.process_levelup(self.ctx, new_level, player_level)

        # Event hook
        self.ctx.bot.dispatch("PVE_completion", self.ctx, True)

        # Persist final battle state
        with suppress(Exception):
            await self.save_battle_to_database()

        return self.player_team

    async def is_battle_over(self):
        """Check if the battle is over"""
        # Battle is over if one team is completely defeated
        player_defeated = all(not c.is_alive() for c in self.player_team.combatants)
        monster_defeated = all(not c.is_alive() for c in self.monster_team.combatants)

        return player_defeated or monster_defeated or await self.is_timed_out() or self.finished
