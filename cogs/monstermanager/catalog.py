"""Safe storage helpers for the interactive ``monsters.json`` editor."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional


CORE_MONSTER_FIELDS = {
    "name",
    "hp",
    "attack",
    "defense",
    "element",
    "url",
    "ispublic",
    "frontier_only",
    "frontier_region_id",
}
MIN_MONSTER_TIER = 1
MAX_MONSTER_TIER = 12


class MonsterCatalogError(ValueError):
    """The catalog or a requested mutation is invalid."""


class MonsterCatalogConflict(MonsterCatalogError):
    """The selected record changed after it was displayed."""


def tier_sort_key(value: Any) -> tuple[int, int | str]:
    text = str(value).strip()
    try:
        return (0, int(text))
    except ValueError:
        return (1, text.casefold())


def monster_fingerprint(monster: Mapping[str, Any]) -> str:
    payload = json.dumps(
        dict(monster),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MonsterLocator:
    tier: str
    index: int
    fingerprint: str


@dataclass(frozen=True)
class MonsterEntry:
    locator: MonsterLocator
    monster: dict[str, Any]

    @property
    def tier(self) -> str:
        return self.locator.tier

    @property
    def index(self) -> int:
        return self.locator.index


def validate_tier(value: Any) -> str:
    try:
        tier = int(str(value).strip())
    except (TypeError, ValueError) as error:
        raise MonsterCatalogError("Tier must be a whole number from 1 to 12.") from error
    if tier < MIN_MONSTER_TIER or tier > MAX_MONSTER_TIER:
        raise MonsterCatalogError("Tier must be between 1 and 12.")
    return str(tier)


def parse_stat_value(value: Any, label: str) -> int | str:
    text = str(value or "").strip()
    if not text:
        raise MonsterCatalogError(f"{label} cannot be empty.")
    if text == "???":
        return text
    try:
        parsed = int(text.replace(",", ""))
    except ValueError as error:
        raise MonsterCatalogError(f"{label} must be a non-negative whole number or `???`.") from error
    if parsed < 0:
        raise MonsterCatalogError(f"{label} cannot be negative.")
    return parsed


def parse_stat_triplet(value: Any) -> tuple[int | str, int | str, int | str]:
    text = str(value or "").strip()
    for separator in ("/", "|", ";"):
        text = text.replace(separator, ",")
    parts = [part.strip() for part in text.split(",")]
    if len(parts) != 3:
        raise MonsterCatalogError(
            "Stats must contain HP, Attack, and Defense, for example `500, 470, 460`."
        )
    return (
        parse_stat_value(parts[0], "HP"),
        parse_stat_value(parts[1], "Attack"),
        parse_stat_value(parts[2], "Defense"),
    )


def parse_required_text(value: Any, label: str, *, maximum: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise MonsterCatalogError(f"{label} cannot be empty.")
    if len(text) > maximum:
        raise MonsterCatalogError(f"{label} must be {maximum} characters or fewer.")
    return text


def parse_image_url(value: Any) -> str:
    url = parse_required_text(value, "Image URL", maximum=1000)
    if not url.casefold().startswith(("https://", "http://")):
        raise MonsterCatalogError("Image URL must begin with `https://` or `http://`.")
    return url


def parse_optional_bool(value: Any, label: str) -> Optional[bool]:
    text = str(value or "").strip().casefold()
    if not text or text in {"unset", "remove", "none", "null", "-"}:
        return None
    if text in {"true", "yes", "y", "1", "on"}:
        return True
    if text in {"false", "no", "n", "0", "off"}:
        return False
    raise MonsterCatalogError(f"{label} must be `true`, `false`, or left blank.")


def parse_extra_json(value: Any) -> dict[str, Any]:
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as error:
        raise MonsterCatalogError(f"Extra fields are not valid JSON: {error.msg}.") from error
    if not isinstance(parsed, dict):
        raise MonsterCatalogError("Extra fields must be a JSON object.")
    reserved = sorted(CORE_MONSTER_FIELDS.intersection(parsed))
    if reserved:
        raise MonsterCatalogError(
            "Extra fields cannot replace editor fields: " + ", ".join(reserved)
        )
    return parsed


def validate_monster(monster: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(monster, Mapping):
        raise MonsterCatalogError("A monster record must be a JSON object.")
    validated = dict(monster)
    validated["name"] = parse_required_text(validated.get("name"), "Name", maximum=100)
    validated["hp"] = parse_stat_value(validated.get("hp"), "HP")
    validated["attack"] = parse_stat_value(validated.get("attack"), "Attack")
    validated["defense"] = parse_stat_value(validated.get("defense"), "Defense")
    validated["element"] = parse_required_text(
        validated.get("element"), "Element", maximum=50
    )
    validated["url"] = parse_image_url(validated.get("url"))
    if not isinstance(validated.get("ispublic"), bool):
        raise MonsterCatalogError("Public must be true or false.")
    if "frontier_only" in validated and not isinstance(validated["frontier_only"], bool):
        raise MonsterCatalogError("Frontier only must be true or false when set.")
    region = validated.get("frontier_region_id")
    if region is not None:
        validated["frontier_region_id"] = parse_required_text(
            region, "Frontier region", maximum=100
        )
    return validated


def flatten_catalog(data: Mapping[str, Any]) -> list[MonsterEntry]:
    entries = []
    for tier in sorted(data, key=tier_sort_key):
        monsters = data[tier]
        if not isinstance(monsters, list):
            continue
        for index, monster in enumerate(monsters):
            if not isinstance(monster, dict):
                continue
            entries.append(
                MonsterEntry(
                    locator=MonsterLocator(
                        tier=str(tier),
                        index=index,
                        fingerprint=monster_fingerprint(monster),
                    ),
                    monster=dict(monster),
                )
            )
    return entries


def filter_entries(
    entries: list[MonsterEntry],
    *,
    query: str = "",
    tier: Optional[str] = None,
) -> list[MonsterEntry]:
    needle = str(query or "").strip().casefold()
    tier_text = str(tier) if tier is not None else None
    return [
        entry
        for entry in entries
        if (tier_text is None or entry.tier == tier_text)
        and (not needle or needle in str(entry.monster.get("name") or "").casefold())
    ]


class MonsterCatalogRepository:
    """Read and atomically mutate a tier-keyed monster JSON document."""

    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        self.backup_path = self.path.with_suffix(self.path.suffix + ".bak")
        self._lock = asyncio.Lock()

    def _read_sync(self) -> dict[str, list[dict[str, Any]]]:
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError as error:
            raise MonsterCatalogError(f"Catalog not found: {self.path}") from error
        except json.JSONDecodeError as error:
            raise MonsterCatalogError(
                f"Catalog JSON is invalid at line {error.lineno}, column {error.colno}."
            ) from error
        if not isinstance(data, dict):
            raise MonsterCatalogError("monsters.json must be an object keyed by tier.")
        for tier, monsters in data.items():
            if not isinstance(monsters, list):
                raise MonsterCatalogError(f"Tier {tier!r} must contain a list.")
            if any(not isinstance(monster, dict) for monster in monsters):
                raise MonsterCatalogError(f"Every entry in tier {tier!r} must be an object.")
        return data

    def _write_sync(self, data: Mapping[str, Any]) -> None:
        payload = json.dumps(data, indent=4, ensure_ascii=False) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_path = Path(handle.name)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            if self.path.exists():
                shutil.copy2(self.path, self.backup_path)
            os.replace(temp_path, self.path)
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()

    @staticmethod
    def _locate(data: Mapping[str, Any], locator: MonsterLocator) -> tuple[str, int, dict]:
        monsters = data.get(locator.tier)
        if isinstance(monsters, list) and 0 <= locator.index < len(monsters):
            candidate = monsters[locator.index]
            if isinstance(candidate, dict) and monster_fingerprint(candidate) == locator.fingerprint:
                return locator.tier, locator.index, candidate

        matches = []
        for entry in flatten_catalog(data):
            if entry.locator.fingerprint == locator.fingerprint:
                matches.append(entry)
        if len(matches) == 1:
            match = matches[0]
            return match.tier, match.index, data[match.tier][match.index]
        raise MonsterCatalogConflict(
            "That monster changed after this page loaded. Press Refresh and select it again."
        )

    async def read(self) -> dict[str, list[dict[str, Any]]]:
        async with self._lock:
            return await asyncio.to_thread(self._read_sync)

    async def add(self, tier: Any, monster: Mapping[str, Any]) -> MonsterEntry:
        tier_key = validate_tier(tier)
        record = validate_monster(monster)
        async with self._lock:
            data = await asyncio.to_thread(self._read_sync)
            bucket = data.setdefault(tier_key, [])
            bucket.append(record)
            await asyncio.to_thread(self._write_sync, data)
            index = len(bucket) - 1
            return MonsterEntry(
                MonsterLocator(tier_key, index, monster_fingerprint(record)), dict(record)
            )

    async def update(
        self,
        locator: MonsterLocator,
        tier: Any,
        monster: Mapping[str, Any],
    ) -> MonsterEntry:
        target_tier = validate_tier(tier)
        record = validate_monster(monster)
        async with self._lock:
            data = await asyncio.to_thread(self._read_sync)
            source_tier, source_index, _current = self._locate(data, locator)
            if source_tier == target_tier:
                data[source_tier][source_index] = record
                target_index = source_index
            else:
                data[source_tier].pop(source_index)
                data.setdefault(target_tier, []).append(record)
                target_index = len(data[target_tier]) - 1
            await asyncio.to_thread(self._write_sync, data)
            return MonsterEntry(
                MonsterLocator(target_tier, target_index, monster_fingerprint(record)),
                dict(record),
            )

    async def delete(self, locator: MonsterLocator) -> MonsterEntry:
        async with self._lock:
            data = await asyncio.to_thread(self._read_sync)
            tier, index, current = self._locate(data, locator)
            deleted = dict(current)
            data[tier].pop(index)
            await asyncio.to_thread(self._write_sync, data)
            return MonsterEntry(
                MonsterLocator(tier, index, monster_fingerprint(deleted)), deleted
            )
