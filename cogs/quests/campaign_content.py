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

NODE_TYPES = {
    "quest", "choice", "dialogue", "scene", "travel", "scenario",
    "warfront", "ending",
}
CHOICE_NODE_TYPES = {"choice", "dialogue"}
MAX_PROMPT_OPTIONS = 20
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
    "fable_reward",
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
DIRECT_UNLOCK_EFFECT_TYPES = {"unlock", "system_unlock", "global_unlock"}
EFFECT_TYPES = (
    FACTION_EFFECT_TYPES
    | STORY_STATE_EFFECT_TYPES
    | MODEL_EFFECT_TYPES
    | COLLECTION_EFFECT_TYPES
    | DIRECT_UNLOCK_EFFECT_TYPES
)
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


def _bounded_int_or_original(
    value: object,
    default: int,
    *,
    minimum: int = 0,
    maximum: int | None = None,
) -> object:
    """Normalize valid integers without hiding authoring mistakes from validation."""
    raw = default if value in (None, "") else value
    if isinstance(raw, bool):
        return raw
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        return raw
    if isinstance(raw, float) and not raw.is_integer():
        return raw
    if isinstance(raw, str) and str(parsed) != raw.strip():
        return raw
    # Preserve out-of-range integers so the validator can give the author a
    # precise error instead of silently changing campaign balance.
    return parsed


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


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
            "chronicle_replay": (
                "Completed canonical snapshots can be replayed as isolated chronicle runs."
            ),
            "campaign_systems": (
                "Campaigns may define companions, camp scenes, autonomous events, "
                "locations, services, homes, and a configurable active party."
            ),
            "warfront": (
                "Warfront nodes resolve assigned force power across simultaneous fronts "
                "and require unconditional fail-forward outcome coverage."
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
        campaign["epilogue"] = _normalize_epilogue(campaign.get("epilogue"), "campaign")
        campaign["party_size"] = _bounded_int_or_original(
            campaign.get("party_size"), 2, minimum=1, maximum=4
        )
        campaign["companions"] = _normalize_companions(campaign.get("companions"))
        campaign["locations"] = _normalize_locations(campaign.get("locations"))
        campaign["autonomous_events"] = _normalize_autonomous_events(
            campaign.get("autonomous_events")
        )

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
            node["warfront"] = _normalize_warfront(node.get("warfront"), node["id"])
            node["location_key"] = normalize_key(node.get("location_key"))
            node["unlocks"] = _as_list(node.get("unlocks"))
            node["effects"] = _normalize_effects(node.get("effects"))
            node["requirements"] = _normalize_conditions(node.get("requirements"))
            node["epilogue"] = _normalize_epilogue(node.get("epilogue"), node["id"])
            node["quest"]["requirements"] = _normalize_conditions(
                node["quest"].get("requirements")
            )

    for quest in package["standalone_quests"]:
        if not isinstance(quest, dict):
            continue
        quest["quest_key"] = normalize_key(quest.get("quest_key"))
        quest["name"] = str(
            quest.get("name") or quest["quest_key"].replace("_", " ").title()
        )
        quest["category"] = str(quest.get("category") or "General")
        quest["objective"] = _as_dict(quest.get("objective"))
        quest["objective"]["source"] = str(
            quest["objective"].get("source") or "none"
        ).strip().lower()
        quest["objective"]["mode"] = str(
            quest["objective"].get("mode") or "progress"
        ).strip().lower()
        quest["turnin"] = _as_dict(quest.get("turnin"))
        quest["turnin"]["type"] = str(
            quest["turnin"].get("type") or "progress"
        ).strip().lower()
        quest["reward"] = _as_dict(quest.get("reward")) or {"type": "none"}
        quest["reward"]["type"] = str(
            quest["reward"].get("type") or "none"
        ).strip().lower()
        quest["prerequisites"] = [
            normalize_key(prerequisite)
            for prerequisite in _as_list(quest.get("prerequisites"))
            if normalize_key(prerequisite)
        ]
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


def _normalize_companions(raw: object) -> list[dict]:
    companions = []
    for companion in _as_list(raw):
        if not isinstance(companion, dict):
            companions.append(companion)
            continue
        normalized = copy.deepcopy(companion)
        normalized["key"] = normalize_key(companion.get("key") or companion.get("name"))
        normalized["name"] = str(
            companion.get("name") or normalized["key"].replace("_", " ").title()
        ).strip()
        normalized["role"] = str(companion.get("role") or "Companion").strip()
        normalized["description"] = str(companion.get("description") or "").strip()
        normalized["conditions"] = _normalize_conditions(companion.get("conditions"))
        normalized["scenes"] = []
        for index, scene in enumerate(_as_list(companion.get("scenes")), start=1):
            if not isinstance(scene, dict):
                normalized["scenes"].append(scene)
                continue
            scene_data = copy.deepcopy(scene)
            scene_data["id"] = normalize_key(
                scene.get("id") or f"{normalized['key']}_scene_{index}"
            )
            scene_data["title"] = str(
                scene.get("title") or scene_data["id"].replace("_", " ").title()
            ).strip()
            scene_data["text"] = str(scene.get("text") or "").strip()
            scene_data["order"] = _bounded_int_or_original(
                scene.get("order"), index * 10, minimum=0
            )
            scene_data["once"] = bool(scene.get("once", True))
            scene_data["requires_party"] = bool(scene.get("requires_party", False))
            scene_data["participants"] = [
                normalize_key(value)
                for value in _as_list(scene.get("participants") or [normalized["key"]])
                if normalize_key(value)
            ]
            scene_data["conditions"] = _normalize_conditions(scene.get("conditions"))
            scene_data["effects"] = _normalize_effects(scene.get("effects"))
            scene_data["unlocks"] = _as_list(scene.get("unlocks"))
            normalized["scenes"].append(scene_data)
        companions.append(normalized)
    return companions


def _normalize_locations(raw: object) -> list[dict]:
    locations = []
    for location in _as_list(raw):
        if not isinstance(location, dict):
            locations.append(location)
            continue
        normalized = copy.deepcopy(location)
        normalized["key"] = normalize_key(location.get("key") or location.get("name"))
        normalized["name"] = str(
            location.get("name") or normalized["key"].replace("_", " ").title()
        ).strip()
        normalized["description"] = str(location.get("description") or "").strip()
        normalized["travel_node"] = normalize_key(location.get("travel_node"))
        normalized["home"] = bool(location.get("home", False))
        normalized["conditions"] = _normalize_conditions(location.get("conditions"))
        normalized["services"] = []
        for service in _as_list(location.get("services")):
            if not isinstance(service, dict):
                normalized["services"].append(service)
                continue
            service_data = copy.deepcopy(service)
            service_data["key"] = normalize_key(service.get("key") or service.get("name"))
            service_data["name"] = str(
                service.get("name") or service_data["key"].replace("_", " ").title()
            ).strip()
            service_data["description"] = str(service.get("description") or "").strip()
            service_data["command"] = str(service.get("command") or "").strip()
            service_data["conditions"] = _normalize_conditions(service.get("conditions"))
            normalized["services"].append(service_data)
        locations.append(normalized)
    return locations


def _normalize_autonomous_events(raw: object) -> list[dict]:
    events = []
    for index, event in enumerate(_as_list(raw), start=1):
        if not isinstance(event, dict):
            events.append(event)
            continue
        normalized = copy.deepcopy(event)
        normalized["id"] = normalize_key(event.get("id") or f"autonomous_event_{index}")
        normalized["trigger_node"] = normalize_key(event.get("trigger_node"))
        normalized["title"] = str(event.get("title") or "Companion Decision").strip()
        normalized["text"] = str(event.get("text") or "").strip()
        normalized["redirect_target"] = normalize_key(event.get("redirect_target"))
        normalized["conditions"] = _normalize_conditions(event.get("conditions"))
        normalized["effects"] = _normalize_effects(event.get("effects"))
        normalized["unlocks"] = _as_list(event.get("unlocks"))
        events.append(normalized)
    return events


def _normalize_warfront(raw: object, node_id: str) -> dict:
    warfront = _as_dict(raw)
    if not warfront:
        return {}
    warfront["key"] = normalize_key(warfront.get("key") or node_id)
    warfront["title"] = str(
        warfront.get("title") or warfront["key"].replace("_", " ").title()
    ).strip()
    warfront["description"] = str(warfront.get("description") or "").strip()
    warfront["player_power"] = _bounded_int_or_original(
        warfront.get("player_power"), 100, minimum=0
    )
    warfront["fronts"] = _as_list(warfront.get("fronts"))
    warfront["assets"] = _as_list(warfront.get("assets"))
    warfront["outcomes"] = _as_list(warfront.get("outcomes"))
    for front in warfront["fronts"]:
        if not isinstance(front, dict):
            continue
        front["id"] = normalize_key(front.get("id"))
        front["title"] = str(front.get("title") or front["id"].replace("_", " ").title())
        front["description"] = str(front.get("description") or "").strip()
        front["difficulty"] = _bounded_int_or_original(
            front.get("difficulty"), 100, minimum=0
        )
        front["base_power"] = _bounded_int_or_original(
            front.get("base_power"), 0, minimum=0
        )
        front["max_assets"] = _bounded_int_or_original(
            front.get("max_assets"), 1, minimum=0, maximum=5
        )
        front["conditions"] = _normalize_conditions(front.get("conditions"))
        front["success_effects"] = _normalize_effects(front.get("success_effects"))
        front["failure_effects"] = _normalize_effects(front.get("failure_effects"))
        front["success_unlocks"] = _as_list(front.get("success_unlocks"))
        front["failure_unlocks"] = _as_list(front.get("failure_unlocks"))
    for asset in warfront["assets"]:
        if not isinstance(asset, dict):
            continue
        asset["key"] = normalize_key(asset.get("key") or asset.get("name"))
        asset["name"] = str(asset.get("name") or asset["key"].replace("_", " ").title())
        asset["description"] = str(asset.get("description") or "").strip()
        asset["power"] = _bounded_int_or_original(
            asset.get("power"), 0, minimum=0
        )
        asset["conditions"] = _normalize_conditions(asset.get("conditions"))
    for outcome in warfront["outcomes"]:
        if not isinstance(outcome, dict):
            continue
        outcome["id"] = normalize_key(outcome.get("id"))
        outcome["title"] = str(outcome.get("title") or outcome["id"].replace("_", " ").title())
        outcome["description"] = str(outcome.get("description") or "").strip()
        outcome["minimum_successes"] = _bounded_int_or_original(
            outcome.get("minimum_successes"), 0, minimum=0
        )
        maximum = outcome.get("maximum_successes")
        outcome["maximum_successes"] = (
            None
            if maximum in (None, "")
            else _bounded_int_or_original(maximum, 0, minimum=0)
        )
        outcome["target"] = normalize_key(outcome.get("target"))
        outcome["conditions"] = _normalize_conditions(outcome.get("conditions"))
        outcome["effects"] = _normalize_effects(outcome.get("effects"))
        outcome["unlocks"] = _as_list(outcome.get("unlocks"))
    return warfront


def resolve_warfront(
    warfront: dict,
    assignments: Mapping[str, object] | None,
    player_front: object,
    available_assets: list[dict],
    *,
    eligible_outcome_ids: set[str] | None = None,
) -> dict:
    """Resolve one authored warfront plan without Discord or database state.

    The caller determines which conditional assets and outcomes are currently
    eligible. This function owns allocation integrity, power calculation, and
    fail-forward result selection, making the core operation deterministic and
    independently testable.
    """
    fronts = [front for front in warfront.get("fronts") or [] if isinstance(front, dict)]
    fronts_by_id = {str(front.get("id") or ""): front for front in fronts}
    player_front_key = normalize_key(player_front)
    if not player_front_key or player_front_key not in fronts_by_id:
        raise ValueError("Choose a valid front for the player to lead.")

    assets_by_key = {
        normalize_key(asset.get("key")): asset
        for asset in available_assets or []
        if isinstance(asset, dict) and normalize_key(asset.get("key"))
    }
    normalized_assignments: dict[str, list[str]] = {front_id: [] for front_id in fronts_by_id}
    seen_assets: set[str] = set()
    for raw_front, raw_assets in dict(assignments or {}).items():
        front_id = normalize_key(raw_front)
        if front_id not in fronts_by_id:
            raise ValueError(f"Assignment references unknown front `{front_id}`.")
        keys = [normalize_key(key) for key in _as_list(raw_assets)]
        keys = [key for key in keys if key]
        if len(keys) > int(fronts_by_id[front_id].get("max_assets") or 0):
            raise ValueError(f"Front `{front_id}` has too many assigned assets.")
        for key in keys:
            if key not in assets_by_key:
                raise ValueError(f"Asset `{key}` is not currently available.")
            if key in seen_assets:
                raise ValueError(f"Asset `{key}` is assigned to more than one front.")
            seen_assets.add(key)
        normalized_assignments[front_id] = keys

    results = []
    for front in fronts:
        front_id = front["id"]
        assigned_keys = normalized_assignments[front_id]
        power = int(front.get("base_power") or 0)
        power += sum(int(assets_by_key[key].get("power") or 0) for key in assigned_keys)
        personally_led = player_front_key == front_id
        if personally_led:
            power += int(warfront.get("player_power") or 0)
        difficulty = int(front.get("difficulty") or 0)
        results.append(
            {
                "front": front_id,
                "title": front.get("title") or front_id,
                "power": power,
                "difficulty": difficulty,
                "success": power >= difficulty,
                "assigned": assigned_keys,
                "player": personally_led,
            }
        )

    success_count = sum(1 for result in results if result["success"])
    outcome = None
    for candidate in warfront.get("outcomes") or []:
        if not isinstance(candidate, dict):
            continue
        candidate_id = normalize_key(candidate.get("id"))
        if eligible_outcome_ids is not None and candidate_id not in eligible_outcome_ids:
            continue
        minimum = int(candidate.get("minimum_successes") or 0)
        maximum = candidate.get("maximum_successes")
        maximum = len(results) if maximum is None else int(maximum)
        if minimum <= success_count <= maximum:
            outcome = candidate
            break
    if outcome is None:
        raise ValueError("No eligible fail-forward outcome covers this result.")
    return {
        "outcome": outcome,
        "successes": success_count,
        "fronts": results,
    }


def _normalize_condition_leaf(condition: dict) -> dict:
    condition_type = str(condition.get("type") or "").strip().lower()
    default_operator = (
        "=="
        if condition_type in {"faction_membership", "system_unlock", "fable_reward"}
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
    elif condition_type == "fable_reward":
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


def _normalize_epilogue(raw: object, owner_key: str) -> list[dict]:
    fragments = []
    for index, fragment in enumerate(_as_list(raw), start=1):
        if not isinstance(fragment, dict):
            fragments.append(fragment)
            continue
        normalized = copy.deepcopy(fragment)
        normalized["id"] = normalize_key(
            fragment.get("id") or f"{owner_key}_epilogue_{index}"
        )
        normalized["title"] = str(fragment.get("title") or "Aftermath").strip()
        normalized["text"] = str(fragment.get("text") or "").strip()
        raw_order = fragment.get("order", index * 10)
        if isinstance(raw_order, bool):
            normalized["order"] = raw_order
        else:
            try:
                normalized["order"] = int(raw_order)
            except (TypeError, ValueError):
                normalized["order"] = raw_order
        normalized["conditions"] = _normalize_conditions(fragment.get("conditions"))
        fragments.append(normalized)
    return fragments


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
        _validate_epilogue(
            campaign.get("epilogue"),
            f"Campaign `{campaign_key}`",
            errors,
        )
        if not campaign.get("start_node"):
            errors.append(f"Campaign `{campaign_key}` needs a start_node.")
        elif campaign["start_node"] not in known_nodes:
            errors.append(
                f"Campaign `{campaign_key}` start_node `{campaign['start_node']}` does not exist."
            )
        _validate_campaign_systems(campaign, known_nodes, errors)

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
            if len(edges or []) > MAX_PROMPT_OPTIONS:
                errors.append(
                    f"Node `{node_id}` has more than {MAX_PROMPT_OPTIONS} transitions; "
                    "split it into smaller player prompts."
                )
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
                _validate_unlocks(
                    edge.get("unlocks"),
                    f"Node `{node_id}` transition to `{edge.get('target')}`",
                    errors,
                )

            _validate_conditions(node.get("requirements"), f"Node `{node_id}`", errors)
            _validate_effects(node.get("effects"), f"Node `{node_id}`", errors)
            _validate_unlocks(node.get("unlocks"), f"Node `{node_id}`", errors)
            _validate_epilogue(node.get("epilogue"), f"Node `{node_id}`", errors)

            if node_type == "quest":
                _validate_quest_node(campaign_key, node, quest_keys, errors)
            elif node_type == "scenario":
                _validate_scenario_node(campaign_key, node, known_nodes, errors)
            elif node_type == "warfront":
                _validate_warfront_node(campaign_key, node, known_nodes, errors)

        if package.get("schema_version") == 2:
            _validate_campaign_graph(campaign, errors)

    for quest_index, quest in enumerate(package["standalone_quests"], start=1):
        owner = f"standalone_quests[{quest_index}]"
        if not isinstance(quest, dict):
            errors.append(f"{owner} must be an object.")
            continue
        quest_key = normalize_key(quest.get("quest_key"))
        if not quest_key:
            errors.append(f"{owner}.quest_key is required.")
        elif quest_key in quest_keys:
            errors.append(f"Duplicate quest key `{quest_key}`.")
        quest_keys.add(quest_key)
        _validate_quest_payload(quest_key or owner, quest, errors)
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


def _validate_campaign_systems(
    campaign: dict,
    known_nodes: set[str],
    errors: list[str],
) -> None:
    owner = f"Campaign `{campaign.get('key') or 'campaign'}`"
    party_size = campaign.get("party_size")
    if not _is_integer(party_size) or not 1 <= party_size <= 4:
        errors.append(f"{owner} party_size must be an integer from 1 to 4.")
    companion_keys: set[str] = set()
    scene_ids: set[str] = set()
    for companion in campaign.get("companions") or []:
        if not isinstance(companion, dict):
            errors.append(f"{owner} contains a companion that is not an object.")
            continue
        key = companion.get("key")
        if not key:
            errors.append(f"{owner} has a companion without a key.")
        elif key in companion_keys:
            errors.append(f"{owner} has duplicate companion `{key}`.")
        companion_keys.add(key)
        _validate_conditions(companion.get("conditions"), f"{owner} companion `{key}`", errors)
        for scene in companion.get("scenes") or []:
            if not isinstance(scene, dict):
                errors.append(f"{owner} companion `{key}` has a scene that is not an object.")
                continue
            scene_id = scene.get("id")
            if not scene_id:
                errors.append(f"{owner} companion `{key}` has a scene without an id.")
            elif scene_id in scene_ids:
                errors.append(f"{owner} has duplicate camp scene `{scene_id}`.")
            scene_ids.add(scene_id)
            if not str(scene.get("text") or "").strip():
                errors.append(f"{owner} camp scene `{scene_id}` needs player-facing text.")
            if not _is_integer(scene.get("order")) or scene.get("order", 0) < 0:
                errors.append(
                    f"{owner} camp scene `{scene_id}` order must be a non-negative integer."
                )
            for participant in scene.get("participants") or []:
                if participant not in companion_keys and participant != key:
                    # A later companion may still be valid; checked after collection below.
                    pass
            _validate_conditions(scene.get("conditions"), f"{owner} camp scene `{scene_id}`", errors)
            _validate_effects(scene.get("effects"), f"{owner} camp scene `{scene_id}`", errors)
            _validate_unlocks(scene.get("unlocks"), f"{owner} camp scene `{scene_id}`", errors)
    for companion in campaign.get("companions") or []:
        if not isinstance(companion, dict):
            continue
        for scene in companion.get("scenes") or []:
            if not isinstance(scene, dict):
                continue
            for participant in scene.get("participants") or []:
                if participant not in companion_keys:
                    errors.append(
                        f"{owner} camp scene `{scene.get('id')}` references missing companion `{participant}`."
                    )

    location_keys: set[str] = set()
    service_keys: set[str] = set()
    for location in campaign.get("locations") or []:
        if not isinstance(location, dict):
            errors.append(f"{owner} contains a location that is not an object.")
            continue
        key = location.get("key")
        if not key:
            errors.append(f"{owner} has a location without a key.")
        elif key in location_keys:
            errors.append(f"{owner} has duplicate location `{key}`.")
        location_keys.add(key)
        if location.get("travel_node") and location["travel_node"] not in known_nodes:
            errors.append(
                f"{owner} location `{key}` points to missing travel node `{location['travel_node']}`."
            )
        _validate_conditions(location.get("conditions"), f"{owner} location `{key}`", errors)
        local_services: set[str] = set()
        for service in location.get("services") or []:
            if not isinstance(service, dict):
                errors.append(f"{owner} location `{key}` has a service that is not an object.")
                continue
            service_key = service.get("key")
            if not service_key:
                errors.append(f"{owner} location `{key}` has a service without a key.")
            elif service_key in local_services:
                errors.append(f"{owner} location `{key}` repeats service `{service_key}`.")
            elif service_key in service_keys:
                errors.append(
                    f"{owner} repeats service key `{service_key}` across locations."
                )
            local_services.add(service_key)
            service_keys.add(service_key)
            if not service.get("command"):
                errors.append(f"{owner} service `{service_key}` needs a launch command.")
            _validate_conditions(service.get("conditions"), f"{owner} service `{service_key}`", errors)

    event_ids: set[str] = set()
    for event in campaign.get("autonomous_events") or []:
        if not isinstance(event, dict):
            errors.append(f"{owner} contains an autonomous event that is not an object.")
            continue
        event_id = event.get("id")
        if not event_id:
            errors.append(f"{owner} has an autonomous event without an id.")
        elif event_id in event_ids:
            errors.append(f"{owner} has duplicate autonomous event `{event_id}`.")
        event_ids.add(event_id)
        if event.get("trigger_node") not in known_nodes:
            errors.append(
                f"{owner} autonomous event `{event_id}` has a missing trigger node."
            )
        if event.get("redirect_target") and event["redirect_target"] not in known_nodes:
            errors.append(
                f"{owner} autonomous event `{event_id}` points to missing redirect `{event['redirect_target']}`."
            )
        if event.get("redirect_target") == event.get("trigger_node"):
            errors.append(f"{owner} autonomous event `{event_id}` redirects to its own trigger.")
        if not str(event.get("text") or "").strip():
            errors.append(f"{owner} autonomous event `{event_id}` needs player-facing text.")
        _validate_conditions(event.get("conditions"), f"{owner} event `{event_id}`", errors)
        _validate_effects(event.get("effects"), f"{owner} event `{event_id}`", errors)
        _validate_unlocks(event.get("unlocks"), f"{owner} event `{event_id}`", errors)


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
        elif node_type == "warfront":
            edges = [
                {"target": outcome.get("target")}
                for outcome in (node.get("warfront") or {}).get("outcomes") or []
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

    stage_adjacency: dict[str, set[str]] = {
        stage_id: set() for stage_id in stage_ids if stage_id
    }
    stages_with_outcomes: set[str] = set()
    referenced_outcomes: set[str] = set()
    for stage in stages:
        stage_owner = f"{owner} stage `{stage.get('id')}`"
        actions = stage.get("actions") or []
        if not actions:
            errors.append(f"{stage_owner} needs at least one action.")
        if len(actions) > MAX_PROMPT_OPTIONS:
            errors.append(
                f"{stage_owner} has more than {MAX_PROMPT_OPTIONS} actions; "
                "split it into smaller stages."
            )
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
            elif next_stage:
                stage_adjacency.setdefault(stage.get("id"), set()).add(next_stage)
            elif outcome:
                stages_with_outcomes.add(stage.get("id"))
                referenced_outcomes.add(outcome)
            _validate_conditions(action.get("conditions"), action_owner, errors)
            _validate_effects(action.get("effects"), action_owner, errors)
            _validate_unlocks(action.get("unlocks"), action_owner, errors)

    for outcome in outcomes:
        outcome_owner = f"{owner} outcome `{outcome.get('id')}`"
        if outcome.get("target") not in known_nodes:
            errors.append(
                f"{outcome_owner} points to missing node `{outcome.get('target')}`."
            )
        _validate_conditions(outcome.get("conditions"), outcome_owner, errors)
        _validate_effects(outcome.get("effects"), outcome_owner, errors)
        _validate_unlocks(outcome.get("unlocks"), outcome_owner, errors)

    start_stage = scenario.get("start_stage")
    reachable_stages: set[str] = set()
    frontier = [start_stage] if start_stage in stage_adjacency else []
    while frontier:
        stage_id = frontier.pop()
        if stage_id in reachable_stages:
            continue
        reachable_stages.add(stage_id)
        frontier.extend(stage_adjacency.get(stage_id, ()))
    for stage_id in sorted(set(stage_adjacency) - reachable_stages):
        errors.append(f"{owner} stage `{stage_id}` is unreachable from its start stage.")

    reverse_stages: dict[str, set[str]] = {
        stage_id: set() for stage_id in stage_adjacency
    }
    for source, targets in stage_adjacency.items():
        for target in targets:
            reverse_stages[target].add(source)
    can_finish = set(stages_with_outcomes)
    frontier = list(stages_with_outcomes)
    while frontier:
        stage_id = frontier.pop()
        for predecessor in reverse_stages.get(stage_id, ()):
            if predecessor not in can_finish:
                can_finish.add(predecessor)
                frontier.append(predecessor)
    for stage_id in sorted(reachable_stages - can_finish):
        errors.append(f"{owner} stage `{stage_id}` cannot reach a fail-forward outcome.")
    for outcome_id in sorted(set(outcome_ids) - referenced_outcomes):
        if outcome_id:
            errors.append(f"{owner} outcome `{outcome_id}` is never used by a stage action.")


def _validate_warfront_node(
    campaign_key: str,
    node: dict,
    known_nodes: set[str],
    errors: list[str],
) -> None:
    owner = f"Campaign `{campaign_key}` warfront `{node.get('id')}`"
    warfront = node.get("warfront") or {}
    if not warfront.get("key"):
        errors.append(f"{owner} needs a warfront key.")
    if not _is_integer(warfront.get("player_power")) or warfront.get("player_power", 0) < 0:
        errors.append(f"{owner} player_power must be a non-negative integer.")
    fronts = [front for front in warfront.get("fronts") or [] if isinstance(front, dict)]
    assets = [asset for asset in warfront.get("assets") or [] if isinstance(asset, dict)]
    outcomes = [outcome for outcome in warfront.get("outcomes") or [] if isinstance(outcome, dict)]
    if len(fronts) < 2:
        errors.append(f"{owner} needs at least two simultaneous fronts.")
    if not outcomes:
        errors.append(f"{owner} needs at least one result outcome.")
    front_ids = [front.get("id") for front in fronts]
    asset_keys = [asset.get("key") for asset in assets]
    outcome_ids = [outcome.get("id") for outcome in outcomes]
    if any(not value for value in front_ids) or len(set(front_ids)) != len(front_ids):
        errors.append(f"{owner} has missing or duplicate front IDs.")
    if any(not value for value in asset_keys) or len(set(asset_keys)) != len(asset_keys):
        errors.append(f"{owner} has missing or duplicate asset keys.")
    if any(not value for value in outcome_ids) or len(set(outcome_ids)) != len(outcome_ids):
        errors.append(f"{owner} has missing or duplicate outcome IDs.")
    for front in fronts:
        front_owner = f"{owner} front `{front.get('id')}`"
        for field in ("difficulty", "base_power", "max_assets"):
            if not _is_integer(front.get(field)) or front.get(field, 0) < 0:
                errors.append(f"{front_owner} {field} must be a non-negative integer.")
        if _is_integer(front.get("max_assets")) and front["max_assets"] > 5:
            errors.append(f"{front_owner} max_assets cannot exceed 5.")
        _validate_conditions(front.get("conditions"), front_owner, errors)
        _validate_effects(front.get("success_effects"), f"{front_owner} success", errors)
        _validate_effects(front.get("failure_effects"), f"{front_owner} failure", errors)
        _validate_unlocks(front.get("success_unlocks"), f"{front_owner} success", errors)
        _validate_unlocks(front.get("failure_unlocks"), f"{front_owner} failure", errors)
    for asset in assets:
        if not _is_integer(asset.get("power")) or asset.get("power", 0) < 0:
            errors.append(
                f"{owner} asset `{asset.get('key')}` power must be a non-negative integer."
            )
        _validate_conditions(
            asset.get("conditions"),
            f"{owner} asset `{asset.get('key')}`",
            errors,
        )
    covered_success_counts: set[int] = set()
    unconditional_success_counts: set[int] = set()
    for outcome in outcomes:
        outcome_owner = f"{owner} outcome `{outcome.get('id')}`"
        if (
            not _is_integer(outcome.get("minimum_successes"))
            or outcome.get("minimum_successes", 0) < 0
        ):
            errors.append(
                f"{outcome_owner} minimum_successes must be a non-negative integer."
            )
            continue
        minimum = outcome["minimum_successes"]
        maximum = outcome.get("maximum_successes")
        if maximum is not None and (not _is_integer(maximum) or maximum < 0):
            errors.append(
                f"{outcome_owner} maximum_successes must be a non-negative integer or blank."
            )
            continue
        maximum = len(fronts) if maximum is None else maximum
        if minimum > maximum:
            errors.append(f"{outcome_owner} minimum successes exceed its maximum.")
        if maximum > len(fronts):
            errors.append(f"{outcome_owner} maximum successes exceed the number of fronts.")
        covered_range = set(range(max(0, minimum), min(len(fronts), maximum) + 1))
        covered_success_counts.update(covered_range)
        if not outcome.get("conditions"):
            unconditional_success_counts.update(covered_range)
        if outcome.get("target") not in known_nodes:
            errors.append(
                f"{outcome_owner} points to missing node `{outcome.get('target')}`."
            )
        _validate_conditions(outcome.get("conditions"), outcome_owner, errors)
        _validate_effects(outcome.get("effects"), outcome_owner, errors)
        _validate_unlocks(outcome.get("unlocks"), outcome_owner, errors)
    missing_counts = sorted(set(range(len(fronts) + 1)) - covered_success_counts)
    if missing_counts:
        errors.append(
            f"{owner} has no fail-forward outcome for success counts: "
            + ", ".join(str(value) for value in missing_counts)
            + "."
        )
    missing_fallbacks = sorted(
        set(range(len(fronts) + 1)) - unconditional_success_counts
    )
    if not missing_counts and missing_fallbacks:
        errors.append(
            f"{owner} needs an unconditional fallback outcome for success counts: "
            + ", ".join(str(value) for value in missing_fallbacks)
            + "."
        )


def _validate_quest_payload(quest_key: str, quest: dict, errors: list[str]) -> None:
    objective = _as_dict(quest.get("objective"))
    source = str(objective.get("source") or "none").strip().lower()
    mode = str(objective.get("mode") or "progress").strip().lower()
    if source not in OBJECTIVE_SOURCES:
        errors.append(f"Quest `{quest_key}` has unsupported source `{source}`.")
    if mode not in OBJECTIVE_MODES:
        errors.append(f"Quest `{quest_key}` has unsupported objective mode `{mode}`.")

    turnin_type = str((_as_dict(quest.get("turnin"))).get("type") or "progress").lower()
    reward = _as_dict(quest.get("reward"))
    reward_type = str(reward.get("type") or "none").lower()
    if turnin_type not in TURNIN_TYPES:
        errors.append(f"Quest `{quest_key}` has unsupported turn-in type `{turnin_type}`.")
    if reward_type not in REWARD_TYPES:
        errors.append(f"Quest `{quest_key}` has unsupported reward type `{reward_type}`.")
    elif reward_type in COLLECTION_REWARD_TYPES and not normalize_key(reward.get("key")):
        errors.append(f"Quest `{quest_key}` reward `{reward_type}` needs a key.")
    elif reward_type == "bundle":
        rewards = _as_list(reward.get("rewards"))
        if not rewards:
            errors.append(f"Quest `{quest_key}` reward bundle is empty.")
        for bundled_reward in rewards:
            bundled = _as_dict(bundled_reward)
            bundled_type = str(bundled.get("type") or "none").lower()
            if bundled_type not in REWARD_TYPES - {"bundle"}:
                errors.append(
                    f"Quest `{quest_key}` has unsupported bundled reward `{bundled_type}`."
                )
            elif bundled_type in COLLECTION_REWARD_TYPES and not normalize_key(
                bundled.get("key")
            ):
                errors.append(
                    f"Quest `{quest_key}` bundled reward `{bundled_type}` needs a key."
                )

    prerequisites = _as_list(quest.get("prerequisites"))
    normalized_prerequisites = [normalize_key(value) for value in prerequisites]
    if len(set(normalized_prerequisites)) != len(normalized_prerequisites):
        errors.append(f"Quest `{quest_key}` has duplicate prerequisites.")
    if quest_key in normalized_prerequisites:
        errors.append(f"Quest `{quest_key}` cannot require itself.")


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
    if condition_type == "fable_reward":
        if not normalize_key(condition.get("key")):
            errors.append(f"{owner} condition `fable_reward` needs a reward key.")
        reward_type = normalize_key(condition.get("field"))
        if reward_type not in COLLECTION_REWARD_TYPES:
            errors.append(
                f"{owner} condition `fable_reward` needs a supported reward type in field."
            )
        if operator not in FACTION_MEMBERSHIP_OPERATORS:
            errors.append(
                f"{owner} condition `fable_reward` only supports equality operators."
            )

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
        if effect_type not in EFFECT_TYPES:
            errors.append(f"{owner} has unsupported effect `{effect_type or 'missing type'}`.")
            continue
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
                _validate_increment_bounds(effect, owner, effect_type, errors)
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
                _validate_increment_bounds(effect, owner, effect_type, errors)
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
        if effect_type in DIRECT_UNLOCK_EFFECT_TYPES:
            if not normalize_key(effect.get("key")):
                errors.append(f"{owner} effect `{effect_type}` needs an unlock key.")
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


def _validate_increment_bounds(
    effect: dict,
    owner: str,
    effect_type: str,
    errors: list[str],
) -> None:
    for name in ("minimum", "maximum"):
        value = effect.get(name)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float))
        ):
            errors.append(f"{owner} effect `{effect_type}` {name} must be numeric.")
    minimum = effect.get("minimum")
    maximum = effect.get("maximum")
    if (
        isinstance(minimum, (int, float))
        and not isinstance(minimum, bool)
        and isinstance(maximum, (int, float))
        and not isinstance(maximum, bool)
        and minimum > maximum
    ):
        errors.append(
            f"{owner} effect `{effect_type}` minimum cannot exceed its maximum."
        )


