"""Player-only Hunt balance, independent of tracking requirements."""

HUNT_HP_PER_HUNTER = 12_000
HUNT_BEAST_ATTACK = 1_200
HUNT_BEAST_ARMOR = 260
HUNT_DEFENSE_SCALE = 1_500
HUNT_MAX_ROUNDS = 20


def build_beast(name, party_size):
    if party_size < 1:
        raise ValueError("A Hunt needs at least one hunter.")
    hp = float(HUNT_HP_PER_HUNTER * party_size)
    return {
        "name": name, "hp": hp, "max_hp": hp,
        "damage": float(HUNT_BEAST_ATTACK), "armor": float(HUNT_BEAST_ARMOR),
        "party_size": party_size,
    }


def incoming_damage(attack, defense):
    """Diminishing mitigation: 1,500 defense halves damage, never grants immunity."""
    return max(1.0, float(attack) * HUNT_DEFENSE_SCALE / (HUNT_DEFENSE_SCALE + max(0.0, float(defense))))


def attack_target_count(party_size, round_no, living_count):
    """One hit per starting hunter per three rounds, capped by survivors.

    Spread fractional quotas over rounds instead of always rounding up; a party
    of ten receives 3/3/4 attacks, while twenty receives 6/7/7. Never rescale the
    original attack budget or beast HP down when hunters fall.
    """
    quota = round_no * party_size // 3 - (round_no - 1) * party_size // 3
    return min(living_count, quota)
