"""Portable campaign package validation and conversion helpers."""

from __future__ import annotations

import copy
import json
import math
import re
from collections.abc import Mapping
from datetime import datetime, timezone


SCHEMA_NAME = "fablereborn.campaign-package"
SCHEMA_VERSION = 2
SUPPORTED_SCHEMA_VERSIONS = {1, 2}

NODE_TYPES = {"quest", "choice", "dialogue", "scene", "travel", "scenario", "ending"}
CHOICE_NODE_TYPES = {"choice", "dialogue"}
OBJECTIVE_SOURCES = {
    "none",
    "pve",
    "adventure",
    "battletower",
    "dragonparty",
    "cbt",
    "raidbattle",
    "jurytower",
    "scripted",
    "frontier_boss",
    "divine_victory",
    "omnithrone_phase",
    "omnithrone_completion",
}
OBJECTIVE_MODES = {"progress", "key_item"}
COLLECTION_REWARD_TYPES = {
    "title",
    "cosmetic",
    "mount",
    "home_upgrade",
    "specialization",
    "faction_service",
    "system_unlock",
}
REWARD_TYPES = {"none", "money", "crate", "item", "egg", "bundle"} | COLLECTION_REWARD_TYPES
TURNIN_TYPES = {"progress", "key_item", "crate", "egg", "money"}
CONDITION_TYPES = {
    "quest_completed",
    "campaign_completed",
    "campaign_choice",
    "reputation",
    "faction_standing",
    "faction_membership",
    "level",
    "class",
    "god",
    "race",
    "money",
    "guild_member",
    "item_owned",
    "item_equipped",
    "pet_owned",
    "pet_equipped",
    "badge",
    "unlock",
    "system_unlock",
    "god_pet_lock",
    "ascension_mantle",
    "frontier_boss_regions",
    "state",
    "story_state",
    "relationship",
    "location_state",
    "war_state",
}
CONDITION_OPERATORS = {
    "=",
    "==",
    "is",
    "!=",
    "is_not",
    ">",
    ">=",
    "<",
    "<=",
    "contains",
    "not_contains",
    "in",
    "not_in",
    "exists",
    "not_exists",
}
FACTION_STANDING_CONDITION_TYPES = {"reputation", "faction_standing"}
FACTION_STANDING_FIELDS = {"points", "rank", "tier"}
FACTION_STANDING_OPERATORS = CONDITION_OPERATORS - {"contains"}
FACTION_EFFECT_TYPES = {
    "reputation",
    "faction_standing",
    "faction_membership",
}
STORY_STATE_CONDITION_TYPES = {"state", "story_state"}
STORY_STATE_EFFECT_TYPES = {
    "state_set",
    "state_increment",
    "state_add",
    "state_remove",
    "state_delete",
}
MODEL_CONDITION_TYPES = {"relationship", "location_state", "war_state"}
MODEL_EFFECT_TYPES = {
    "relationship_change",
    "relationship_set",
    "relationship_flag_add",
    "relationship_flag_remove",
    "location_set",
    "war_set",
    "war_increment",
}
COLLECTION_EFFECT_TYPES = {"fable_reward", "collection_reward"}
CONDITION_GROUP_KEYS = {"all", "any", "not"}
FACTION_MEMBERSHIP_OPERATORS = {"=", "==", "is", "!=", "is_not"}
FACTION_STANDING_EFFECT_NUMERIC_FIELDS = (
    "points",
    "delta",
    "rank_delta",
    "set_rank",
)
ENCOUNTER_KINDS = {
    "none",
    "pve",
    "battle_tower",
    "dragon_party",
    "raid_battle",
    "jury_tower",
    "external_command",
}

ENDGAME_EVENT_TYPES = {
    "frontier_boss_clear",
    "divine_victory",
    "omnithrone_phase",
    "omnithrone_completion",
}
DIVINE_GODS = ("Elysia", "Sepulchure", "Drakath")


class CampaignPackageError(ValueError):
    """Raised when a campaign package cannot be safely imported."""

    def __init__(self, errors: list[str]):
        super().__init__("\n".join(errors))
        self.errors = errors


def normalize_key(value: object) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower())
    return cleaned.strip("_")


def normalize_system_unlock_key(value: object) -> str:
    """Return the canonical storage form for durable, cross-system unlocks."""
    return normalize_key(value)


def normalize_state_key(value: object) -> str:
    """Normalize a scoped V2 story-state key while preserving dot namespaces."""
    return ".".join(
        segment
        for segment in (normalize_key(part) for part in str(value or "").split("."))
        if segment
    )


