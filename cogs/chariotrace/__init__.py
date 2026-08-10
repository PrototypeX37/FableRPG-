"""
Team Chariot Race mini-game.
"""

import asyncio
import copy
from dataclasses import dataclass

import discord
from discord.enums import ButtonStyle
from discord.ext import commands
from discord.ui.button import Button

from cogs.help import chunks
from utils import random
from utils.checks import user_is_gm
from utils.i18n import _, locale_doc
from utils.misc import nice_join


JOIN_TIMEOUT = 60 * 5
BETTING_TIMEOUT = 60
CHOICE_TIMEOUT = 60
MIN_PLAYERS = 2
LAP_DISTANCE = 8
RACE_TIMEOUT_SECONDS = 60 * 60


ACTION_LABELS = {
    "forward": "Forward",
    "defense": "Defense",
    "attack": "Attack",
    "rush": "Rush",
    "full_attack": "Full Attack",
}


PATRON_GODS = [
    "Zeus",
    "Hera",
    "Poseidon",
    "Demeter",
    "Athena",
    "Apollo",
    "Artemis",
    "Ares",
    "Aphrodite",
    "Hephaestus",
    "Hermes",
    "Dionysus",
]


CARD_POOL = [
    ("zeus_thunder", "Divine Intervention"),
    ("satyr_stumble", "Divine Intervention"),
    ("medusa_gaze", "Divine Intervention"),
    ("poseidon_wave", "Divine Intervention"),
    ("ares_riot", "Divine Intervention"),
    ("olympian_blessing", "Divine Blessing"),
    ("lady_luck", "Divine Blessing"),
    ("nemesis_backlash", "Divine Blessing"),
    ("apollo_divination", "Divine Blessing"),
    ("athena_strategy", "Divine Blessing"),
    ("hermes_boots", "Divine Object"),
    ("pandora_box", "Divine Object"),
    ("eros_arrow", "Divine Object"),
    ("icarus_wings", "Divine Object"),
    ("aegis_shield", "Divine Object"),
    ("trojan_horse", "Divine Object"),
    ("cerberus", "Divine Object"),
    ("ariadne_thread", "Divine Object"),
]


@dataclass
class ChariotBet:
    bettor: discord.Member
    amount: int
    team_index: int


class ChariotLobbyView(discord.ui.View):
    def __init__(self, cog: "ChariotRace", channel_id: int, host_id: int, timeout: int):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.channel_id = channel_id
        self.host_id = host_id

    @discord.ui.button(label="Join", style=discord.ButtonStyle.success)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        lobby = self.cog.lobbies.get(self.channel_id)
        if not lobby:
            await interaction.response.send_message("No lobby found.", ephemeral=True)
            return
        if interaction.user in lobby["players"]:
            await interaction.response.send_message("You are already in.", ephemeral=True)
            return

        entry_fee = int(lobby["entry_fee"])
        if entry_fee > 0:
            balance = await self.cog._balance(interaction.user.id)
            if balance < entry_fee:
                await interaction.response.send_message(
                    f"You need **${entry_fee}** to join this race.",
                    ephemeral=True,
                )
                return
            await self.cog._add_money(interaction.user.id, -entry_fee)
            lobby["paid"].add(interaction.user.id)
            lobby["entry_pot"] += entry_fee

        lobby["players"].append(interaction.user)
        await interaction.response.edit_message(embed=self.cog._lobby_embed(lobby), view=self)

    @discord.ui.button(label="Leave", style=discord.ButtonStyle.secondary)
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        lobby = self.cog.lobbies.get(self.channel_id)
        if not lobby:
            await interaction.response.send_message("No lobby found.", ephemeral=True)
            return
        if interaction.user not in lobby["players"]:
            await interaction.response.send_message("You are not in the lobby.", ephemeral=True)
            return

        lobby["players"].remove(interaction.user)
        if interaction.user.id in lobby["paid"]:
            entry_fee = int(lobby["entry_fee"])
            await self.cog._add_money(interaction.user.id, entry_fee)
            lobby["paid"].remove(interaction.user.id)
            lobby["entry_pot"] = max(0, int(lobby["entry_pot"]) - entry_fee)

        await interaction.response.edit_message(embed=self.cog._lobby_embed(lobby), view=self)

    @discord.ui.button(label="Begin Now", style=discord.ButtonStyle.primary)
    async def begin(self, interaction: discord.Interaction, button: discord.ui.Button):
        lobby = self.cog.lobbies.get(self.channel_id)
        if not lobby:
            await interaction.response.send_message("No lobby found.", ephemeral=True)
            return

        is_admin = interaction.user.guild_permissions.manage_guild or interaction.user.guild_permissions.administrator
        is_gm_user = await user_is_gm(self.cog.bot, interaction.user)
        if interaction.user.id != self.host_id and not is_admin and not is_gm_user:
            await interaction.response.send_message("Only the host/GM can begin now.", ephemeral=True)
            return

        lobby["force_begin"].set()
        await interaction.response.send_message("Beginning now...", ephemeral=True)


class ChariotChoiceButton(Button):
    def __init__(self, action: str):
        super().__init__(
            label=ACTION_LABELS[action],
            style={
                "forward": ButtonStyle.primary,
                "defense": ButtonStyle.success,
                "attack": ButtonStyle.danger,
                "rush": ButtonStyle.primary,
                "full_attack": ButtonStyle.danger,
            }[action],
        )
        self.action = action

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if not isinstance(view, ChariotChoiceView):
            return
        await view.choose_action(interaction, self.action)


class ChariotCardSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="Mystery divine card",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label="Use mystery card",
                    value="use",
                    description="Reveal it only if both teammates choose this.",
                ),
                discord.SelectOption(
                    label="Hold reins",
                    value="hold",
                    description="Do not use this turn's card.",
                ),
            ],
        )

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if not isinstance(view, ChariotChoiceView):
            return
        await view.choose_card(interaction, self.values[0] == "use")