def _validate_unlocks(raw: object, owner: str, errors: list[str]) -> None:
    for index, unlock in enumerate(_as_list(raw), start=1):
        unlock_owner = f"{owner} unlock {index}"
        if isinstance(unlock, str):
            if not normalize_key(unlock):
                errors.append(f"{unlock_owner} needs a key.")
            continue
        if not isinstance(unlock, dict):
            errors.append(f"{unlock_owner} must be a string or object.")
            continue
        if not normalize_key(unlock.get("key")):
            errors.append(f"{unlock_owner} needs a key.")
        scope = str(unlock.get("scope") or "campaign").strip().lower()
        if scope not in {"campaign", "system", "global"}:
            errors.append(
                f"{unlock_owner} has unsupported scope `{scope}`; "
                "use campaign, system, or global."
            )


def _validate_epilogue(raw: object, owner: str, errors: list[str]) -> None:
    seen_ids: set[str] = set()
    for index, fragment in enumerate(_as_list(raw), start=1):
        fragment_owner = f"{owner} epilogue fragment {index}"
        if not isinstance(fragment, dict):
            errors.append(f"{fragment_owner} must be an object.")
            continue
        fragment_id = normalize_key(fragment.get("id"))
        if not fragment_id:
            errors.append(f"{fragment_owner} needs an id.")
        elif fragment_id in seen_ids:
            errors.append(f"{owner} has duplicate epilogue fragment `{fragment_id}`.")
        seen_ids.add(fragment_id)
        if not str(fragment.get("text") or "").strip():
            errors.append(f"{fragment_owner} needs player-facing text.")
        order = fragment.get("order", index * 10)
        if isinstance(order, bool) or not isinstance(order, int):
            errors.append(f"{fragment_owner} order must be an integer.")
        _validate_conditions(
            fragment.get("conditions"),
            f"{fragment_owner} conditions",
            errors,
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
