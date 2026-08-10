"""Data and configuration for The Dreambound Spring event."""
from __future__ import annotations

EVENT_NAME = "The Dreambound Spring"
EVENT_CURRENCY = "Bloomshards"
GARDEN_NAME = "Elysian Garden"
DEFAULT_SEASON_ID = "dreambound_spring_2026"
DEFAULT_PHASE = 2

ASSETS = {
    "backgrounds": {
        0: {
            "name": "Base Garden",
            "grid_size": (5, 3),
            "image": "https://i.imgur.com/OyNZRSN.png"
        },
        1: {
            "name": "Expanded Garden",
            "grid_size": (5, 4),
            "image": "https://i.imgur.com/WpCsCYt.png"
        },
        2: {
            "name": "Grand Garden",
            "grid_size": (6, 4),
            "image": "https://i.imgur.com/Zsz8bwR.png"
        }
    },

    "flowers": {
        "GOOD_DREAM_FLOWER": {
            "display_name": "Good Dream",
            "crate_id": "GOOD_DREAM_CRATE_PLACEHOLDER",
            "stages": {
                "planted": "https://i.imgur.com/pWmAXzl.png",
                "bloomed": "https://i.imgur.com/klQvUwe.png"
            }
        },
        "BAD_DREAM_FLOWER": {
            "display_name": "Bad Dream",
            "crate_id": "BAD_DREAM_CRATE_PLACEHOLDER",
            "stages": {
                "planted": "https://i.imgur.com/pWmAXzl.png",
                "bloomed": "https://i.imgur.com/MOxDuJd.png"
            }
        },
        "NEUTRAL_DREAM_FLOWER": {
            "display_name": "Neutral Dream",
            "crate_id": "NEUTRAL_DREAM_CRATE_PLACEHOLDER",
            "stages": {
                "planted": "https://i.imgur.com/pWmAXzl.png",
                "bloomed": "https://i.imgur.com/JhpGuhE.png"
            }
        },
        "GREAT_DREAM_FLOWER": {
            "display_name": "Great Dream",
            "crate_id": "GREAT_DREAM_CRATE_PLACEHOLDER",
            "stages": {
                "planted": "https://i.imgur.com/cqzJtBO.png",
                "bloomed": "https://i.imgur.com/6wvbFau.png"
            }
        },
        "NIGHTMARE_FLOWER": {
            "display_name": "Nightmare",
            "crate_id": "NIGHTMARE_CRATE_PLACEHOLDER",
            "stages": {
                "planted": "https://i.imgur.com/cqzJtBO.png",
                "bloomed": "https://i.imgur.com/ffMvAsr.png"
            }
        },
        "FORGOTTEN_DREAM_FLOWER": {
            "display_name": "Forgotten Dream",
            "crate_id": "FORGOTTEN_DREAM_CRATE_PLACEHOLDER",
            "stages": {
                "planted": "https://i.imgur.com/cqzJtBO.png",
                "bloomed": "https://i.imgur.com/PfT8T2Z.png"
            }
        },
        "LUCID_DREAM_FLOWER": {
            "display_name": "Lucid Dream",
            "crate_id": "LUCID_DREAM_CRATE_PLACEHOLDER",
            "stages": {
                "planted": "https://i.imgur.com/cqzJtBO.png",
                "bloomed": "https://i.imgur.com/FS2eSzc.png"
            }
        },
        "PESTILENCE_FLOWER": {
            "display_name": "Pestilence",
            "crate_id": "PESTILENCE_CRATE_PLACEHOLDER",
            "stages": {
                "planted": "https://i.imgur.com/zNbS1Xb.png",
                "bloomed": "https://i.imgur.com/dypzYNy.png"
            }
        },
        "WAR_FLOWER": {
            "display_name": "War",
            "crate_id": "WAR_CRATE_PLACEHOLDER",
            "stages": {
                "planted": "https://i.imgur.com/zNbS1Xb.png",
                "bloomed": "https://i.imgur.com/vustQ0z.png"
            }
        },
        "FAMINE_FLOWER": {
            "display_name": "Famine",
            "crate_id": "FAMINE_CRATE_PLACEHOLDER",
            "stages": {
                "planted": "https://i.imgur.com/zNbS1Xb.png",
                "bloomed": "https://i.imgur.com/FYcz22B.png"
            }
        }
    }
}

SEEDS = {
    "GOOD_DREAM_SEED": {
        "flower_type": "GOOD_DREAM_FLOWER",
        "display_name": "Good Dream Seed",
        "group": "normal",
        "growth_seconds": 8 * 3600,
    },
    "BAD_DREAM_SEED": {
        "flower_type": "BAD_DREAM_FLOWER",
        "display_name": "Bad Dream Seed",
        "group": "normal",
        "growth_seconds": 8 * 3600,
    },
    "NEUTRAL_DREAM_SEED": {
        "flower_type": "NEUTRAL_DREAM_FLOWER",
        "display_name": "Neutral Dream Seed",
        "group": "normal",
        "growth_seconds": 8 * 3600,
    },
    "GREAT_DREAM_SEED": {
        "flower_type": "GREAT_DREAM_FLOWER",
        "display_name": "Great Dream Seed",
        "group": "special",
        "growth_seconds": 12 * 3600,
    },
    "NIGHTMARE_SEED": {
        "flower_type": "NIGHTMARE_FLOWER",
        "display_name": "Nightmare Seed",
        "group": "special",
        "growth_seconds": 12 * 3600,
    },
    "FORGOTTEN_DREAM_SEED": {
        "flower_type": "FORGOTTEN_DREAM_FLOWER",
        "display_name": "Forgotten Dream Seed",
        "group": "special",
        "growth_seconds": 12 * 3600,
    },
    "LUCID_DREAM_SEED": {
        "flower_type": "LUCID_DREAM_FLOWER",
        "display_name": "Lucid Dream Seed",
        "group": "special",
        "growth_seconds": 12 * 3600,
    },
    "PESTILENCE_SEED": {
        "flower_type": "PESTILENCE_FLOWER",
        "display_name": "Pestilence Seed",
        "group": "horsemen",
        "growth_seconds": 15 * 3600,
    },
    "WAR_SEED": {
        "flower_type": "WAR_FLOWER",
        "display_name": "War Seed",
        "group": "horsemen",
        "growth_seconds": 15 * 3600,
    },
    "FAMINE_SEED": {
        "flower_type": "FAMINE_FLOWER",
        "display_name": "Famine Seed",
        "group": "horsemen",
        "growth_seconds": 15 * 3600,
    },
}