class ChariotChoiceView(discord.ui.View):
    def __init__(self, game: "ChariotGame"):
        super().__init__(timeout=CHOICE_TIMEOUT)
        self.game = game
        self.done = asyncio.Event()
        for action in ACTION_LABELS:
            self.add_item(ChariotChoiceButton(action))
        self.add_item(ChariotCardSelect())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user not in self.game.players:
            await interaction.response.send_message(
                _("Only racers in this Chariot Race can choose an action."),
                ephemeral=True,
            )
            return False
        return True

    async def choose_action(self, interaction: discord.Interaction, action: str):
        self.game.choices[interaction.user.id] = action
        await interaction.response.send_message(
            _("You chose **{action}**. Now choose whether to use the mystery card.").format(
                action=ACTION_LABELS[action]
            ),
            ephemeral=True,
        )
        self._maybe_finish()

    async def choose_card(self, interaction: discord.Interaction, wants_card: bool):
        self.game.card_choices[interaction.user.id] = wants_card
        choice = _("use") if wants_card else _("hold")
        await interaction.response.send_message(
            _("Card choice locked: **{choice}**.").format(choice=choice),
            ephemeral=True,
        )
        self._maybe_finish()

    def _maybe_finish(self):
        if self.game.all_choices_locked():
            self.done.set()
            self.stop()

    async def on_timeout(self):
        self.done.set()


