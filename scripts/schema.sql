-- Branching campaign content imported by the offline campaign editor.
CREATE TABLE IF NOT EXISTS campaign_content (
    campaign_key TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    start_node_key TEXT NOT NULL,
    campaign_json TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    created_by BIGINT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS player_campaigns (
    user_id BIGINT NOT NULL,
    campaign_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    current_node_key TEXT NOT NULL,
    history_json TEXT NOT NULL DEFAULT '[]',
    choices_json TEXT NOT NULL DEFAULT '{}',
    unlocks_json TEXT NOT NULL DEFAULT '[]',
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    PRIMARY KEY (user_id, campaign_key)
);

CREATE TABLE IF NOT EXISTS player_reputation (
    user_id BIGINT NOT NULL,
    reputation_key TEXT NOT NULL,
    points INTEGER NOT NULL DEFAULT 0,
    rank INTEGER NOT NULL DEFAULT 0,
    tier_key TEXT NOT NULL DEFAULT 'neutral',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, reputation_key)
);

-- Shared faction catalog. Historical reputation keys without a catalog row
-- remain valid and use the application's default standing ladder.
CREATE TABLE IF NOT EXISTS faction_definitions (
    faction_key TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    emoji TEXT NOT NULL DEFAULT '',
    tiers_json TEXT NOT NULL,
    allegiance_group TEXT NOT NULL DEFAULT '',
    rivals_json TEXT NOT NULL DEFAULT '[]',
    allies_json TEXT NOT NULL DEFAULT '[]',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_by BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS player_faction_memberships (
    user_id BIGINT NOT NULL,
    faction_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'none',
    joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, faction_key)
);

CREATE TABLE IF NOT EXISTS faction_reputation_events (
    event_id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    faction_key TEXT NOT NULL,
    delta INTEGER NOT NULL,
    old_points INTEGER NOT NULL,
    new_points INTEGER NOT NULL,
    old_rank INTEGER NOT NULL,
    new_rank INTEGER NOT NULL,
    source TEXT NOT NULL DEFAULT 'system',
    event_key TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS faction_reputation_events_idempotency_idx
ON faction_reputation_events (user_id, faction_key, event_key)
WHERE event_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS faction_membership_events (
    event_id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    faction_key TEXT NOT NULL,
    old_status TEXT NOT NULL,
    new_status TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'system',
    event_key TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS faction_membership_events_idempotency_idx
ON faction_membership_events (user_id, faction_key, event_key)
WHERE event_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS faction_access_rules (
    scope TEXT NOT NULL,
    target_key TEXT NOT NULL,
    subtarget_key TEXT NOT NULL DEFAULT '',
    conditions_json TEXT NOT NULL DEFAULT '[]',
    failure_message TEXT NOT NULL DEFAULT '',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_by BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (scope, target_key, subtarget_key)
);

CREATE TABLE IF NOT EXISTS faction_system_migrations (
    migration_key TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Durable story facts (betrayals, oaths, unlocked systems) are deliberately
-- independent of reversible faction standing.
CREATE TABLE IF NOT EXISTS player_system_unlocks (
    user_id BIGINT NOT NULL,
    unlock_key TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'system',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    unlocked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, unlock_key)
);

CREATE TABLE IF NOT EXISTS content_monsters (
    monster_key TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    tier INTEGER NOT NULL DEFAULT 1,
    hp INTEGER NOT NULL,
    attack INTEGER NOT NULL,
    defense INTEGER NOT NULL,
    element TEXT NOT NULL DEFAULT 'Nature',
    url TEXT NOT NULL DEFAULT '',
    tags_json TEXT NOT NULL DEFAULT '[]',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