CONFIG = {
    "BASE_GROWTH_SECONDS": 28800,  # fallback only (8h)
    "FERTILIZER_REDUCTION_SECONDS": 21600,  # 6h
    "WATER_REDUCTION_SECONDS": 10800,  # 3h
    "BLOOM_TONIC_COST": 500,
    "GARDEN_CANVAS_SIZE": (1563, 938),
    "TARGET_FLOWER_SIZE": (300, 300),
    "CELL_PADDING_RATIO": 0.06,
    "UPGRADE_COSTS": {
        0: 500,   # Level 0 -> 1
        1: 900    # Level 1 -> 2
    },
    "MAX_GARDEN_LEVEL": 2
}

# One-time garden completion rewards.
# Keys:
# - row: any newly completed row
# - column: any newly completed column
# - full_by_grid: full unlocked garden, keyed by "<cols>x<rows>"
GARDEN_MILESTONE_REWARDS = {
    "row": {
        "bloomshards": 0,
        "crates": {"crates_divine": 1},
    },
    "column": {
        "bloomshards": 0,
        "crates": {"crates_materials": 1},
    },
    "full_by_grid": {
        "5x3": {
            "bloomshards": 25,
            "crates": {"crates_fortune": 1},
        },
        "5x4": {
            "bloomshards": 25,
            "crates": {"crates_fortune": 2},
        },
        "6x4": {
            "bloomshards": 25,
            "crates": {"crates_fortune": 1, "crates_divine": 1},
        },
    },
}

# Item IDs and costs are data-driven for shop logic.
SHOP_ITEMS = {
    "fertilizer": {
        "item_id": "FERTILIZER",
        "display_name": "Fertilizer",
        "cost": 120,
        "effect": "reduce_time",
        "seconds": CONFIG["FERTILIZER_REDUCTION_SECONDS"],
        "requires_slot": True,
    },
    "enchanted_water": {
        "item_id": "ENCHANTED_WATER",
        "display_name": "Enchanted Water",
        "cost": 80,
        "effect": "reduce_time",
        "seconds": CONFIG["WATER_REDUCTION_SECONDS"],
        "requires_slot": True,
    },
    "bloom_tonic": {
        "item_id": "BLOOM_TONIC",
        "display_name": "Bloom Tonic",
        "cost": CONFIG["BLOOM_TONIC_COST"],
        "effect": "instant_bloom",
        "requires_slot": True,
    },
    "shears": {
        "item_id": "SHEARS",
        "display_name": "Shears",
        "cost": 50,
        "effect": "clear_slot",
        "requires_slot": True,
    },
    "divine_shard_random": {
        "item_id": "DIVINE_SHARD_RANDOM",
        "display_name": "Random Divine Shard",
        "cost": 9000,
        "effect": "grant_random_divine_shard",
        "requires_slot": False,
    },
    "divine_shard_chosen": {
        "item_id": "DIVINE_SHARD_CHOSEN",
        "display_name": "Chosen Divine Shard",
        "cost": 12000,
        "effect": "grant_chosen_divine_shard",
        "requires_slot": False,
    },
}

# Maps crate placeholders to existing profile crate columns. Replace safely later.
CRATE_PROFILE_COLUMNS = {
    "GOOD_DREAM_CRATE_PLACEHOLDER": "crates_uncommon",
    "BAD_DREAM_CRATE_PLACEHOLDER": "crates_uncommon",
    "NEUTRAL_DREAM_CRATE_PLACEHOLDER": "crates_rare",
    "GREAT_DREAM_CRATE_PLACEHOLDER": "crates_magic",
    "NIGHTMARE_CRATE_PLACEHOLDER": "crates_magic",
    "FORGOTTEN_DREAM_CRATE_PLACEHOLDER": "crates_magic",
    "LUCID_DREAM_CRATE_PLACEHOLDER": "crates_magic",
    "PESTILENCE_CRATE_PLACEHOLDER": "crates_legendary",
    "WAR_CRATE_PLACEHOLDER": "crates_legendary",
    "FAMINE_CRATE_PLACEHOLDER": "crates_legendary",
}

ALLOWED_PROFILE_CRATE_COLUMNS = {
    "crates_common",
    "crates_uncommon",
    "crates_rare",
    "crates_magic",
    "crates_legendary",
    "crates_mystery",
    "crates_fortune",
    "crates_divine",
    "crates_materials",
}