class ChariotGame:
    def __init__(
        self,
        bot,
        ctx,
        players: list[discord.Member],
        laps: int,
        entry_pot: int = 0,
        sponsor_reward: int = 0,
        entry_fee: int = 0,
        paid_user_ids: set[int] | None = None,
    ):
        self.bot = bot
        self.ctx = ctx
        self.players = players
        self.laps = laps
        self.finish_distance = max(1, laps) * LAP_DISTANCE
        self.entry_pot = max(0, int(entry_pot))
        self.sponsor_reward = max(0, int(sponsor_reward))
        self.entry_fee = max(0, int(entry_fee))
        self.paid_user_ids = set(paid_user_ids or set())
        self.round = 1
        self.choices: dict[int, str] = {}
        self.card_choices: dict[int, bool] = {}
        self.teams: list[dict] = []
        self.bets: list[ChariotBet] = []
        self.total_spectator_bets = 0
        self.betting_open = False
        self.global_skip_rounds = 0
        self.stopped = False
        self.stop_reason = ""
        self.stop_event = asyncio.Event()
        self.race_deadline = None

    def make_teams(self):
        racers = copy.copy(self.players)
        racers = random.shuffle(racers)
        gods = random.shuffle(PATRON_GODS)
        self.teams = []
        pairs = [[racer] for racer in racers] if len(racers) == 2 else chunks(racers, 2)
        for index, pair in enumerate(pairs, start=1):
            patron = gods[(index - 1) % len(gods)]
            self.teams.append(
                {
                    "index": index,
                    "name": _("{god}'s Chariot #{index}").format(god=patron, index=index),
                    "god": patron,
                    "players": list(pair),
                    "distance": 0,
                    "lap_floor": 0,
                    "round_defense": 0,
                    "round_attack": 0,
                    "card": None,
                    "last_attacker": None,
                    "last_attack_damage": 0,
                    "immobile_turns": 0,
                    "lady_luck_turns": 0,
                    "lady_luck_pending": 0,
                    "cerberus_turns": 0,
                    "cerberus_pending": 0,
                    "trojan_bonus_next": 0,
                    "trojan_waiting": False,
                    "icarus_next": False,
                    "icarus_pending": False,
                    "icarus_fragile": False,
                    "aegis_turns": 0,
                    "apollo_evade": False,
                    "apollo_pending": False,
                    "nemesis_ready": False,
                    "ariadne_thread": False,
                    "card_step_bonus": 0,
                    "card_defense_bonus": 0,
                    "card_attack_bonus": 0,
                    "skip_move_this_round": False,
                }
            )

    def team_label(self, team: dict) -> str:
        labels = [player.mention for player in team["players"]]
        if len(labels) == 1:
            labels.append(_("AI teammate"))
        return nice_join(labels)

    def team_by_index(self, team_index: int) -> dict | None:
        for team in self.teams:
            if int(team["index"]) == int(team_index):
                return team
        return None

    def all_choices_locked(self) -> bool:
        return all(
            player.id in self.choices and player.id in self.card_choices
            for player in self.players
        )

    def action_for(self, player: discord.Member) -> str:
        if player.id in self.choices:
            return self.choices[player.id]
        return "forward"

    def stop(self, reason: str = ""):
        self.stopped = True
        self.stop_reason = reason
        self.stop_event.set()

    def progress_bar(self, distance: int) -> str:
        width = 18
        distance = max(0, min(distance, self.finish_distance))
        filled = int((distance / self.finish_distance) * width)
        return "█" * filled + "░" * (width - filled)

    def race_time_left(self) -> float:
        if self.race_deadline is None:
            return float(RACE_TIMEOUT_SECONDS)
        return max(0.0, float(self.race_deadline) - asyncio.get_running_loop().time())

    def standings_embed(self, title: str, log: list[str] | None = None) -> discord.Embed:
        embed = discord.Embed(title=title, color=discord.Color.gold())
        ordered = sorted(self.teams, key=lambda team: team["distance"], reverse=True)
        for place, team in enumerate(ordered, start=1):
            completed_laps = min(self.laps, team["distance"] // LAP_DISTANCE)
            current_lap = min(self.laps, completed_laps + 1)
            step = team["distance"] % LAP_DISTANCE
            if completed_laps >= self.laps:
                current_lap = self.laps
                step = LAP_DISTANCE
            effects = self.effect_summary(team)
            value = (
                f"{self.progress_bar(team['distance'])} "
                f"{team['distance']}/{self.finish_distance}\n"
                f"Lap **{current_lap}/{self.laps}** | Step **{step}/{LAP_DISTANCE}**\n"
                f"{self.team_label(team)}"
            )
            if effects:
                value += f"\n{effects}"
            embed.add_field(
                name=f"{place}. Team {team['index']} - {team['name']}",
                value=value,
                inline=False,
            )
        if self.total_spectator_bets:
            embed.add_field(
                name=_("Spectator Bet Pool"),
                value=f"${self.total_spectator_bets}",
                inline=True,
            )
        if log:
            embed.add_field(name=_("Race Log"), value="\n".join(log)[:1024], inline=False)
        return embed

    def effect_summary(self, team: dict) -> str:
        effects = []
        if int(team.get("immobile_turns", 0)) > 0:
            effects.append(f"Medusa {team['immobile_turns']}")
        if int(team.get("lady_luck_turns", 0)) > 0:
            effects.append(f"Luck {team['lady_luck_turns']}")
        if int(team.get("cerberus_turns", 0)) > 0:
            effects.append(f"Cerberus {team['cerberus_turns']}")
        if team.get("icarus_next"):
            effects.append("Icarus ready")
        if team.get("icarus_pending"):
            effects.append("Icarus next")
        if int(team.get("aegis_turns", 0)) > 0:
            effects.append("Aegis")
        if team.get("apollo_evade"):
            effects.append("Apollo")
        if team.get("apollo_pending"):
            effects.append("Apollo next")
        if team.get("nemesis_ready"):
            effects.append("Nemesis")
        if team.get("ariadne_thread"):
            effects.append("Ariadne")
        return "Effects: " + ", ".join(effects) if effects else ""

    async def send_cast(self):
        self.make_teams()
        embed = discord.Embed(
            title=_("Chariot Race Teams"),
            description=_(
                "Random teams of two have been hitched to their chariots. "
                "If a team is missing a second racer, the bot drives as their AI teammate."
            ),
            color=discord.Color.gold(),
        )
        for team in self.teams:
            embed.add_field(
                name=f"Team {team['index']} - {team['god']}",
                value=self.team_label(team),
                inline=False,
            )
        await self.ctx.send(embed=embed)

    async def betting_phase(self):
        self.betting_open = True
        prefix = self.ctx.clean_prefix
        await self.ctx.send(
            _(
                "Spectators have **{seconds}s** to bet on a team with "
                "`{prefix}chariot bet <amount> <team number>`."
            ).format(seconds=BETTING_TIMEOUT, prefix=prefix)
        )
        try:
            await asyncio.wait_for(self.stop_event.wait(), timeout=BETTING_TIMEOUT)
        except asyncio.TimeoutError:
            pass
        self.betting_open = False
        if self.stopped:
            return
        await self.ctx.send(_("Betting is closed. The race begins."))

    def deal_cards(self):
        for team in self.teams:
            team["card"] = random.choice(CARD_POOL)

    async def collect_choices(self):
        self.choices = {}
        self.card_choices = {}
        self.deal_cards()
        view = ChariotChoiceView(self)
        embed = discord.Embed(
            title=_("Round {round}: choose action and mystery card").format(round=self.round),
            description=_(
                "Each racer chooses **Forward**, **Defense**, **Attack**, **Rush**, or **Full Attack**.\n"
                "Each team also receives one mystery divine card. It only reveals and activates "
                "if both teammates choose **Use mystery card**."
            ),
            color=discord.Color.orange(),
        )
        message = await self.ctx.send(embed=embed, view=view)
        done_task = asyncio.create_task(view.done.wait())
        stop_task = asyncio.create_task(self.stop_event.wait())
        try:
            done, pending = await asyncio.wait(
                {done_task, stop_task},
                timeout=min(CHOICE_TIMEOUT, self.race_time_left()),
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                task.result()
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

        for player in self.players:
            self.choices.setdefault(player.id, "forward")
            self.card_choices.setdefault(player.id, False)

        for child in view.children:
            child.disabled = True
        try:
            await message.edit(view=view)
        except discord.HTTPException:
            pass

    def team_wants_card(self, team: dict) -> bool:
        return all(self.card_choices.get(player.id, False) for player in team["players"])

    def prepare_round_effects(self):
        for team in self.teams:
            team["round_defense"] = 0
            team["round_attack"] = 0
            team["card_step_bonus"] = 0
            team["card_defense_bonus"] = 0
            team["card_attack_bonus"] = 0
            team["skip_move_this_round"] = False
            team["icarus_fragile"] = False

    def random_team(self, exclude: dict | None = None) -> dict | None:
        teams = [team for team in self.teams if team is not exclude]
        if not teams:
            return None
        return random.choice(teams)

    def leader(self, exclude: dict | None = None) -> dict | None:
        teams = [team for team in self.teams if team is not exclude]
        if not teams:
            return None
        return sorted(teams, key=lambda team: team["distance"], reverse=True)[0]

    def set_team_distance(self, team: dict, distance: int):
        lap_floor = int(team.get("lap_floor", 0))
        new_distance = max(
            lap_floor,
            min(self.finish_distance, int(distance)),
        )
        team["distance"] = new_distance
        reached_floor = min(
            self.finish_distance,
            (new_distance // LAP_DISTANCE) * LAP_DISTANCE,
        )
        team["lap_floor"] = max(lap_floor, reached_floor)

    def move_team(self, team: dict, amount: int):
        self.set_team_distance(team, int(team["distance"]) + int(amount))

    def reveal_cards(self, log: list[str]):
        for team in self.teams:
            if not self.team_wants_card(team):
                continue
            card_id, category = team["card"]
            self.apply_card(team, card_id, category, log)

    def apply_card(self, team: dict, card_id: str, category: str, log: list[str]):
        if card_id == "zeus_thunder":
            target = random.choice(self.teams)
            self.move_team(target, -2)
            log.append(f"{team['name']} revealed {category}: Zeus' Thunder hits {target['name']} (-2).")
        elif card_id == "satyr_stumble":
            self.global_skip_rounds = max(self.global_skip_rounds, 1)
            log.append(f"{team['name']} revealed {category}: a drunk satyr halts the race next turn.")
        elif card_id == "medusa_gaze":
            team["immobile_turns"] = int(team.get("immobile_turns", 0)) + 2
            log.append(f"{team['name']} revealed {category}: Medusa freezes them for 2 turns.")
        elif card_id == "poseidon_wave":
            for rival in [rival for rival in self.teams if rival is not team]:
                self.move_team(rival, -1)
            log.append(f"{team['name']} revealed {category}: Poseidon's wave pushes rivals back (-1).")
        elif card_id == "ares_riot":
            for racing_team in self.teams:
                racing_team["card_attack_bonus"] = int(racing_team["card_attack_bonus"]) + 1
            log.append(f"{team['name']} revealed {category}: Ares makes every attack stronger this turn.")
        elif card_id == "olympian_blessing":
            self.apply_olympian_blessing(team, log)
        elif card_id == "lady_luck":
            team["lady_luck_pending"] = max(int(team.get("lady_luck_pending", 0)), 2)
            log.append(f"{team['name']} revealed {category}: Lady Luck grants +2 steps for 2 turns.")
        elif card_id == "nemesis_backlash":
            attacker = team.get("last_attacker")
            damage = int(team.get("last_attack_damage", 0))
            if attacker and damage > 0:
                self.move_team(attacker, -(damage * 2))
                log.append(f"{team['name']} revealed {category}: Nemesis returns {damage * 2} damage to {attacker['name']}.")
            else:
                team["nemesis_ready"] = True
                log.append(f"{team['name']} revealed {category}: Nemesis waits for the next attacker.")
        elif card_id == "apollo_divination":
            team["apollo_pending"] = True
            log.append(f"{team['name']} revealed {category}: Apollo will evade the next attack and move them +1.")
        elif card_id == "athena_strategy":
            team["card_defense_bonus"] = int(team["card_defense_bonus"]) + 2
            team["card_step_bonus"] = int(team["card_step_bonus"]) + 1
            log.append(f"{team['name']} revealed {category}: Athena grants +2 defense and +1 step.")
        elif card_id == "hermes_boots":
            self.move_team(team, 3)
            log.append(f"{team['name']} revealed {category}: Hermes' Boots launch them forward (+3).")
        elif card_id == "pandora_box":
            self.move_team(team, -2)
            target = self.leader(exclude=team)
            if target:
                self.move_team(target, -5)
                log.append(f"{team['name']} revealed {category}: Pandora's Box drags them -2 and {target['name']} -5.")
            else:
                log.append(f"{team['name']} revealed {category}: Pandora's Box drags them -2.")
        elif card_id == "eros_arrow":
            target = self.random_team(exclude=team)
            if target:
                if random.randint(0, 1):
                    self.set_team_distance(team, int(target["distance"]))
                    log.append(f"{team['name']} revealed {category}: Eros teleports them to {target['name']}.")
                else:
                    self.set_team_distance(target, int(team["distance"]))
                    log.append(f"{team['name']} revealed {category}: Eros pulls {target['name']} to them.")
        elif card_id == "icarus_wings":
            team["icarus_pending"] = True
            log.append(f"{team['name']} revealed {category}: Icarus' Wings double their next movement, but attacks hit twice as hard.")
        elif card_id == "aegis_shield":
            team["aegis_turns"] = int(team.get("aegis_turns", 0)) + 1
            log.append(f"{team['name']} revealed {category}: Aegis turns incoming attacks into momentum.")
        elif card_id == "trojan_horse":
            team["trojan_waiting"] = True
            log.append(f"{team['name']} revealed {category}: Trojan Horse stops them now, then gives +6 next turn.")
        elif card_id == "cerberus":
            team["cerberus_pending"] = max(int(team.get("cerberus_pending", 0)), 3)
            log.append(f"{team['name']} revealed {category}: Tamed Cerberus gives +2 steps for 3 turns.")
        elif card_id == "ariadne_thread":
            team["ariadne_thread"] = True
            log.append(f"{team['name']} revealed {category}: Ariadne's Thread reduces the next knockback by 2.")

    def apply_olympian_blessing(self, team: dict, log: list[str]):
        god = random.choice(PATRON_GODS)
        if god == "Zeus":
            team["card_attack_bonus"] = int(team["card_attack_bonus"]) + 1
            effect = "+1 attack this turn"
        elif god == "Hera":
            team["card_defense_bonus"] = int(team["card_defense_bonus"]) + 2
            effect = "+2 defense this turn"
        elif god == "Poseidon":
            self.move_team(team, 2)
            effect = "+2 steps immediately"
        elif god == "Demeter":
            team["ariadne_thread"] = True
            effect = "the next knockback is reduced by 2"
        elif god == "Athena":
            team["card_defense_bonus"] = int(team["card_defense_bonus"]) + 1
            team["card_step_bonus"] = int(team["card_step_bonus"]) + 1
            effect = "+1 defense and +1 step this turn"
        elif god == "Apollo":
            team["apollo_pending"] = True
            effect = "evade the next attack and gain +1"
        elif god == "Artemis":
            team["card_attack_bonus"] = int(team["card_attack_bonus"]) + 1
            effect = "+1 precise attack this turn"
        elif god == "Ares":
            team["card_attack_bonus"] = int(team["card_attack_bonus"]) + 2
            effect = "+2 attack this turn"
        elif god == "Aphrodite":
            target = self.random_team(exclude=team)
            if target:
                target["card_attack_bonus"] = int(target["card_attack_bonus"]) - 1
                effect = f"{target['name']} loses 1 attack this turn"
            else:
                effect = "no rival is close enough to charm"
        elif god == "Hephaestus":
            team["aegis_turns"] = int(team.get("aegis_turns", 0)) + 1
            effect = "Aegis activates for one turn"
        elif god == "Hermes":
            self.move_team(team, 3)
            effect = "+3 steps immediately"
        else:
            team["lady_luck_pending"] = max(int(team.get("lady_luck_pending", 0)), 1)
            effect = "+2 steps next turn"

        log.append(f"{team['name']} revealed Divine Blessing: {god} grants {effect}.")

    def choose_target(self, acting_team: dict) -> dict | None:
        rivals = [team for team in self.teams if team is not acting_team]
        if not rivals:
            return None
        return min(
            rivals,
            key=lambda team: (
                abs(int(team["distance"]) - int(acting_team["distance"])),
                int(team["distance"]) <= int(acting_team["distance"]),
                -int(team["distance"]),
            ),
        )

    def resolve_team(self, team: dict, log: list[str]):
        if int(team.get("immobile_turns", 0)) > 0:
            team["round_defense"] = int(team["card_defense_bonus"])
            team["round_attack"] = 0
            log.append(f"{team['name']} is immobilised by Medusa and cannot move or attack.")
            return

        actions = [self.action_for(player) for player in team["players"]]
        if len(actions) == 1:
            actions.append(random.choice(list(ACTION_LABELS)))

        names = " + ".join(ACTION_LABELS[action] for action in actions)
        combo = tuple(sorted(actions))
        gain = 0
        defense = 0
        attack = 0
        rush_count = actions.count("rush")
        full_attack_count = actions.count("full_attack")

        if full_attack_count:
            attack = 5 * full_attack_count
            if "defense" in actions:
                defense += 1
            if "attack" in actions:
                attack += 1
            log.append(f"{team['name']} commits to Full Attack and sacrifices movement.")
        elif rush_count:
            for _ in range(rush_count):
                if random.randint(0, 1):
                    gain += 5
                    log.append(f"{team['name']} rushes forward (+5).")
                else:
                    gain -= 3
                    log.append(f"{team['name']}'s rush backfires (-3).")
            if "defense" in actions:
                defense += 1
            if "attack" in actions:
                attack += 1
            if "forward" in actions:
                gain += 1
        elif combo == ("forward", "forward"):
            gain = 3
        elif combo == ("defense", "forward"):
            gain = 2
            defense = 1
        elif combo == ("attack", "forward"):
            gain = 2
            attack = 1
        elif combo == ("defense", "defense"):
            gain = 1
            defense = 2
        elif combo == ("attack", "defense"):
            gain = 1
            attack = 1
        elif combo == ("attack", "attack"):
            gain = 1
            attack = 2

        if team.get("trojan_waiting"):
            team["trojan_waiting"] = False
            team["trojan_bonus_next"] = int(team.get("trojan_bonus_next", 0)) + 6
            team["skip_move_this_round"] = True

        if team.get("skip_move_this_round"):
            gain = 0
        else:
            if int(team.get("lady_luck_turns", 0)) > 0:
                gain += 2
            if int(team.get("cerberus_turns", 0)) > 0:
                gain += 2
            if int(team.get("trojan_bonus_next", 0)) > 0:
                gain += int(team["trojan_bonus_next"])
                team["trojan_bonus_next"] = 0
            gain += int(team["card_step_bonus"])
            if team.get("icarus_next"):
                gain *= 2
                team["icarus_next"] = False
                team["icarus_fragile"] = True

        defense += int(team["card_defense_bonus"])
        attack += int(team["card_attack_bonus"])
        attack = max(0, attack)

        team["round_defense"] = defense
        team["round_attack"] = attack
        self.move_team(team, gain)
        log.append(
            f"{team['name']}: {names} -> +{gain} step(s), +{defense} defense, +{attack} attack."
        )

    def apply_attacks(self, log: list[str]):
        for team in self.teams:
            attack = int(team.get("round_attack", 0))
            if attack <= 0:
                continue
            target = self.choose_target(team)
            if target is None:
                continue

            if int(target.get("aegis_turns", 0)) > 0:
                self.move_team(target, attack)
                self.move_team(team, -1)
                target["last_attacker"] = team
                target["last_attack_damage"] = 0
                log.append(f"{team['name']} attacks {target['name']}, but Aegis turns it into +{attack} and knocks the attacker -1.")
                continue

            if target.get("apollo_evade"):
                target["apollo_evade"] = False
                self.move_team(target, 1)
                target["last_attacker"] = team
                target["last_attack_damage"] = 0
                log.append(f"{team['name']} attacks {target['name']}, but Apollo's divination evades it (+1).")
                continue

            defense = int(target.get("round_defense", 0))
            damage = max(0, attack - defense)
            if target.get("icarus_fragile"):
                damage *= 2
            if target.get("ariadne_thread") and damage > 0:
                old_damage = damage
                damage = max(0, damage - 2)
                target["ariadne_thread"] = False
                log.append(f"Ariadne's Thread reduces damage on {target['name']} from {old_damage} to {damage}.")

            target["last_attacker"] = team
            target["last_attack_damage"] = damage
            if damage == 0:
                log.append(f"{team['name']} attacks {target['name']}, but defense cancels it.")
                continue

            self.move_team(target, -damage)
            log.append(f"{team['name']} attacks {target['name']}: -{damage} step(s).")

            if target.get("nemesis_ready"):
                target["nemesis_ready"] = False
                backlash = damage * 2
                self.move_team(team, -backlash)
                log.append(f"Nemesis punishes {team['name']} for {backlash} step(s).")

    def tick_effects(self):
        for team in self.teams:
            for key in ("immobile_turns", "lady_luck_turns", "cerberus_turns", "aegis_turns"):
                if int(team.get(key, 0)) > 0:
                    team[key] = int(team[key]) - 1
            if int(team.get("lady_luck_pending", 0)) > 0:
                team["lady_luck_turns"] = max(
                    int(team.get("lady_luck_turns", 0)),
                    int(team["lady_luck_pending"]),
                )
                team["lady_luck_pending"] = 0
            if int(team.get("cerberus_pending", 0)) > 0:
                team["cerberus_turns"] = max(
                    int(team.get("cerberus_turns", 0)),
                    int(team["cerberus_pending"]),
                )
                team["cerberus_pending"] = 0
            if team.get("icarus_pending"):
                team["icarus_next"] = True
                team["icarus_pending"] = False
            if team.get("apollo_pending"):
                team["apollo_evade"] = True
                team["apollo_pending"] = False

    async def play_round(self):
        if self.stopped:
            return

        if self.global_skip_rounds > 0:
            self.global_skip_rounds -= 1
            await self.ctx.send(
                embed=self.standings_embed(
                    _("Chariot Race: round {round} halted").format(round=self.round),
                    [_("A drunk satyr blocks the hippodrome. Nobody moves this round.")],
                )
            )
            self.round += 1
            return

        await self.collect_choices()
        if self.stopped:
            return
        log: list[str] = []
        self.prepare_round_effects()
        self.reveal_cards(log)
        for team in self.teams:
            self.resolve_team(team, log)
        self.apply_attacks(log)
        self.tick_effects()
        await self.ctx.send(
            embed=self.standings_embed(
                _("Chariot Race: round {round} results").format(round=self.round),
                log,
            )
        )
        self.round += 1

    def winners(self) -> list[dict]:
        return [
            team
            for team in self.teams
            if int(team["distance"]) >= self.finish_distance
        ]

    async def pay_entry_pot(self, winner: dict, log: list[str]):
        if self.entry_pot <= 0:
            return
        humans = list(winner["players"])
        if not humans:
            return
        split = self.entry_pot // len(humans)
        remainder = self.entry_pot % len(humans)
        async with self.bot.pool.acquire() as conn:
            for index, player in enumerate(humans):
                payout = split + (remainder if index == 0 else 0)
                if payout > 0:
                    await conn.execute(
                        'UPDATE profile SET "money"="money"+$1 WHERE "user"=$2;',
                        payout,
                        player.id,
                    )
        log.append(f"Entry pot paid to the winning team: ${self.entry_pot}.")

    async def pay_sponsor_reward(self, winner: dict, log: list[str]):
        if self.sponsor_reward <= 0:
            return
        humans = list(winner["players"])
        if not humans:
            return
        split = self.sponsor_reward // len(humans)
        remainder = self.sponsor_reward % len(humans)
        async with self.bot.pool.acquire() as conn:
            for index, player in enumerate(humans):
                payout = split + (remainder if index == 0 else 0)
                if payout > 0:
                    await conn.execute(
                        'UPDATE profile SET "money"="money"+$1 WHERE "user"=$2;',
                        payout,
                        player.id,
                    )
        log.append(f"Sponsored reward paid to the winning team: ${self.sponsor_reward}.")

    async def pay_spectator_bets(self, winner: dict, log: list[str]):
        if not self.bets:
            return
        winning_bets = [bet for bet in self.bets if int(bet.team_index) == int(winner["index"])]
        if not winning_bets:
            log.append("No spectators picked the winning team. Spectator bet pool is burned.")
            return
        split = self.total_spectator_bets // len(winning_bets)
        remainder = self.total_spectator_bets % len(winning_bets)
        async with self.bot.pool.acquire() as conn:
            for index, bet in enumerate(winning_bets):
                payout = split + (remainder if index == 0 else 0)
                await conn.execute(
                    'UPDATE profile SET "money"="money"+$1 WHERE "user"=$2;',
                    payout,
                    bet.bettor.id,
                )
                try:
                    await bet.bettor.send(f"You won **${payout}** from your Chariot Race bet on Team {winner['index']}!")
                except discord.HTTPException:
                    pass
        log.append(f"Spectator bet pool paid out: ${self.total_spectator_bets}.")

    async def main(self):
        await self.send_cast()
        if self.stopped:
            return
        await self.betting_phase()
        if self.stopped:
            return
        self.race_deadline = asyncio.get_running_loop().time() + RACE_TIMEOUT_SECONDS
        await self.ctx.send(embed=self.standings_embed(_("Chariot Race begins!")))

        while not self.stopped and not self.winners():
            if self.race_time_left() <= 0:
                break
            await self.play_round()
            if self.stopped:
                break
            try:
                await asyncio.wait_for(
                    self.stop_event.wait(),
                    timeout=min(2, self.race_time_left()),
                )
            except asyncio.TimeoutError:
                pass

        if self.stopped:
            return

        finished = self.winners()
        timed_out = not finished
        winners = sorted(
            finished or self.teams,
            key=lambda team: (team["distance"], -team["index"]),
            reverse=True,
        )
        winner = winners[0]
        payout_log: list[str] = []
        await self.pay_entry_pot(winner, payout_log)
        await self.pay_sponsor_reward(winner, payout_log)
        await self.pay_spectator_bets(winner, payout_log)

        embed = discord.Embed(
            title=_("Chariot Race Results"),
            description=(
                _(
                    "The 1 hour limit was reached. {team} wins by current distance!"
                ).format(team=winner["name"])
                if timed_out
                else _("{team} wins the race!").format(team=winner["name"])
            ),
            color=discord.Color.green(),
        )
        embed.add_field(name=_("Victors"), value=self.team_label(winner), inline=False)
        embed.add_field(
            name=_("Final Distance"),
            value=f"{winner['distance']}/{self.finish_distance}",
            inline=False,
        )
        if payout_log:
            embed.add_field(name=_("Rewards"), value="\n".join(payout_log), inline=False)
        await self.ctx.send(embed=embed)


class ChariotRace(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.lobbies = {}
        self.active_games = {}

    async def _balance(self, user_id: int) -> int:
        async with self.bot.pool.acquire() as conn:
            return int(
                await conn.fetchval(
                    'SELECT "money" FROM profile WHERE "user" = $1;',
                    user_id,
                )
                or 0
            )

    async def _add_money(self, user_id: int, amount: int):
        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                'UPDATE profile SET "money"="money"+$1 WHERE "user"=$2;',
                int(amount),
                user_id,
            )

    def _lobby_embed(self, lobby: dict) -> discord.Embed:
        players = lobby["players"]
        remaining = int(lobby["remaining"])
        host = lobby["host"]
        laps = int(lobby["laps"])
        entry_fee = int(lobby["entry_fee"])
        embed = discord.Embed(
            title=_("Chariot Race Lobby"),
            description=_("Join with buttons. Auto-starts when the timer ends."),
            color=discord.Color.gold(),
        )
        embed.add_field(name=_("Host"), value=host.mention, inline=True)
        embed.add_field(name=_("Laps"), value=str(laps), inline=True)
        embed.add_field(name=_("Starts in"), value=f"{remaining}s", inline=True)
        embed.add_field(
            name=_("Entry Fee"),
            value=f"${entry_fee}" if entry_fee else _("Free"),
            inline=True,
        )
        embed.add_field(name=_("Pot"), value=f"${int(lobby['entry_pot'])}", inline=True)
        if players:
            embed.add_field(
                name=_("Players ({count})").format(count=len(players)),
                value=", ".join(player.mention for player in players),
                inline=False,
            )
        else:
            embed.add_field(name=_("Players (0)"), value=_("No one yet."), inline=False)
        embed.set_footer(text=_("Minimum 2 players. Host/GM can Begin Now."))
        return embed

    async def refund_lobby(self, lobby: dict):
        entry_fee = int(lobby["entry_fee"])
        if entry_fee <= 0:
            return
        for user_id in list(lobby["paid"]):
            await self._add_money(user_id, entry_fee)
        lobby["paid"].clear()
        lobby["entry_pot"] = 0

    async def refund_game(self, game: ChariotGame):
        entry_fee = max(0, int(getattr(game, "entry_fee", 0)))
        paid_user_ids = getattr(game, "paid_user_ids", set())
        if entry_fee > 0:
            for user_id in list(paid_user_ids):
                await self._add_money(user_id, entry_fee)
            paid_user_ids.clear()
            game.entry_pot = 0

        bets = getattr(game, "bets", None)
        if bets is not None:
            for bet in list(bets):
                await self._add_money(bet.bettor.id, bet.amount)
            bets.clear()
        if hasattr(game, "total_spectator_bets"):
            game.total_spectator_bets = 0

    async def can_stop(self, ctx: commands.Context, host) -> bool:
        is_admin = ctx.author.guild_permissions.manage_guild or ctx.author.guild_permissions.administrator
        is_gm_user = await user_is_gm(self.bot, ctx.author)
        return bool(
            is_admin
            or is_gm_user
            or (host and ctx.author.id == host.id)
        )

    def _iter_task_frames(self, task: asyncio.Task):
        seen = set()
        stack = [task.get_coro()]
        while stack:
            awaitable = stack.pop()
            if awaitable is None:
                continue
            awaitable_id = id(awaitable)
            if awaitable_id in seen:
                continue
            seen.add(awaitable_id)

            frame = (
                getattr(awaitable, "cr_frame", None)
                or getattr(awaitable, "gi_frame", None)
                or getattr(awaitable, "ag_frame", None)
            )
            if frame is not None:
                yield frame

            for attr in ("cr_await", "gi_yieldfrom", "ag_await"):
                nested = getattr(awaitable, attr, None)
                if nested is not None:
                    stack.append(nested)

    def _game_from_value(self, value, channel_id: int):
        if value.__class__.__name__ == "ChariotGame":
            game = value
        else:
            game = getattr(value, "game", None)
            if game is None or game.__class__.__name__ != "ChariotGame":
                return None

        ctx = getattr(game, "ctx", None)
        channel = getattr(ctx, "channel", None)
        if getattr(channel, "id", None) == channel_id:
            return game
        return None

    def chariot_tasks_for_channel(self, channel_id: int):
        current = asyncio.current_task()
        matches = []
        games = {}
        for task in asyncio.all_tasks():
            if task is current or task.done():
                continue
            matched_game = None
            for frame in self._iter_task_frames(task):
                for value in frame.f_locals.values():
                    matched_game = self._game_from_value(value, channel_id)
                    if matched_game is not None:
                        break
                if matched_game:
                    break
            if matched_game:
                matches.append(task)
                games[id(matched_game)] = matched_game
        return matches, list(games.values())

    @commands.group(name="chariot", aliases=["cr", "chariotrace"], invoke_without_command=True)
    @commands.guild_only()
    async def chariot(self, ctx: commands.Context):
        await self.chariot_help(ctx)

    @chariot.command(name="help")
    async def chariot_help(self, ctx: commands.Context):
        prefix = ctx.clean_prefix
        await ctx.send(
            "**Chariot Race Help**\n\n"
            f"Start: `{prefix}chariot start [laps] [entry_fee]`\n"
            f"Bet: `{prefix}chariot bet <amount> <team number>` during the betting phase\n"
            f"Stop lobby/race: `{prefix}chariot stop`\n\n"
            "Teams are random pairs. Odd player count gives the last racer an AI teammate.\n"
            "With only 2 players, each racer gets their own AI teammate.\n"
            "A race can last at most 1 hour; if nobody finishes, the current leader wins.\n"
            "Each lap is 8 steps. Each round, both teammates choose Forward, Defense, Attack, Rush, or Full Attack.\n"
            "Rush is 50/50: +5 steps or -3 steps. Full Attack gives no movement but +5 attack.\n"
            "Each team gets a mystery divine card every round; it activates only if both teammates choose to use it."
        )

    @chariot.command(name="start", brief=_("Start a team Chariot Race"))
    @locale_doc
    async def chariot_start(self, ctx, laps: int = 3, entry_fee: int = 0):
        _(
            """Start a team Chariot Race.

            Players join with the lobby buttons, then random teams of two race.
            Each round, every racer chooses Forward, Defense, or Attack.
            Every team gets a mystery divine card that activates only if both teammates use it.
            """
        )
        if not isinstance(ctx.channel, discord.TextChannel):
            return await ctx.send(_("Start Chariot Race in a server text channel."))
        if ctx.channel.id in self.lobbies:
            return await ctx.send(_("A Chariot Race lobby is already open in this channel."))
        if ctx.channel.id in self.active_games:
            return await ctx.send(_("A Chariot Race is already running in this channel."))

        laps = max(1, int(laps))
        entry_fee = max(0, int(entry_fee))
        if entry_fee > 0:
            balance = await self._balance(ctx.author.id)
            if balance < entry_fee:
                return await ctx.send(
                    _("{user}, you need **${amount}** to host this race.").format(
                        user=ctx.author.mention,
                        amount=entry_fee,
                    )
                )
            await self._add_money(ctx.author.id, -entry_fee)

        auto_minigame = getattr(ctx, "auto_minigame", False)
        sponsor_reward = max(0, int(getattr(ctx, "scheduled_reward", 0))) if auto_minigame else 0
        force_begin = asyncio.Event()
        lobby = {
            "host": ctx.author,
            "laps": laps,
            "entry_fee": entry_fee,
            "entry_pot": 0 if auto_minigame else entry_fee,
            "paid": set() if auto_minigame or entry_fee <= 0 else {ctx.author.id},
            "players": [] if auto_minigame else [ctx.author],
            "force_begin": force_begin,
            "remaining": JOIN_TIMEOUT,
            "message": None,
            "stopped": False,
        }
        self.lobbies[ctx.channel.id] = lobby

        view = ChariotLobbyView(self, ctx.channel.id, ctx.author.id, timeout=JOIN_TIMEOUT)
        msg = await ctx.send(embed=self._lobby_embed(lobby), view=view)
        lobby["message"] = msg

        try:
            end_at = asyncio.get_running_loop().time() + JOIN_TIMEOUT
            while True:
                if force_begin.is_set():
                    break
                remaining = int(max(0, end_at - asyncio.get_running_loop().time()))
                lobby["remaining"] = remaining
                try:
                    await msg.edit(embed=self._lobby_embed(lobby), view=view)
                except discord.HTTPException:
                    pass
                if remaining <= 0:
                    break
                await asyncio.sleep(5)
        finally:
            for child in view.children:
                child.disabled = True
            try:
                await msg.edit(view=view)
            except discord.HTTPException:
                pass

        players = list(lobby["players"])
        if lobby.get("stopped"):
            return
        if len(players) < MIN_PLAYERS:
            self.lobbies.pop(ctx.channel.id, None)
            await self.refund_lobby(lobby)
            await self.bot.reset_cooldown(ctx)
            return await ctx.send(_("Lobby ended: need at least **2** players to start. Entry fees refunded."))

        self.lobbies.pop(ctx.channel.id, None)
        game = ChariotGame(
            self.bot,
            ctx,
            players=players,
            laps=laps,
            entry_pot=int(lobby["entry_pot"]),
            sponsor_reward=sponsor_reward,
            entry_fee=entry_fee,
            paid_user_ids=set(lobby["paid"]),
        )
        self.active_games[ctx.channel.id] = game
        try:
            await game.main()
        except Exception as exc:
            await ctx.send(_("An error happened during the Chariot Race. Please try again!"))
            raise exc
        finally:
            self.active_games.pop(ctx.channel.id, None)

    @chariot.command(name="bet")
    async def chariot_bet(self, ctx, amount: int, team_number: int):
        game = self.active_games.get(ctx.channel.id)
        if not game or not game.betting_open:
            return await ctx.send(_("There is no Chariot Race accepting bets right now."))
        if ctx.author in game.players:
            return await ctx.send(_("Racers cannot place spectator bets."))
        if amount <= 0:
            return await ctx.send(_("Bet amount must be greater than zero."))
        if any(bet.bettor.id == ctx.author.id for bet in game.bets):
            return await ctx.send(_("You have already placed a Chariot Race bet."))

        target = game.team_by_index(team_number)
        if not target:
            return await ctx.send(_("That team number does not exist."))

        balance = await self._balance(ctx.author.id)
        if balance < amount:
            return await ctx.send(_("You do not have enough money to place that bet."))

        await self._add_money(ctx.author.id, -amount)
        game.bets.append(ChariotBet(ctx.author, int(amount), int(team_number)))
        game.total_spectator_bets += int(amount)
        await ctx.send(
            _("{user} bet **${amount}** on **Team {team}**.").format(
                user=ctx.author.mention,
                amount=amount,
                team=team_number,
            )
        )

    @chariot.command(name="stop")
    async def chariot_stop(self, ctx):
        stopped_any = False
        lobby = self.lobbies.get(ctx.channel.id)
        game = self.active_games.get(ctx.channel.id)
        race_tasks, task_games = self.chariot_tasks_for_channel(ctx.channel.id)

        if not lobby and not game and not race_tasks:
            return await ctx.send(_("There is no Chariot Race lobby or active race to stop."))

        host = lobby["host"] if lobby else getattr(getattr(game, "ctx", None), "author", None)
        if host is None and task_games:
            host = getattr(getattr(task_games[0], "ctx", None), "author", None)
        if not await self.can_stop(ctx, host):
            return await ctx.send(_("Only the host/GM can stop this Chariot Race."))

        if lobby:
            self.lobbies.pop(ctx.channel.id, None)
            lobby["stopped"] = True
            lobby["force_begin"].set()
            await self.refund_lobby(lobby)
            stopped_any = True

        if game:
            self.active_games.pop(ctx.channel.id, None)
            game.stop(f"Stopped by {ctx.author}.")
            await self.refund_game(game)
            stopped_any = True

        for task_game in task_games:
            if task_game is game:
                continue
            stop = getattr(task_game, "stop", None)
            if callable(stop):
                stop(f"Stopped by {ctx.author}.")
            await self.refund_game(task_game)
            stopped_any = True

        for task in race_tasks:
            task.cancel()
            stopped_any = True
        if race_tasks:
            await asyncio.sleep(0)

        if stopped_any:
            await ctx.send(_("Chariot Race stopped. Entry fees and spectator bets refunded."))


async def setup(bot):
    await bot.add_cog(ChariotRace(bot))
