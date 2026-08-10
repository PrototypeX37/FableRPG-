# The Dreambound Spring (Phase 2)

This module implements **The Dreambound Spring** seasonal gardening system for the **Elysian Garden**.

## Configure First

Edit `/Users/fannygilles/Desktop/EoO/Code/EoOBeta/cogs/dreambound_spring/data.py`:

- `DEFAULT_SEASON_ID`: active season key.
- `ASSETS["backgrounds"]`: set level image URLs.
- `ASSETS["flowers"][...]["stages"]`: replace planted/bloomed placeholders.
- `ASSETS["flowers"][...]["crate_id"]`: set your real crate IDs.
- `SEEDS`: add/remove seed types and map to flower types.
- `SEEDS[*]["growth_seconds"]`: per-seed growth time in seconds.
- `CONFIG`: tune fallback growth timing, reductions, tonic cost, upgrade costs, max level.
- `CONFIG["GARDEN_CANVAS_SIZE"]`: base garden image size.
- `CONFIG["TARGET_FLOWER_SIZE"]`: preferred flower sprite size before fitting.
- `CONFIG["CELL_PADDING_RATIO"]`: per-cell padding for centered fit.
- `SHOP_ITEMS`: item IDs/costs/effects are data-driven.
- `CRATE_PROFILE_COLUMNS`: map crate IDs to profile crate columns.

Edit `/Users/fannygilles/Desktop/EoO/Code/EoOBeta/cogs/dreambound_spring/pve_data.py`:

- `SPRING_PVE_CONFIG`: spring PvE cooldown/search/turn pacing.
- `SPRING_PVE_MONSTERS`: 10 seasonal monsters, text, abilities, and reward placeholders.
- `SPRING_PVE_REWARD_DEFAULTS`: fallback rewards when placeholders are not set.

## Fixed Garden Layouts

Hard-coded by level from `ASSETS["backgrounds"]`:

- Level 0: 5x3 (15 slots)
- Level 1: 5x4 (20 slots)
- Level 2: 6x4 (24 slots, max)

No other expansions are supported.

## Growth Model

Per slot:

- `PLANTED -> BLOOMED`
- Time-based with timestamps and `boost_seconds`
- No background task required

Configured growths:

- Normal dream seeds: 8h
- Special dream seeds: 12h
- Horsemen seeds: 15h

## Commands

- `$springhelp` (or `$garden help`, `$springshop help`) to view event commands
- `$garden` (or `$garden show`): show garden state, level, grid, slots, seeds/items/currency
- `$garden plant <slot> <seed_name|seed_id>`
- `$garden plantall` (fills empty slots using lowest seeds first)
- `$garden remove <slot>` (consumes **Shears**, no seed refund)
- `$garden harvest <slot>` (BLOOMED only; grants 1 crate)
- `$garden harvestall` (harvests every BLOOMED flower in unlocked slots)
- `$garden upgrade` (uses Bloomshards; max level 2)
- `$garden snapshot` (stores seasonal JSON snapshot)
- `$garden snapshot latest` (shows latest saved snapshot)
- `$garden snapshot <id>` (shows a specific saved snapshot)
- `$springpve` (seasonal PvE with monster-based flower/seed drops)
- `$springshop`
- `$springshop buy <item> [amount]`
- `$springshop use <item> <slot>`

GMs also get:

- `$springadmin` (alias: `$springgm`)
- `$springspawn <hp> [rarity]` (GM-only Death raid spawn via `newraids`)
- `$springadmin grantshards <@user> <amount>`
- `$springadmin grantseed <@user> <seed_type> <amount>`
- `$springadmin growall <@user>` (instantly blooms all planted flowers for trials)
- `$springadmin setphase <1-3>`
- `$springadmin setenabled <true|false>`

## Phase 1 Hooks

The cog exposes helpers for other systems:

- `add_bloomshards(user_id, amount, season_id=None, conn=None)`
- `add_seeds(user_id, seed_type, amount, season_id=None, conn=None)`
- `grant_phase1_rewards(user_id, bloomshards=0, seeds=None, season_id=None)`

Use these from another cog to award event resources.

## DB Setup

Tables are auto-created on cog startup and also provided in:

- `/Users/fannygilles/Desktop/EoO/Code/EoOBeta/scripts/migrations/2026_02_11_dreambound_spring.sql`

## Notes on Visual Rendering

Current output is text-grid + render payload metadata.

The cog already computes dynamic render payload from:

- `ASSETS["backgrounds"][garden_level]`
- `ASSETS["flowers"][flower_type]["stages"][state]`
- dynamic row/col from `grid_size`
- centered `sprite_rect` fit per slot based on `GARDEN_CANVAS_SIZE` and current grid

You can plug this into PIL rendering later without changing command logic.
