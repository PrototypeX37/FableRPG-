"""Phase 1 PvE data for The Dreambound Spring.

Keep all seasonal PvE monster tuning here. Command logic should read from this
module instead of hardcoding encounter values.
"""
from __future__ import annotations

SPRING_PVE_CONFIG = {
    "COOLDOWN_SECONDS": 1800,
    "SEARCH_MIN_SECONDS": 2,
    "SEARCH_MAX_SECONDS": 5,
    "TURN_DELAY_SECONDS": 1,
}

# Fallback rewards used when monster reward placeholders are not configured yet.
SPRING_PVE_REWARD_DEFAULTS = {
    "easy": {
        "seed_type": "GOOD_DREAM_SEED",
        "bloomshards": 25,
    },
    "normal": {
        "seed_type": "GREAT_DREAM_SEED",
        "bloomshards": 55,
    },
    "hard": {
        "seed_type": "NIGHTMARE_SEED",
        "bloomshards": 95,
    },
}

SPRING_PVE_DIVINE_SHARD_CHANCE_PCT = {
    "easy": 5,
    "normal": 7,
    "hard": 10,
}

# Engine support notes for custom monster effects.
SPRING_PVE_EFFECT_SUPPORT = {
    "basic_attack": "native",
    "self_buff": "partial",
    "apply_status": "partial",
    "stun_player": "partial",
    "damage_and_debuff": "planned",
    "dot_percent_current_hp": "planned",
    "damage_and_stun": "planned",
    "heal_player_percent_current_hp": "planned",
    "reverse_next_player_damage": "planned",
    "player_loses_percent_max_hp": "planned",
    "lifesteal_percent_max_hp": "planned",
    "damage_percent_max_hp_and_stun": "planned",
    "true_damage_flat": "planned",
    "multiattack_next_turns": "planned",
    "damage_percent_max_hp": "planned",
    "negate_last_player_damage_or_heal": "planned",
    "self_heal_percent_current_hp": "partial",
    "permanent_burn_stack_percent_max_hp": "planned",
    "stacking_player_accuracy_reduction": "planned",
    "multiattack": "planned",
    "execute_percent_starting_hp": "planned",
    "dot_percent_starting_hp": "planned",
}