def _required_event_text(payload: Mapping, key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ValueError(f"Endgame event payload needs `{key}`.")
    return value


def _positive_event_int(payload: Mapping, key: str, default: int = 1) -> int:
    raw = payload.get(key, default)
    try:
        return max(1, int(raw))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Endgame event payload `{key}` must be a positive number.") from exc


def normalize_endgame_event(event_type: object, payload: object) -> dict:
    """Validate an endgame dispatch and derive its quest/unlock projection.

    The returned ``event_id`` is stable and is used to make quest progress
    idempotent. Callers should dispatch only after the underlying battle or
    phase transaction has committed.
    """
    event_key = normalize_key(event_type)
    if event_key not in ENDGAME_EVENT_TYPES:
        raise ValueError(f"Unsupported endgame event `{event_key or event_type}`.")
    if not isinstance(payload, Mapping):
        raise ValueError("Endgame event payload must be an object.")

    party_size = _positive_event_int(payload, "party_size", 1)
    if event_key == "frontier_boss_clear":
        battle_id = _required_event_text(payload, "battle_id")
        region_id = normalize_key(_required_event_text(payload, "region_id"))
        boss_name = _required_event_text(payload, "boss_name")
        absolute_week = payload.get("absolute_week")
        return {
            "event_type": event_key,
            "source": "frontier_boss",
            "candidate_names": (boss_name, region_id, "frontier_boss"),
            "metadata": {
                "event_id": f"frontier_boss:{battle_id}",
                "battle_id": battle_id,
                "region_id": region_id,
                "target_key": region_id,
                "absolute_week": absolute_week,
                "party_size": party_size,
            },
            "unlock_keys": (f"frontier_boss_{region_id}",),
        }

    if event_key == "divine_victory":
        battle_id = _required_event_text(payload, "battle_id")
        requested_god = _required_event_text(payload, "god_name")
        god_name = next(
            (name for name in DIVINE_GODS if name.casefold() == requested_god.casefold()),
            None,
        )
        if god_name is None:
            raise ValueError("Divine victory must name Elysia, Sepulchure, or Drakath.")
        tier = _positive_event_int(payload, "tier", 11)
        if tier != 11:
            raise ValueError("Divine victory events only accept Tier 11 encounters.")
        god_key = normalize_key(god_name)
        return {
            "event_type": event_key,
            "source": "divine_victory",
            "candidate_names": (god_name, god_key, "tier_11"),
            "metadata": {
                "event_id": f"divine_victory:{battle_id}",
                "battle_id": battle_id,
                "god_name": god_name,
                "target_key": god_key,
                "tier": 11,
                "party_size": party_size,
            },
            "unlock_keys": (f"divine_victory_{god_key}",),
        }

    attempt_id = _required_event_text(payload, "attempt_id")
    if event_key == "omnithrone_phase":
        phase_key = normalize_key(_required_event_text(payload, "phase_key"))
        phase_number = _positive_event_int(payload, "phase_number")
        return {
            "event_type": event_key,
            "source": "omnithrone_phase",
            "candidate_names": (phase_key, f"phase_{phase_number}", "omnithrone"),
            "metadata": {
                "event_id": f"omnithrone:{attempt_id}:phase:{phase_key}",
                "attempt_id": attempt_id,
                "phase_key": phase_key,
                "phase_number": phase_number,
                "target_key": phase_key,
                "party_size": party_size,
            },
            "unlock_keys": (f"omnithrone_phase_{phase_key}",),
        }

    outcome = normalize_key(_required_event_text(payload, "outcome"))
    if outcome != "sealed":
        raise ValueError("Omnithrone completion must record the `sealed` outcome.")
    return {
        "event_type": event_key,
        "source": "omnithrone_completion",
        "candidate_names": ("sealed", "complete", "omnithrone"),
        "metadata": {
            "event_id": f"omnithrone:{attempt_id}:completion",
            "attempt_id": attempt_id,
            "outcome": "sealed",
            "target_key": "sealed",
            "party_size": party_size,
        },
        "unlock_keys": ("omnithrone_sealed",),
    }


def validate_builtin_package(raw: object) -> dict:
    """Validate a bundled package using the monotonic version contract.

    Bundled JSON must include a stable ``package_key`` and a positive integer
    ``content_version``. A running bot installs a package once, skips the same
    or an older version, and installs a higher version as an explicit content
    migration. This lets GMs edit installed content safely between releases.
    """
    package = validate_package(raw)
    errors: list[str] = []
    package_key = normalize_key(package.get("package_key"))
    if not package_key:
        errors.append("Bundled campaign packages need a stable `package_key`.")
    try:
        content_version = int(package.get("content_version"))
    except (TypeError, ValueError):
        content_version = 0
    if content_version < 1:
        errors.append("Bundled campaign packages need `content_version` >= 1.")
    if errors:
        raise CampaignPackageError(errors)
    package["package_key"] = package_key
    package["content_version"] = content_version
    return package


def builtin_install_decision(
    installed_version: int | None,
    bundled_version: int,
    *,
    has_unmanaged_conflicts: bool = False,
) -> str:
    """Return ``install``, ``upgrade``, ``skip``, or ``conflict``."""
    bundled_version = int(bundled_version)
    if installed_version is None:
        return "conflict" if has_unmanaged_conflicts else "install"
    return "upgrade" if bundled_version > int(installed_version) else "skip"


def _as_dict(value: object) -> dict:
    return copy.deepcopy(value) if isinstance(value, dict) else {}


def _as_list(value: object) -> list:
    return copy.deepcopy(value) if isinstance(value, list) else []


def _positive_int(value: object, default: int = 1) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


def new_package() -> dict:
    return {
        "schema": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "campaigns": [],
        "standalone_quests": [],
        "cutscenes": [],
        "monsters": [],
        "reference": build_reference_catalog([]),
    }


def build_reference_catalog(monsters: list[dict]) -> dict:
    monster_names = sorted(
        {
            str(monster.get("name") or "").strip()
            for monster in monsters
            if isinstance(monster, dict) and str(monster.get("name") or "").strip()
        },
        key=str.casefold,
    )
    return {
        "node_types": sorted(NODE_TYPES),
        "objective_sources": sorted(OBJECTIVE_SOURCES),
        "objective_modes": sorted(OBJECTIVE_MODES),
        "turnin_types": sorted(TURNIN_TYPES),
        "reward_types": sorted(REWARD_TYPES),
        "condition_types": sorted(CONDITION_TYPES),
        "condition_operators": sorted(CONDITION_OPERATORS),
        "faction_standing_fields": sorted(FACTION_STANDING_FIELDS),
        "faction_standing_operators": sorted(FACTION_STANDING_OPERATORS),
        "faction_membership_operators": sorted(FACTION_MEMBERSHIP_OPERATORS),
        "faction_effect_types": sorted(FACTION_EFFECT_TYPES),
        "story_state_effect_types": sorted(STORY_STATE_EFFECT_TYPES),
        "story_model_effect_types": sorted(MODEL_EFFECT_TYPES),
        "collection_effect_types": sorted(COLLECTION_EFFECT_TYPES),
        "faction_standing_effect_numeric_fields": list(
            FACTION_STANDING_EFFECT_NUMERIC_FIELDS
        ),
        "encounter_kinds": sorted(ENCOUNTER_KINDS),
        "monster_names": monster_names,
        "notes": {
            "launch_command": (
                "Discord command shown to players for an encounter. Existing battle "
                "commands enforce their own lobby and party rules."
            ),
            "battle_settings": (
                "Stored with the campaign now and reserved for a generic campaign "
                "battle runner."
            ),
            "builtin_versioning": (
                "Files in cogs/quests/data/builtin_campaigns need package_key and "
                "content_version >= 1. Equal or older versions are skipped; only a "
                "higher content_version replaces an installed package."
            ),
            "distinct_objectives": (
                "Set objective.distinct_targets=true to count each event target once."
            ),
            "condition_groups": (
                "V2 requirements may use nested {all:[...]}, {any:[...]}, and "
                "{not:{...}} expressions. V1 condition arrays remain implicit all groups."
            ),
            "story_state": (
                "Use state/story_state conditions with dotted keys and state_set, "
                "state_increment, state_add, state_remove, or state_delete effects."
            ),
        },
    }


def normalize_package(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise CampaignPackageError(["The uploaded JSON must contain one object."])

    package = copy.deepcopy(raw)
    package.setdefault("schema", SCHEMA_NAME)
    package.setdefault("schema_version", SCHEMA_VERSION)
    package["campaigns"] = _as_list(package.get("campaigns"))
    package["standalone_quests"] = _as_list(package.get("standalone_quests"))
    package["cutscenes"] = _as_list(package.get("cutscenes"))
    package["monsters"] = _as_list(package.get("monsters"))
    package["reference"] = _as_dict(package.get("reference"))
    package.setdefault("exported_at", datetime.now(timezone.utc).isoformat())

    for campaign in package["campaigns"]:
        if not isinstance(campaign, dict):
            continue
        campaign["key"] = normalize_key(campaign.get("key"))
        campaign["title"] = str(campaign.get("title") or campaign["key"].replace("_", " ").title())
        campaign["description"] = str(campaign.get("description") or "")
        campaign["start_node"] = normalize_key(campaign.get("start_node"))
        campaign["is_active"] = bool(campaign.get("is_active", False))
        campaign["nodes"] = _as_list(campaign.get("nodes"))
        campaign["requirements"] = _normalize_conditions(campaign.get("requirements"))

        for node in campaign["nodes"]:
            if not isinstance(node, dict):
                continue
            node["id"] = normalize_key(node.get("id"))
            node["type"] = str(node.get("type") or "quest").strip().lower()
            node["title"] = str(node.get("title") or node["id"].replace("_", " ").title())
            node["description"] = str(node.get("description") or "")
            node["next"] = _normalize_edges(node.get("next"))
            node["options"] = _normalize_edges(node.get("options"))
            node["quest"] = _as_dict(node.get("quest"))
            node["cutscenes"] = _as_dict(node.get("cutscenes"))
            node["encounter"] = _as_dict(node.get("encounter"))
            node["scenario"] = _normalize_scenario(node.get("scenario"), node["id"])
            node["location_key"] = normalize_key(node.get("location_key"))
            node["unlocks"] = _as_list(node.get("unlocks"))
            node["effects"] = _normalize_effects(node.get("effects"))
            node["requirements"] = _normalize_conditions(node.get("requirements"))
            node["quest"]["requirements"] = _normalize_conditions(
                node["quest"].get("requirements")
            )

    for quest in package["standalone_quests"]:
        if not isinstance(quest, dict):
            continue
        quest["access"] = _as_dict(quest.get("access"))
        quest["access"]["conditions"] = _normalize_conditions(
            quest["access"].get("conditions")
        )

    for monster in package["monsters"]:
        if not isinstance(monster, dict):
            continue
        monster["key"] = normalize_key(monster.get("key") or monster.get("name"))
        monster["name"] = str(monster.get("name") or "").strip()
        monster["tier"] = _positive_int(monster.get("tier"), 1)
        monster["hp"] = _positive_int(monster.get("hp"), 100)
        monster["attack"] = _positive_int(monster.get("attack"), 10)
        monster["defense"] = max(0, int(monster.get("defense") or 0))
        monster["element"] = str(monster.get("element") or "Nature").strip()
        monster["url"] = str(monster.get("url") or "").strip()
        monster["tags"] = [
            str(tag).strip()
            for tag in _as_list(monster.get("tags"))
            if str(tag).strip()
        ]

    return package


def _normalize_edges(raw: object) -> list[dict]:
    if isinstance(raw, str):
        raw = [{"target": raw}]
    edges = []
    for edge in _as_list(raw):
        if isinstance(edge, str):
            edge = {"target": edge}
        if not isinstance(edge, dict):
            continue
        target = normalize_key(edge.get("target"))
        edges.append(
            {
                "label": str(edge.get("label") or "Continue").strip(),
                "target": target,
                "description": str(edge.get("description") or "").strip(),
                "effects": _normalize_effects(edge.get("effects")),
                "unlocks": _as_list(edge.get("unlocks")),
                "conditions": _normalize_conditions(edge.get("conditions")),
            }
        )
    return edges


def _normalize_scenario(raw: object, node_id: str) -> dict:
    scenario = _as_dict(raw)
    if not scenario:
        return {}
    scenario["key"] = normalize_key(scenario.get("key") or node_id)
    scenario["start_stage"] = normalize_key(scenario.get("start_stage"))
    scenario["stages"] = _as_list(scenario.get("stages"))
    scenario["outcomes"] = _as_list(scenario.get("outcomes"))
    for stage in scenario["stages"]:
        if not isinstance(stage, dict):
            continue
        stage["id"] = normalize_key(stage.get("id"))
        stage["title"] = str(stage.get("title") or stage["id"].replace("_", " ").title())
        stage["description"] = str(stage.get("description") or "")
        stage["launch_command"] = str(stage.get("launch_command") or "").strip()
        stage["actions"] = _as_list(stage.get("actions"))
        for action in stage["actions"]:
            if not isinstance(action, dict):
                continue
            action["label"] = str(action.get("label") or "Continue").strip()
            action["description"] = str(action.get("description") or "").strip()
            action["next_stage"] = normalize_key(action.get("next_stage"))
            action["outcome"] = normalize_key(action.get("outcome"))
            action["conditions"] = _normalize_conditions(action.get("conditions"))
            action["effects"] = _normalize_effects(action.get("effects"))
            action["unlocks"] = _as_list(action.get("unlocks"))
    for outcome in scenario["outcomes"]:
        if not isinstance(outcome, dict):
            continue
        outcome["id"] = normalize_key(outcome.get("id"))
        outcome["title"] = str(outcome.get("title") or outcome["id"].replace("_", " ").title())
        outcome["description"] = str(outcome.get("description") or "")
        outcome["target"] = normalize_key(outcome.get("target"))
        outcome["conditions"] = _normalize_conditions(outcome.get("conditions"))
        outcome["effects"] = _normalize_effects(outcome.get("effects"))
        outcome["unlocks"] = _as_list(outcome.get("unlocks"))
    if not scenario["start_stage"] and scenario["stages"]:
        first_stage = scenario["stages"][0]
        if isinstance(first_stage, dict):
            scenario["start_stage"] = first_stage.get("id") or ""
    return scenario


def _normalize_condition_leaf(condition: dict) -> dict:
    condition_type = str(condition.get("type") or "").strip().lower()
    default_operator = (
        "=="
        if condition_type in {"faction_membership", "system_unlock"}
        else ">="
    )
    if condition_type in STORY_STATE_CONDITION_TYPES:
        default_operator = "=="
    field = str(condition.get("field") or "").strip().lower()
    key = str(condition.get("key") or "").strip()
    value = condition.get("value", 1)
    if condition_type in FACTION_STANDING_CONDITION_TYPES and not field:
        # Historical `reputation` conditions defaulted to rank at runtime.
        field = "rank"
    elif condition_type == "faction_membership" and not field:
        field = "status"
    if condition_type in FACTION_STANDING_CONDITION_TYPES | {"faction_membership"}:
        key = normalize_key(key)
    elif condition_type in STORY_STATE_CONDITION_TYPES:
        key = normalize_state_key(key)
    elif condition_type in MODEL_CONDITION_TYPES:
        key = normalize_key(key)
        field = normalize_key(field)
    if condition_type == "faction_membership" and isinstance(value, str):
        value = normalize_key(value)
    elif (
        condition_type in FACTION_STANDING_CONDITION_TYPES
        and field == "tier"
        and isinstance(value, str)
        and not _is_numeric(value)
    ):
        value = normalize_key(value)
    return {
        "type": condition_type,
        "key": key,
        "operator": str(condition.get("operator") or default_operator).strip().lower(),
        "value": value,
        "equipped": bool(condition.get("equipped", False)),
        "field": field,
        "campaign_key": normalize_key(condition.get("campaign_key")),
        "description": str(condition.get("description") or "").strip(),
    }


def _normalize_conditions(raw: object) -> object:
    """Normalize a legacy condition list or a nested V2 condition expression."""
    if isinstance(raw, dict):
        group_keys = CONDITION_GROUP_KEYS.intersection(raw)
        if group_keys:
            # Validation reports multiple group operators; normalization keeps
            # all of them so malformed content is never silently reinterpreted.
            normalized: dict = {
                "description": str(raw.get("description") or "").strip()
            }
            for group_key in group_keys:
                child = raw.get(group_key)
                if group_key == "not":
                    normalized[group_key] = _normalize_conditions(child)
                else:
                    normalized[group_key] = [
                        _normalize_conditions(entry)
                        if isinstance(entry, (list, dict))
                        else entry
                        for entry in _as_list(child)
                    ]
            return normalized
        return _normalize_condition_leaf(raw)

    conditions = []
    for condition in _as_list(raw):
        if not isinstance(condition, dict):
            conditions.append(condition)
            continue
        conditions.append(_normalize_conditions(condition))
    return conditions


def _normalize_effects(raw: object) -> list[dict]:
    effects = []
    for effect in _as_list(raw):
        if not isinstance(effect, dict):
            effects.append(effect)
            continue
        normalized = copy.deepcopy(effect)
        effect_type = str(effect.get("type") or "").strip().lower()
        normalized["type"] = effect_type
        if effect_type in FACTION_EFFECT_TYPES:
            normalized["key"] = normalize_key(effect.get("key"))
        elif effect_type in STORY_STATE_EFFECT_TYPES:
            normalized["key"] = normalize_state_key(effect.get("key"))
        elif effect_type in MODEL_EFFECT_TYPES:
            normalized["key"] = normalize_key(effect.get("key"))
            normalized["field"] = normalize_key(effect.get("field"))
        elif effect_type in COLLECTION_EFFECT_TYPES:
            normalized["key"] = normalize_key(effect.get("key"))
            normalized["reward_type"] = normalize_key(effect.get("reward_type"))
        membership_status = normalized.get("status", normalized.get("value"))
        if effect_type == "faction_membership" and isinstance(
            membership_status, str
        ):
            normalized["status"] = normalize_key(membership_status)
        effects.append(normalized)
    return effects


def validate_package(raw: object) -> dict:
    package = normalize_package(raw)
    errors: list[str] = []

    if package.get("schema") != SCHEMA_NAME:
        errors.append(f"schema must be `{SCHEMA_NAME}`.")
    if package.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
        supported = ", ".join(str(version) for version in sorted(SUPPORTED_SCHEMA_VERSIONS))
        errors.append(f"schema_version must be one of: {supported}.")

    campaign_keys: set[str] = set()
    quest_keys: set[str] = set()
    for campaign_index, campaign in enumerate(package["campaigns"], start=1):
        prefix = f"campaigns[{campaign_index}]"
        if not isinstance(campaign, dict):
            errors.append(f"{prefix} must be an object.")
            continue
        campaign_key = campaign.get("key")
        if not campaign_key:
            errors.append(f"{prefix}.key is required.")
        elif campaign_key in campaign_keys:
            errors.append(f"Duplicate campaign key `{campaign_key}`.")
        campaign_keys.add(campaign_key)

        nodes = campaign.get("nodes") or []
        if not nodes:
            errors.append(f"Campaign `{campaign_key or campaign_index}` needs at least one node.")
            continue
        node_ids = [node.get("id") for node in nodes if isinstance(node, dict)]
        known_nodes = set(node_ids)
        _validate_conditions(
            campaign.get("requirements"),
            f"Campaign `{campaign_key}`",
            errors,
        )
        if not campaign.get("start_node"):
            errors.append(f"Campaign `{campaign_key}` needs a start_node.")
        elif campaign["start_node"] not in known_nodes:
            errors.append(
                f"Campaign `{campaign_key}` start_node `{campaign['start_node']}` does not exist."
            )

        seen_nodes: set[str] = set()
        for node_index, node in enumerate(nodes, start=1):
            node_prefix = f"campaign `{campaign_key}` node {node_index}"
            if not isinstance(node, dict):
                errors.append(f"{node_prefix} must be an object.")
                continue
            node_id = node.get("id")
            if not node_id:
                errors.append(f"{node_prefix} needs an id.")
            elif node_id in seen_nodes:
                errors.append(f"Campaign `{campaign_key}` has duplicate node `{node_id}`.")
            seen_nodes.add(node_id)

            node_type = node.get("type")
            if node_type not in NODE_TYPES:
                errors.append(f"Node `{node_id}` has unsupported type `{node_type}`.")

            edges = node.get("options") if node_type in CHOICE_NODE_TYPES else node.get("next")
            if node_type == "choice" and len(edges or []) < 2:
                errors.append(f"Choice node `{node_id}` needs at least two options.")
            if node_type == "dialogue" and not edges:
                errors.append(f"Dialogue node `{node_id}` needs at least one response.")
            if node_type in {"scene", "travel"} and not edges:
                errors.append(f"{node_type.title()} node `{node_id}` needs a next transition.")
            if node_type == "travel" and not node.get("location_key"):
                errors.append(f"Travel node `{node_id}` needs a location_key.")
            for edge in edges or []:
                if not edge.get("target"):
                    errors.append(f"Node `{node_id}` has a transition without a target.")
                elif edge["target"] not in known_nodes:
                    errors.append(
                        f"Node `{node_id}` points to missing node `{edge['target']}`."
                    )
                _validate_conditions(
                    edge.get("conditions"),
                    f"Node `{node_id}` transition to `{edge.get('target')}`",
                    errors,
                )
                _validate_effects(
                    edge.get("effects"),
                    f"Node `{node_id}` transition to `{edge.get('target')}`",
                    errors,
                )

            _validate_conditions(node.get("requirements"), f"Node `{node_id}`", errors)
            _validate_effects(node.get("effects"), f"Node `{node_id}`", errors)

            if node_type == "quest":
                _validate_quest_node(campaign_key, node, quest_keys, errors)
            elif node_type == "scenario":
                _validate_scenario_node(campaign_key, node, known_nodes, errors)

        if package.get("schema_version") == 2:
            _validate_campaign_graph(campaign, errors)

    for quest_index, quest in enumerate(package["standalone_quests"], start=1):
        owner = f"standalone_quests[{quest_index}]"
        if not isinstance(quest, dict):
            errors.append(f"{owner} must be an object.")
            continue
        _validate_conditions(
            (_as_dict(quest.get("access"))).get("conditions"),
            owner,
            errors,
        )

    monster_keys: set[str] = set()
    monster_names: set[str] = set()
    for monster in package["monsters"]:
        if not isinstance(monster, dict):
            errors.append("Every monster entry must be an object.")
            continue
        if not monster.get("key") or not monster.get("name"):
            errors.append("Every monster needs a key and name.")
            continue
        if monster["key"] in monster_keys:
            errors.append(f"Duplicate monster key `{monster['key']}`.")
        if monster["name"].casefold() in monster_names:
            errors.append(f"Duplicate monster name `{monster['name']}`.")
        monster_keys.add(monster["key"])
        monster_names.add(monster["name"].casefold())

    if errors:
        raise CampaignPackageError(errors)

    package["reference"] = build_reference_catalog(package["monsters"])
    return package


def _validate_campaign_graph(campaign: dict, errors: list[str]) -> None:
    """Reject V2 story graphs with unreachable chapters or no route to an ending."""
    campaign_key = campaign.get("key") or "campaign"
    nodes = [node for node in campaign.get("nodes") or [] if isinstance(node, dict)]
    by_id = {node.get("id"): node for node in nodes if node.get("id")}
    adjacency: dict[str, set[str]] = {node_id: set() for node_id in by_id}
    ending_ids = {
        node_id
        for node_id, node in by_id.items()
        if node.get("type") == "ending"
    }
    if not ending_ids:
        errors.append(f"Campaign `{campaign_key}` needs at least one ending node.")
        return

    for node_id, node in by_id.items():
        node_type = node.get("type")
        if node_type == "scenario":
            edges = [
                {"target": outcome.get("target")}
                for outcome in (node.get("scenario") or {}).get("outcomes") or []
                if isinstance(outcome, dict)
            ]
        else:
            edges = node.get("options") if node_type in CHOICE_NODE_TYPES else node.get("next")
        for edge in edges or []:
            target = edge.get("target") if isinstance(edge, dict) else None
            if target in by_id:
                adjacency[node_id].add(target)
        if node_type != "ending" and not adjacency[node_id]:
            errors.append(f"Campaign `{campaign_key}` node `{node_id}` is a non-ending dead end.")

    start = campaign.get("start_node")
    reachable: set[str] = set()
    frontier = [start] if start in by_id else []
    while frontier:
        node_id = frontier.pop()
        if node_id in reachable:
            continue
        reachable.add(node_id)
        frontier.extend(adjacency.get(node_id, ()))
    for node_id in sorted(set(by_id) - reachable):
        errors.append(f"Campaign `{campaign_key}` node `{node_id}` is unreachable from its start.")

    reverse: dict[str, set[str]] = {node_id: set() for node_id in by_id}
    for source, targets in adjacency.items():
        for target in targets:
            reverse[target].add(source)
    can_finish = set(ending_ids)
    frontier = list(ending_ids)
    while frontier:
        node_id = frontier.pop()
        for predecessor in reverse.get(node_id, ()):
            if predecessor not in can_finish:
                can_finish.add(predecessor)
                frontier.append(predecessor)
    for node_id in sorted(reachable - can_finish):
        errors.append(
            f"Campaign `{campaign_key}` node `{node_id}` cannot reach an ending."
        )


def _validate_scenario_node(
    campaign_key: str,
    node: dict,
    known_nodes: set[str],
    errors: list[str],
) -> None:
    owner = f"Campaign `{campaign_key}` scenario `{node.get('id')}`"
    scenario = node.get("scenario") or {}
    if not scenario.get("key"):
        errors.append(f"{owner} needs a scenario key.")
    stages = [stage for stage in scenario.get("stages") or [] if isinstance(stage, dict)]
    outcomes = [
        outcome for outcome in scenario.get("outcomes") or [] if isinstance(outcome, dict)
    ]
    if not stages:
        errors.append(f"{owner} needs at least one stage.")
        return
    if not outcomes:
        errors.append(f"{owner} needs at least one fail-forward outcome.")

    stage_ids = [stage.get("id") for stage in stages]
    outcome_ids = [outcome.get("id") for outcome in outcomes]
    if len(set(stage_ids)) != len(stage_ids) or any(not stage_id for stage_id in stage_ids):
        errors.append(f"{owner} has missing or duplicate stage IDs.")
    if len(set(outcome_ids)) != len(outcome_ids) or any(not value for value in outcome_ids):
        errors.append(f"{owner} has missing or duplicate outcome IDs.")
    if scenario.get("start_stage") not in set(stage_ids):
        errors.append(f"{owner} start_stage does not exist.")

    for stage in stages:
        stage_owner = f"{owner} stage `{stage.get('id')}`"
        actions = stage.get("actions") or []
        if not actions:
            errors.append(f"{stage_owner} needs at least one action.")
        for action_index, action in enumerate(actions, start=1):
            action_owner = f"{stage_owner} action {action_index}"
            if not isinstance(action, dict):
                errors.append(f"{action_owner} must be an object.")
                continue
            next_stage = action.get("next_stage")
            outcome = action.get("outcome")
            if bool(next_stage) == bool(outcome):
                errors.append(
                    f"{action_owner} must define exactly one next_stage or outcome."
                )
            elif next_stage and next_stage not in set(stage_ids):
                errors.append(f"{action_owner} points to missing stage `{next_stage}`.")
            elif outcome and outcome not in set(outcome_ids):
                errors.append(f"{action_owner} points to missing outcome `{outcome}`.")
            _validate_conditions(action.get("conditions"), action_owner, errors)
            _validate_effects(action.get("effects"), action_owner, errors)

    for outcome in outcomes:
        outcome_owner = f"{owner} outcome `{outcome.get('id')}`"
        if outcome.get("target") not in known_nodes:
            errors.append(
                f"{outcome_owner} points to missing node `{outcome.get('target')}`."
            )
        _validate_conditions(outcome.get("conditions"), outcome_owner, errors)
        _validate_effects(outcome.get("effects"), outcome_owner, errors)


def _validate_quest_node(
    campaign_key: str,
    node: dict,
    quest_keys: set[str],
    errors: list[str],
) -> None:
    node_id = node.get("id")
    quest = node.get("quest") or {}
    quest_key = normalize_key(quest.get("quest_key") or f"{campaign_key}_{node_id}")
    quest["quest_key"] = quest_key
    if quest_key in quest_keys:
        errors.append(f"Duplicate generated quest key `{quest_key}`.")
    quest_keys.add(quest_key)

    objective = _as_dict(quest.get("objective"))
    source = str(objective.get("source") or "none").lower()
    mode = str(objective.get("mode") or "progress").lower()
    if source not in OBJECTIVE_SOURCES:
        errors.append(f"Quest `{quest_key}` has unsupported source `{source}`.")
    if mode not in OBJECTIVE_MODES:
        errors.append(f"Quest `{quest_key}` has unsupported objective mode `{mode}`.")

    turnin_type = str((_as_dict(quest.get("turnin"))).get("type") or "progress").lower()
    reward_type = str((_as_dict(quest.get("reward"))).get("type") or "none").lower()
    if turnin_type not in TURNIN_TYPES:
        errors.append(f"Quest `{quest_key}` has unsupported turn-in type `{turnin_type}`.")
    if reward_type not in REWARD_TYPES:
        errors.append(f"Quest `{quest_key}` has unsupported reward type `{reward_type}`.")
    if reward_type in COLLECTION_REWARD_TYPES and not normalize_key(
        (_as_dict(quest.get("reward"))).get("key")
    ):
        errors.append(f"Quest `{quest_key}` reward `{reward_type}` needs a key.")
    _validate_conditions(
        quest.get("requirements"),
        f"Quest `{quest_key}`",
        errors,
    )
    if reward_type == "bundle":
        rewards = _as_list((_as_dict(quest.get("reward"))).get("rewards"))
        if not rewards:
            errors.append(f"Quest `{quest_key}` reward bundle is empty.")
        for bundled_reward in rewards:
            bundled_type = str((_as_dict(bundled_reward)).get("type") or "none").lower()
            if bundled_type not in REWARD_TYPES - {"bundle"}:
                errors.append(
                    f"Quest `{quest_key}` has unsupported bundled reward `{bundled_type}`."
                )
            elif bundled_type in COLLECTION_REWARD_TYPES and not normalize_key(
                (_as_dict(bundled_reward)).get("key")
            ):
                errors.append(
                    f"Quest `{quest_key}` bundled reward `{bundled_type}` needs a key."
                )

    encounter = node.get("encounter") or {}
    if encounter:
        kind = str(encounter.get("kind") or "none").lower()
        if kind not in ENCOUNTER_KINDS:
            errors.append(f"Quest `{quest_key}` has unsupported encounter kind `{kind}`.")
        party = _as_dict(encounter.get("party"))
        minimum = _positive_int(party.get("min"), 1)
        maximum = _positive_int(party.get("max"), minimum)
        if minimum > maximum:
            errors.append(f"Quest `{quest_key}` party minimum cannot exceed its maximum.")


def _validate_conditions(raw: object, owner: str, errors: list[str]) -> None:
    if raw in (None, [], {}):
        return
    if isinstance(raw, list):
        for index, condition in enumerate(raw, start=1):
            _validate_conditions(condition, f"{owner} condition {index}", errors)
        return
    if not isinstance(raw, dict):
        errors.append(f"{owner} contains a condition that is not an object.")
        return

    group_keys = CONDITION_GROUP_KEYS.intersection(raw)
    if group_keys:
        if len(group_keys) != 1:
            errors.append(f"{owner} condition group must use exactly one of all, any, or not.")
            return
        group_key = next(iter(group_keys))
        child = raw.get(group_key)
        if group_key in {"all", "any"}:
            if not isinstance(child, list) or not child:
                errors.append(f"{owner} `{group_key}` condition group cannot be empty.")
                return
            for index, condition in enumerate(child, start=1):
                _validate_conditions(
                    condition,
                    f"{owner} `{group_key}` branch {index}",
                    errors,
                )
        else:
            if child in (None, [], {}):
                errors.append(f"{owner} `not` condition group needs one expression.")
                return
            _validate_conditions(child, f"{owner} `not` branch", errors)
        return

    condition = raw
    condition_type = str(condition.get("type") or "").lower()
    if condition_type not in CONDITION_TYPES:
        errors.append(f"{owner} has unsupported condition `{condition_type}`.")
    operator = str(condition.get("operator") or ">=").strip().lower()
    if operator not in CONDITION_OPERATORS:
        errors.append(
            f"{owner} condition `{condition_type}` has unsupported operator `{operator}`."
        )
    if condition_type not in {
        "level",
        "money",
        "guild_member",
        "frontier_boss_regions",
    } and not str(condition.get("key") or "").strip():
        errors.append(f"{owner} condition `{condition_type}` needs a key.")

    if condition_type in STORY_STATE_CONDITION_TYPES:
        if not normalize_state_key(condition.get("key")):
            errors.append(f"{owner} story-state condition needs a valid dotted key.")
        if operator not in CONDITION_OPERATORS:
            errors.append(
                f"{owner} story-state condition has unsupported operator `{operator}`."
            )
    if condition_type in MODEL_CONDITION_TYPES:
        if not normalize_key(condition.get("key")):
            errors.append(f"{owner} condition `{condition_type}` needs an entity key.")
        if not normalize_key(condition.get("field")):
            errors.append(f"{owner} condition `{condition_type}` needs a field.")

    if condition_type in FACTION_STANDING_CONDITION_TYPES:
        field = str(condition.get("field") or "rank").strip().lower()
        if field not in FACTION_STANDING_FIELDS:
            errors.append(
                f"{owner} condition `{condition_type}` has unsupported field `{field}`; "
                "use points, rank, or tier."
            )
        if operator not in FACTION_STANDING_OPERATORS:
            errors.append(
                f"{owner} condition `{condition_type}` cannot use operator `{operator}`."
            )
        value = condition.get("value", 1)
        if field in {"points", "rank"} and not _is_numeric(value):
            errors.append(
                f"{owner} condition `{condition_type}` field `{field}` needs a numeric value."
            )
        elif field == "tier" and not (
            _is_numeric(value)
            or (isinstance(value, str) and bool(normalize_key(value)))
        ):
            errors.append(
                f"{owner} condition `{condition_type}` field `tier` needs a tier "
                "key or numeric value."
            )

    if condition_type == "faction_membership":
        if operator not in FACTION_MEMBERSHIP_OPERATORS:
            errors.append(
                f"{owner} condition `faction_membership` only supports equality operators."
            )
        value = condition.get("value")
        if not isinstance(value, str) or not normalize_key(value):
            errors.append(
                f"{owner} condition `faction_membership` needs a membership status value."
            )
    if condition_type == "system_unlock" and operator not in FACTION_MEMBERSHIP_OPERATORS:
        errors.append(
            f"{owner} condition `system_unlock` only supports equality operators."
        )


def _is_numeric(value: object) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    try:
        numeric = float(value)
        return math.isfinite(numeric) and numeric.is_integer()
    except (TypeError, ValueError, OverflowError):
        return False


def _validate_effects(raw: object, owner: str, errors: list[str]) -> None:
    for effect in _as_list(raw):
        if not isinstance(effect, dict):
            errors.append(f"{owner} contains an effect that is not an object.")
            continue
        effect_type = str(effect.get("type") or "").strip().lower()
        if effect_type in STORY_STATE_EFFECT_TYPES:
            key = normalize_state_key(effect.get("key"))
            if not key:
                errors.append(f"{owner} effect `{effect_type}` needs a story-state key.")
            if effect_type == "state_increment":
                delta = effect.get("delta", effect.get("value", 1))
                if isinstance(delta, bool) or not isinstance(delta, (int, float)):
                    errors.append(
                        f"{owner} effect `state_increment` needs a numeric delta."
                    )
            elif effect_type in {"state_set", "state_add", "state_remove"}:
                if "value" not in effect:
                    errors.append(f"{owner} effect `{effect_type}` needs a value.")
                else:
                    try:
                        json.dumps(effect.get("value"), allow_nan=False)
                    except (TypeError, ValueError):
                        errors.append(
                            f"{owner} effect `{effect_type}` value must be valid JSON."
                        )
            continue
        if effect_type in MODEL_EFFECT_TYPES:
            if not normalize_key(effect.get("key")):
                errors.append(f"{owner} effect `{effect_type}` needs an entity key.")
            if effect_type in {"relationship_change", "war_increment"}:
                delta = effect.get("delta", effect.get("value", 0))
                if isinstance(delta, bool) or not isinstance(delta, (int, float)):
                    errors.append(f"{owner} effect `{effect_type}` needs a numeric delta.")
            elif "value" not in effect:
                errors.append(f"{owner} effect `{effect_type}` needs a value.")
            continue
        if effect_type in COLLECTION_EFFECT_TYPES:
            reward_type = normalize_key(effect.get("reward_type"))
            if reward_type not in COLLECTION_REWARD_TYPES:
                errors.append(
                    f"{owner} effect `{effect_type}` has unsupported reward type `{reward_type}`."
                )
            if not normalize_key(effect.get("key")):
                errors.append(f"{owner} effect `{effect_type}` needs a reward key.")
            continue
        if effect_type not in FACTION_EFFECT_TYPES:
            continue
        key = normalize_key(effect.get("key"))
        if not key:
            errors.append(f"{owner} effect `{effect_type}` needs a faction key.")

        if effect_type in FACTION_STANDING_CONDITION_TYPES:
            provided_fields = [
                field
                for field in FACTION_STANDING_EFFECT_NUMERIC_FIELDS
                if effect.get(field) is not None
            ]
            if not provided_fields:
                errors.append(
                    f"{owner} effect `{effect_type}` needs a numeric standing value."
                )
            for field in provided_fields:
                if not _is_numeric(effect.get(field)):
                    errors.append(
                        f"{owner} effect `{effect_type}` field `{field}` must be numeric."
                    )
        elif effect_type == "faction_membership":
            status = effect.get("status", effect.get("value"))
            if not isinstance(status, str) or not normalize_key(status):
                errors.append(
                    f"{owner} effect `faction_membership` needs a membership status."
                )


def quest_record_from_node(campaign: dict, node: dict) -> dict:
    """Convert a validated quest node to the existing custom_quests shape."""
    quest = _as_dict(node.get("quest"))
    quest_key = normalize_key(quest.get("quest_key") or f"{campaign['key']}_{node['id']}")
    objective = _as_dict(quest.get("objective"))
    objective.setdefault("source", "none")
    objective.setdefault("mode", "progress")
    objective.setdefault("required_count", 0)
    objective.setdefault("target_name", "")

    turnin = _as_dict(quest.get("turnin"))
    turnin.setdefault("type", "progress")
    reward = _as_dict(quest.get("reward"))
    reward.setdefault("type", "none")
    access = _as_dict(quest.get("access"))
    combined_conditions = []
    for expression in (node.get("requirements"), quest.get("requirements")):
        if isinstance(expression, list):
            combined_conditions.extend(copy.deepcopy(expression))
        elif isinstance(expression, dict) and expression:
            combined_conditions.append(copy.deepcopy(expression))
    access.update(
        {
            "campaign_key": campaign["key"],
            "campaign_node_key": node["id"],
            "conditions": combined_conditions,
        }
    )

    return {
        "quest_key": quest_key,
        "name": str(quest.get("name") or node.get("title") or quest_key),
        "category": str(quest.get("category") or campaign.get("title") or "Campaign"),
        "short_description": str(quest.get("short_description") or node.get("description") or ""),
        "offer_text": str(quest.get("offer_text") or node.get("description") or ""),
        "turnin_text": str(quest.get("turnin_text") or ""),
        "objective": objective,
        "turnin": turnin,
        "reward": reward,
        "access": access,
        "prerequisites": _as_list(quest.get("prerequisites")),
        "repeatable": bool(quest.get("repeatable", False)),
        "is_active": bool(campaign.get("is_active", False) and quest.get("is_active", True)),
    }


def node_by_id(campaign: dict, node_id: str) -> dict | None:
    normalized = normalize_key(node_id)
    for node in campaign.get("nodes") or []:
        if node.get("id") == normalized:
            return node
    return None