SPRING_PVE_MONSTERS = [
    {
        "id": "good_dream",
        "name": "Good Dream",
        "difficulty": "easy",
        "image_url": "https://i.imgur.com/KyN3Dvv.png",
        "min_player_level": 1,
        "weight": 28,
        "level": 1,
        "element": "Light",
        "stats": {
            "hp": 280,
            "atk": 145,
            "def": 160,
        },
        "discovery_text": (
            "A gentle calm washes over you, silencing every ache and fear you carried in. "
            "The world here feels warm, familiar, like a memory you never want to leave. "
            "Soft light drapes itself over rolling shapes that almost resemble home.\n"
            "Your instincts scream too late: this dream does not wish you harm... it simply "
            "does not wish to let you go."
        ),
        "defeat_text": (
            "The warmth fractures, light shattering like glass in slow motion. "
            "The dream recoils, its serenity finally broken.\n"
            "You awaken gasping, heart pounding, unsure whether you won... or simply escaped."
        ),
        "fail_text": (
            "The pain fades first. Then the fear.\n"
            "Cradled by warmth and memory, you stop struggling.\n"
            "You never wake, because in a Good Dream, waking is the cruelest fate of all."
        ),
        "reward": {
            "seed_type": "GOOD_DREAM_SEED",
            "bloomshards": "REPLACE_ME_GOOD_DREAM_BLOOMSHARDS",
            "crate_id": "GOOD_DREAM_CRATE_PLACEHOLDER",
            "crate_amount": 15,
            "divine_shard_chance_pct": 5,
        },
        "abilities": [
            {
                "key": "gentle_overwhelm",
                "name": "Gentle Overwhelm",
                "chance_pct": 30,
                "effect": {
                    "type": "damage_and_debuff",
                    "damage_scale": 1.0,
                    "debuff": {
                        "type": "player_damage_mult",
                        "value": 0.70,
                        "duration_turns": 1,
                    },
                },
                "flavor": "A wave of comfort crashes over you, heavier than it should be...",
            },
            {
                "key": "sweet_reverie",
                "name": "Sweet Reverie",
                "chance_pct": 15,
                "effect": {
                    "type": "dot_percent_current_hp",
                    "pct": 10,
                    "duration_turns": 2,
                },
                "flavor": "Perfect memories bloom before your eyes...",
            },
            {
                "key": "lulling_descent",
                "name": "Lulling Descent",
                "chance_pct": 10,
                "effect": {
                    "type": "damage_and_stun",
                    "damage_pct_of_attack": 10,
                    "stun_turns": 1,
                },
                "flavor": "A soft voice whispers promises of rest...",
            },
            {
                "key": "serene_touch",
                "name": "Serene Touch",
                "chance_pct": 45,
                "effect": {"type": "basic_attack"},
                "flavor": "The dream brushes past you like a warm breeze...",
            },
        ],
    },
    {
        "id": "bad_dream",
        "name": "Bad Dream",
        "difficulty": "easy",
        "image_url": "https://i.imgur.com/m0l7Ka7.png",
        "min_player_level": 1,
        "weight": 26,
        "level": 2,
        "element": "Dark",
        "stats": {
            "hp": 240,
            "atk": 175,
            "def": 120,
        },
        "discovery_text": (
            "The air feels stale, like a room that has been slept in too long...\n"
            "You have stumbled into a Bad Dream."
        ),
        "defeat_text": (
            "The pressure eases. The world steadies. Colors return, sharper than before..."
        ),
        "fail_text": (
            "You struggle, but everything feels delayed, ineffective...\n"
            "You simply never manage to wake up."
        ),
        "reward": {
            "seed_type": "BAD_DREAM_SEED",
            "bloomshards": "REPLACE_ME_BAD_DREAM_BLOOMSHARDS",
            "crate_id": "BAD_DREAM_CRATE_PLACEHOLDER",
            "crate_amount": 15,
            "divine_shard_chance_pct": 5,
        },
        "abilities": [
            {
                "key": "disorienting_loop",
                "name": "Disorienting Loop",
                "chance_pct": 30,
                "effect": {
                    "type": "apply_status",
                    "status": "player_miss_chance",
                    "value_pct": 45,
                    "duration_turns": 1,
                },
                "flavor": "The scene repeats itself - a step forward...",
            },
            {
                "key": "lingering_anxiety",
                "name": "Lingering Anxiety",
                "chance_pct": 20,
                "effect": {
                    "type": "self_buff",
                    "buff": "attack_damage_mult",
                    "value": 1.30,
                    "duration_turns": 3,
                },
                "flavor": "A tightness forms in your chest...",
            },
            {
                "key": "frustrating_paralysis",
                "name": "Frustrating Paralysis",
                "chance_pct": 10,
                "effect": {
                    "type": "stun_player",
                    "duration_turns": 1,
                },
                "flavor": "You try to act - but your body responds a moment too late...",
            },
            {
                "key": "uneasy_strike",
                "name": "Uneasy Strike",
                "chance_pct": 40,
                "effect": {"type": "basic_attack"},
                "flavor": "The dream lashes out clumsily...",
            },
        ],
    },
    {
        "id": "neutral_dream",
        "name": "Neutral Dream",
        "difficulty": "easy",
        "image_url": "https://i.imgur.com/Mqem5Iq.png",
        "min_player_level": 4,
        "weight": 22,
        "level": 3,
        "element": "Nature",
        "stats": {
            "hp": 310,
            "atk": 150,
            "def": 175,
        },
        "discovery_text": (
            "You arrive without transition. No warmth, no dread - just awareness...\n"
            "This dream simply continues."
        ),
        "defeat_text": (
            "The space destabilizes - not violently, but inefficiently..."
        ),
        "fail_text": (
            "There is no final blow. No panic. No release.\n"
            "The dream continues, and so do you..."
        ),
        "reward": {
            "seed_type": "NEUTRAL_DREAM_SEED",
            "bloomshards": "REPLACE_ME_NEUTRAL_DREAM_BLOOMSHARDS",
            "crate_id": "NEUTRAL_DREAM_CRATE_PLACEHOLDER",
            "crate_amount": 10,
            "divine_shard_chance_pct": 5,
        },
        "abilities": [
            {
                "key": "passive_pressure",
                "name": "Passive Pressure",
                "chance_pct": 30,
                "effect": {
                    "type": "self_buff",
                    "buff": "attack_damage_mult",
                    "value": 1.10,
                    "duration_turns": 1,
                },
                "flavor": "The air thickens imperceptibly...",
            },
            {
                "key": "blank_interval",
                "name": "Blank Interval",
                "chance_pct": 10,
                "effect": {
                    "type": "heal_player_percent_current_hp",
                    "pct": 10,
                },
                "flavor": "The dream forgets you exist - your pain vanishes for a moment.",
            },
            {
                "key": "delusion",
                "name": "Delusion",
                "chance_pct": 10,
                "effect": {
                    "type": "reverse_next_player_damage",
                    "duration_turns": 1,
                },
                "flavor": "You see a wounded friend instead of an enemy...",
            },
            {
                "key": "standard_continuation",
                "name": "Standard Continuation",
                "chance_pct": 50,
                "effect": {"type": "basic_attack"},
                "flavor": "The dream adjusts, corrects, and continues...",
            },
        ],
    },
    {
        "id": "great_dream",
        "name": "Great Dream",
        "difficulty": "normal",
        "image_url": "https://i.imgur.com/tJHvcwu.png",
        "min_player_level": 10,
        "weight": 18,
        "level": 4,
        "element": "Light",
        "stats": {
            "hp": 620,
            "atk": 480,
            "def": 360,
        },
        "discovery_text": "Light pours in from everywhere at once...",
        "defeat_text": "The light fractures into countless colors...",
        "fail_text": (
            "You stop fighting - not from fear, but from certainty..."
        ),
        "reward": {
            "seed_type": "GREAT_DREAM_SEED",
            "bloomshards": "REPLACE_ME_GREAT_DREAM_BLOOMSHARDS",
            "crate_id": "GREAT_DREAM_CRATE_PLACEHOLDER",
            "crate_amount": 3,
            "divine_shard_chance_pct": 7,
        },
        "abilities": [
            {
                "key": "exalting_surge",
                "name": "Exalting Surge",
                "chance_pct": 15,
                "effect": {
                    "type": "player_loses_percent_max_hp",
                    "pct": 30,
                },
                "flavor": "Power floods your body... the dream takes more than it gave.",
            },
            {
                "key": "vision_of_perfection",
                "name": "Vision of Perfection",
                "chance_pct": 20,
                "effect": {
                    "type": "lifesteal_percent_max_hp",
                    "pct": 15,
                },
                "flavor": "You glimpse the perfect version of yourself...",
            },
            {
                "key": "ascendant_moment",
                "name": "Ascendant Moment",
                "chance_pct": 15,
                "effect": {
                    "type": "damage_percent_max_hp_and_stun",
                    "pct": 20,
                    "stun_turns": 1,
                },
                "flavor": "Time slows... then everything crashes back down.",
            },
            {
                "key": "radiant_pressure",
                "name": "Radiant Pressure",
                "chance_pct": 50,
                "effect": {
                    "type": "basic_attack",
                    "notes": "This monster has higher base ATK than other dreams.",
                },
                "flavor": "The brilliance presses in around you...",
            },
        ],
    },
    {
        "id": "forgotten_dream",
        "name": "Forgotten Dream",
        "difficulty": "normal",
        "image_url": "https://i.imgur.com/1rNkLKx.png",
        "min_player_level": 14,
        "weight": 14,
        "level": 5,
        "element": "Corrupted",
        "stats": {
            "hp": 700,
            "atk": 320,
            "def": 420,
        },
        "discovery_text": (
            "You almost miss it. The dream is faint, fraying at the edges..."
        ),
        "defeat_text": (
            "The dream thins until it can no longer hold shape..."
        ),
        "fail_text": (
            "You remain as the dream fades further, piece by piece..."
        ),
        "reward": {
            "seed_type": "FORGOTTEN_DREAM_SEED",
            "bloomshards": "REPLACE_ME_FORGOTTEN_DREAM_BLOOMSHARDS",
            "crate_id": "FORGOTTEN_DREAM_CRATE_PLACEHOLDER",
            "crate_amount": 3,
            "divine_shard_chance_pct": 7,
        },
        "abilities": [
            {
                "key": "fading_touch",
                "name": "Fading Touch",
                "chance_pct": 10,
                "effect": {
                    "type": "true_damage_flat",
                    "amount": 500,
                },
                "flavor": "The dream brushes against you, and something vital slips away.",
            },
            {
                "key": "memory_erosion",
                "name": "Memory Erosion",
                "chance_pct": 10,
                "effect": {
                    "type": "multiattack_next_turns",
                    "hits_per_turn": 2,
                    "duration_turns": 2,
                },
                "flavor": "Details peel away from your thoughts...",
            },
            {
                "key": "final_dissolution",
                "name": "Final Dissolution",
                "chance_pct": 10,
                "effect": {
                    "type": "damage_percent_max_hp",
                    "pct": 10,
                },
                "flavor": "The dream collapses inward...",
            },
            {
                "key": "residual_echo",
                "name": "Residual Echo",
                "chance_pct": 70,
                "effect": {"type": "basic_attack"},
                "flavor": "An echo of something that once mattered brushes past you...",
            },
        ],
    },
    {
        "id": "lucid_dream",
        "name": "Lucid Dream",
        "difficulty": "normal",
        "image_url": "https://i.imgur.com/SuzuxlL.png",
        "min_player_level": 16,
        "weight": 12,
        "level": 6,
        "element": "Water",
        "stats": {
            "hp": 520,
            "atk": 360,
            "def": 410,
        },
        "discovery_text": (
            "Clarity snaps into place...\n"
            "The dream realizes you are aware and it does not like sharing control."
        ),
        "defeat_text": (
            "The world steadies. The dream loosens its grip..."
        ),
        "fail_text": (
            "You try to change everything at once. The dream pushes back harder..."
        ),
        "reward": {
            "seed_type": "LUCID_DREAM_SEED",
            "bloomshards": "REPLACE_ME_LUCID_DREAM_BLOOMSHARDS",
            "crate_id": "LUCID_DREAM_CRATE_PLACEHOLDER",
            "crate_amount": 3,
            "divine_shard_chance_pct": 7,
        },
        "abilities": [
            {
                "key": "reality_rejection",
                "name": "Reality Rejection",
                "chance_pct": 15,
                "effect": {
                    "type": "negate_last_player_damage_or_heal",
                },
                "flavor": "You reshape the world, only for it to snap back violently.",
            },
            {
                "key": "competing_control",
                "name": "Competing Control",
                "chance_pct": 20,
                "effect": {
                    "type": "damage_percent_max_hp",
                    "pct": 10,
                },
                "flavor": "Two wills collide, and the backlash tears through your mind.",
            },
            {
                "key": "lucid_dreams_song",
                "name": "Lucid Dreams",
                "chance_pct": 20,
                "effect": {
                    "type": "self_heal_percent_current_hp",
                    "pct": 5,
                },
                "flavor": "A familiar heartbreak melody echoes through the dream.",
            },
            {
                "key": "lucid_backlash",
                "name": "Lucid Backlash",
                "chance_pct": 45,
                "effect": {"type": "basic_attack"},
                "flavor": "The dream adapts instantly, striking when you hesitate.",
            },
        ],
    },
    {
        "id": "nightmare",
        "name": "Nightmare",
        "difficulty": "hard",
        "image_url": "https://i.imgur.com/81lRUpp.png",
        "min_player_level": 22,
        "weight": 9,
        "level": 7,
        "element": "Fire",
        "stats": {
            "hp": 1550,
            "atk": 980,
            "def": 920,
        },
        "discovery_text": (
            "The air turns heavy before you even see it...\n"
            "You have entered a Nightmare."
        ),
        "defeat_text": (
            "The flames gutter out, screams fading into silence..."
        ),
        "fail_text": (
            "The Nightmare closes in, fire and terror consuming everything you are..."
        ),
        "reward": {
            "seed_type": "NIGHTMARE_SEED",
            "bloomshards": "REPLACE_ME_NIGHTMARE_BLOOMSHARDS",
            "crate_id": "NIGHTMARE_CRATE_PLACEHOLDER",
            "crate_amount": 3,
            "divine_shard_chance_pct": 7,
        },
        "abilities": [
            {
                "key": "manifest_terror",
                "name": "Manifest Terror",
                "chance_pct": 10,
                "effect": {
                    "type": "permanent_burn_stack_percent_max_hp",
                    "pct": 2,
                    "max_stacks": 3,
                },
                "flavor": "The Nightmare pulls your deepest fear from your mind...",
            },
            {
                "key": "hellbound_grasp",
                "name": "Hellbound Grasp",
                "chance_pct": 10,
                "effect": {
                    "type": "stacking_player_accuracy_reduction",
                    "reduction_pct": 5,
                    "max_stacks": 6,
                },
                "flavor": "Burning hands claw up from beneath you...",
            },
            {
                "key": "endless_fall",
                "name": "Endless Fall",
                "chance_pct": 1,
                "effect": {
                    "type": "apply_status",
                    "status": "permanent_trip",
                },
                "flavor": "The ground vanishes. You fall through fire and shadow...",
            },
            {
                "key": "relentless_assault",
                "name": "Relentless Assault",
                "chance_pct": 79,
                "effect": {"type": "basic_attack"},
                "flavor": "The Nightmare does not pause, does not hesitate...",
            },
        ],
    },
    {
        "id": "pestilence",
        "name": "Pestilence",
        "difficulty": "hard",
        "image_url": "https://i.imgur.com/SeerZk8.png",
        "min_player_level": 24,
        "weight": 8,
        "level": 8,
        "element": "Nature",
        "stats": {
            "hp": 1200,
            "atk": 720,
            "def": 820,
        },
        "discovery_text": (
            "The first time you cough you brush it off...\n"
            "And then you hear the hooves of a horseman: Pestilence..."
        ),
        "defeat_text": (
            "For the first time in what feels like forever, you can draw a full breath..."
        ),
        "fail_text": (
            "This is not a quick, painless end..."
        ),
        "reward": {
            "seed_type": "PESTILENCE_SEED",
            "bloomshards": "REPLACE_ME_PESTILENCE_BLOOMSHARDS",
            "crate_id": "PESTILENCE_CRATE_PLACEHOLDER",
            "crate_amount": 1,
            "divine_shard_chance_pct": 10,
        },
        "abilities": [
            {
                "key": "diseasebringer",
                "name": "Diseasebringer",
                "chance_pct": 5,
                "effect": {
                    "type": "execute_percent_starting_hp",
                    "pct": 100,
                },
                "flavor": "Pestilence gets close and rests a hand on your shoulder...",
            },
            {
                "key": "coughing_fit",
                "name": "Coughing Fit",
                "chance_pct": 20,
                "effect": {
                    "type": "apply_status",
                    "status": "player_damage_taken_mult",
                    "value": 1.30,
                    "duration_turns": 2,
                },
                "flavor": "You start coughing uncontrollably...",
            },
            {
                "key": "fever",
                "name": "Fever",
                "chance_pct": 20,
                "effect": {
                    "type": "dot_percent_starting_hp",
                    "pct": 5,
                    "duration_turns": 3,
                },
                "flavor": "Your forehead is hot to the touch...",
            },
            {
                "key": "regular_attack",
                "name": "Regular Attack",
                "chance_pct": 50,
                "effect": {"type": "basic_attack"},
                "flavor": "The sickness itself lashes out.",
            },
        ],
    },
    {
        "id": "war",
        "name": "War",
        "difficulty": "hard",
        "image_url": "https://i.imgur.com/a9tVH19.png",
        "min_player_level": 30,
        "weight": 5,
        "level": 10,
        "element": "Fire",
        "stats": {
            "hp": 1850,
            "atk": 1450,
            "def": 1150,
        },
        "discovery_text": (
            "It starts with a deep echoing hum in the distance...\n"
            "And you know: War has arrived."
        ),
        "defeat_text": (
            "War slides off his horse, a laugh in his throat..."
        ),
        "fail_text": (
            "\"No foolish mortal could ever hope to defeat me...\""
        ),
        "reward": {
            "seed_type": "WAR_SEED",
            "bloomshards": "REPLACE_ME_WAR_BLOOMSHARDS",
            "crate_id": "WAR_CRATE_PLACEHOLDER",
            "crate_amount": 1,
            "divine_shard_chance_pct": 10,
        },
        "abilities": [
            {
                "key": "multiattack",
                "name": "Multiattack",
                "chance_pct": 30,
                "effect": {
                    "type": "multiattack",
                    "hits": 3,
                },
                "flavor": "War laughs in your face... he strikes out three times.",
            },
            {
                "key": "raise_army",
                "name": "Raise Army",
                "chance_pct": 20,
                "effect": {
                    "type": "self_shield",
                    "damage_reduction_pct": 25,
                    "duration_turns": 2,
                },
                "flavor": "War pulls a horn and calls for aid...",
            },
            {
                "key": "warcry",
                "name": "Warcry",
                "chance_pct": 10,
                "effect": {
                    "type": "stun_player",
                    "duration_turns": 1,
                },
                "flavor": "A loud bellowing scream - your limbs freeze...",
            },
            {
                "key": "regular_attack",
                "name": "Regular Attack",
                "chance_pct": 40,
                "effect": {"type": "basic_attack"},
                "flavor": "Steel meets flesh.",
            },
        ],
    },
    {
        "id": "famine",
        "name": "Famine",
        "difficulty": "hard",
        "image_url": "https://i.imgur.com/kf4JUWx.png",
        "min_player_level": 28,
        "weight": 7,
        "level": 9,
        "element": "Corrupted",
        "stats": {
            "hp": 1650,
            "atk": 1050,
            "def": 980,
        },
        "discovery_text": (
            "It starts simple, almost unnoticeable: plants are withering...\n"
            "This is Famine, and Famine has come to watch you starve."
        ),
        "defeat_text": (
            "You strike true and Famine is on the ground..."
        ),
        "fail_text": (
            "Hunger has consumed you..."
        ),
        "reward": {
            "seed_type": "FAMINE_SEED",
            "bloomshards": "REPLACE_ME_FAMINE_BLOOMSHARDS",
            "crate_id": "FAMINE_CRATE_PLACEHOLDER",
            "crate_amount": 1,
            "divine_shard_chance_pct": 10,
        },
        "abilities": [
            {
                "key": "entangle",
                "name": "Entangle",
                "chance_pct": 25,
                "effect": {
                    "type": "stun_player",
                    "duration_turns": 1,
                },
                "flavor": "Wilted crops gain life, wrapping around your limbs...",
            },
            {
                "key": "rotten_crops",
                "name": "Rotten Crops",
                "chance_pct": 10,
                "effect": {
                    "type": "apply_status",
                    "status": "player_damage_mult",
                    "value": 0.50,
                    "duration_turns": 3,
                },
                "flavor": "Plants rot rapidly... your weapon feels heavier.",
            },
            {
                "key": "hunger",
                "name": "Hunger",
                "chance_pct": 20,
                "effect": {
                    "type": "apply_status",
                    "status": "player_miss_chance",
                    "value_pct": 40,
                    "duration_turns": 2,
                },
                "flavor": "\"Your body is shutting down...\"",
            },
            {
                "key": "regular_attack",
                "name": "Regular Attack",
                "chance_pct": 45,
                "effect": {"type": "basic_attack"},
                "flavor": "The rot itself lashes out.",
            },
        ],
    },
]
