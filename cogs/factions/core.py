"""Pure faction-standing rules shared by quests, shops, and conversations.

This module deliberately has no Discord or database dependencies.  Callers own
persistence and pass stored point totals into these helpers.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import re


_KEY_SEPARATOR_RE = re.compile(r"[^a-z0-9]+")
_INTEGER_RE = re.compile(r"^[+-]?\d+$")


def normalize_faction_key(value: object) -> str:
    """Return the canonical lowercase snake-case key used for persistence."""

    cleaned = _KEY_SEPARATOR_RE.sub("_", str(value or "").strip().lower())
    return cleaned.strip("_")


def _coerce_integer(value: object, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be an integer, not a boolean.")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        raise ValueError(f"{label} must be a whole number.")
    text = str(value or "").strip()
    if not _INTEGER_RE.fullmatch(text):
        raise ValueError(f"{label} must be an integer.")
    return int(text)


@dataclass(frozen=True, slots=True)
class StandingTier:
    """One inclusive point threshold in a faction's standing ladder."""

    key: str
    name: str
    minimum_points: int
    rank: int

    def __post_init__(self) -> None:
        key = normalize_faction_key(self.key or self.name)
        name = str(self.name or "").strip()
        if not key:
            raise ValueError("Standing tier key must contain letters or numbers.")
        if not name:
            raise ValueError("Standing tier name cannot be empty.")
        object.__setattr__(self, "key", key)
        object.__setattr__(
            self,
            "minimum_points",
            _coerce_integer(self.minimum_points, f"Minimum points for {name}"),
        )
        object.__setattr__(self, "rank", _coerce_integer(self.rank, f"Rank for {name}"))

    @property
    def threshold(self) -> int:
        """Alias useful when rendering faction configuration."""

        return self.minimum_points


DEFAULT_TIERS: tuple[StandingTier, ...] = (
    StandingTier("hated", "Hated", -1000, -3),
    StandingTier("hostile", "Hostile", -500, -2),
    StandingTier("unfriendly", "Unfriendly", -100, -1),
    StandingTier("neutral", "Neutral", 0, 0),
    StandingTier("friendly", "Friendly", 100, 1),
    StandingTier("trusted", "Trusted", 300, 2),
    StandingTier("honored", "Honored", 600, 3),
    StandingTier("revered", "Revered", 1000, 4),
)

# A more explicit name for callers that prefer it; both names are immutable.
DEFAULT_STANDING_TIERS = DEFAULT_TIERS


def _tier_from_mapping(raw: Mapping[str, object]) -> StandingTier:
    name = str(raw.get("name") or "").strip()
    key = normalize_faction_key(raw.get("key") or name)
    if not name and key:
        name = key.replace("_", " ").title()

    threshold_value: object | None = None
    for field in ("minimum_points", "min_points", "threshold", "points"):
        if field in raw:
            threshold_value = raw[field]
            break
    if threshold_value is None:
        raise ValueError(f"Standing tier {key or name or '<unknown>'} needs minimum_points.")
    if "rank" not in raw:
        raise ValueError(f"Standing tier {key or name or '<unknown>'} needs a rank.")
    return StandingTier(key, name, threshold_value, raw["rank"])


def normalize_tiers(
    tiers: Iterable[StandingTier | Mapping[str, object]] | None = None,
) -> tuple[StandingTier, ...]:
    """Normalize, sort, and validate a custom standing ladder.

    Mapping entries accept ``minimum_points`` and the aliases ``min_points``,
    ``threshold``, or ``points``.  Point thresholds and ranks must both rise
    strictly so rank comparisons have the same ordering as point comparisons.
    """

    if tiers is None:
        return DEFAULT_TIERS

    normalized: list[StandingTier] = []
    for raw in tiers:
        if isinstance(raw, StandingTier):
            tier = raw
        elif isinstance(raw, Mapping):
            tier = _tier_from_mapping(raw)
        else:
            raise ValueError("Each standing tier must be a StandingTier or mapping.")
        normalized.append(tier)

    if not normalized:
        raise ValueError("A faction needs at least one standing tier.")
    normalized.sort(key=lambda tier: (tier.minimum_points, tier.rank))

    keys: set[str] = set()
    names: set[str] = set()
    thresholds: set[int] = set()
    ranks: set[int] = set()
    previous_rank: int | None = None
    for tier in normalized:
        name_key = normalize_faction_key(tier.name)
        if tier.key in keys:
            raise ValueError(f"Duplicate standing tier key `{tier.key}`.")
        if name_key in names:
            raise ValueError(f"Duplicate standing tier name `{tier.name}`.")
        if tier.minimum_points in thresholds:
            raise ValueError(f"Duplicate standing threshold {tier.minimum_points}.")
        if tier.rank in ranks:
            raise ValueError(f"Duplicate standing rank {tier.rank}.")
        if previous_rank is not None and tier.rank <= previous_rank:
            raise ValueError("Standing ranks must increase with point thresholds.")
        keys.add(tier.key)
        names.add(name_key)
        thresholds.add(tier.minimum_points)
        ranks.add(tier.rank)
        previous_rank = tier.rank

    aliases: dict[str, StandingTier] = {}
    for tier in normalized:
        for alias in {tier.key, normalize_faction_key(tier.name)}:
            other = aliases.get(alias)
            if other is not None and other != tier:
                raise ValueError(f"Ambiguous standing tier alias `{alias}`.")
            aliases[alias] = tier
    return tuple(normalized)


def tier_for_points(
    points: object,
    tiers: Iterable[StandingTier | Mapping[str, object]] | None = None,
) -> StandingTier:
    """Return the highest tier whose threshold is met by ``points``."""

    point_total = _coerce_integer(points, "Standing points")
    ladder = normalize_tiers(tiers)
    current = ladder[0]
    for tier in ladder[1:]:
        if point_total < tier.minimum_points:
            break
        current = tier
    return current


def rank_for_points(
    points: object,
    tiers: Iterable[StandingTier | Mapping[str, object]] | None = None,
) -> int:
    """Return the signed rank derived from a point total."""

    return tier_for_points(points, tiers).rank


def resolve_tier(
    value: object,
    tiers: Iterable[StandingTier | Mapping[str, object]] | None = None,
) -> StandingTier:
    """Resolve a tier by object, canonical key, display name, or exact rank."""

    ladder = normalize_tiers(tiers)
    if isinstance(value, StandingTier):
        requested_key = value.key
        for tier in ladder:
            if tier.key == requested_key:
                return tier
        raise ValueError(f"Unknown standing tier `{requested_key}`.")

    if isinstance(value, bool):
        raise ValueError("Standing tier cannot be a boolean.")
    if isinstance(value, int):
        requested_rank = value
    else:
        text = str(value or "").strip()
        alias = normalize_faction_key(text)
        for tier in ladder:
            if alias in {tier.key, normalize_faction_key(tier.name)}:
                return tier
        if not _INTEGER_RE.fullmatch(text):
            valid = ", ".join(tier.name for tier in ladder)
            raise ValueError(f"Unknown standing tier `{text}`. Expected one of: {valid}.")
        requested_rank = int(text)

    for tier in ladder:
        if tier.rank == requested_rank:
            return tier
    valid_ranks = ", ".join(str(tier.rank) for tier in ladder)
    raise ValueError(f"Unknown standing rank `{requested_rank}`. Expected one of: {valid_ranks}.")


def resolve_tier_key(
    value: object,
    tiers: Iterable[StandingTier | Mapping[str, object]] | None = None,
) -> str:
    return resolve_tier(value, tiers).key


def resolve_tier_name(
    value: object,
    tiers: Iterable[StandingTier | Mapping[str, object]] | None = None,
) -> str:
    return resolve_tier(value, tiers).name


def resolve_tier_rank(
    value: object,
    tiers: Iterable[StandingTier | Mapping[str, object]] | None = None,
) -> int:
    return resolve_tier(value, tiers).rank


_OPERATOR_ALIASES = {
    "=": "==",
    "==": "==",
    "is": "==",
    "eq": "==",
    "!=": "!=",
    "is_not": "!=",
    "ne": "!=",
    ">": ">",
    "gt": ">",
    ">=": ">=",
    "=>": ">=",
    "ge": ">=",
    "gte": ">=",
    "<": "<",
    "lt": "<",
    "<=": "<=",
    "=<": "<=",
    "le": "<=",
    "lte": "<=",
}
VALID_COMPARISON_OPERATORS = frozenset({"==", "!=", ">", ">=", "<", "<="})


def normalize_comparison_operator(operator: object) -> str:
    """Return a canonical operator or raise instead of silently guessing."""

    raw = str(operator or "").strip().lower()
    try:
        return _OPERATOR_ALIASES[raw]
    except KeyError as exc:
        valid = ", ".join(sorted(VALID_COMPARISON_OPERATORS))
        raise ValueError(f"Unsupported comparison operator `{raw}`. Use one of: {valid}.") from exc


def compare_numbers(actual: object, operator: object, expected: object) -> bool:
    """Compare integer values using a validated operator."""

    left = _coerce_integer(actual, "Actual value")
    right = _coerce_integer(expected, "Expected value")
    normalized_operator = normalize_comparison_operator(operator)
    if normalized_operator == "==":
        return left == right
    if normalized_operator == "!=":
        return left != right
    if normalized_operator == ">":
        return left > right
    if normalized_operator == ">=":
        return left >= right
    if normalized_operator == "<":
        return left < right
    return left <= right


def standing_meets(
    points: object,
    operator: object,
    expected: object,
    *,
    field: str = "points",
    tiers: Iterable[StandingTier | Mapping[str, object]] | None = None,
) -> bool:
    """Evaluate a point, rank, or named-tier standing requirement."""

    normalized_field = str(field or "points").strip().lower()
    if normalized_field == "points":
        return compare_numbers(points, operator, expected)
    if normalized_field == "rank":
        return compare_numbers(rank_for_points(points, tiers), operator, expected)
    if normalized_field in {"tier", "tier_key", "tier_name", "standing"}:
        actual_rank = rank_for_points(points, tiers)
        expected_rank = resolve_tier_rank(expected, tiers)
        return compare_numbers(actual_rank, operator, expected_rank)
    raise ValueError("Standing field must be `points`, `rank`, or `tier`.")


# Readable alias for condition evaluators.
compare_standing = standing_meets


@dataclass(frozen=True, slots=True)
class TierProgress:
    """Progress from the current tier threshold toward the next threshold."""

    points: int
    tier: StandingTier
    next_tier: StandingTier | None
    points_into_tier: int
    points_to_next: int | None
    tier_span: int | None
    fraction: float

    @property
    def is_maximum(self) -> bool:
        return self.next_tier is None

    @property
    def percent(self) -> float:
        return self.fraction * 100.0


def progress_to_next_tier(
    points: object,
    tiers: Iterable[StandingTier | Mapping[str, object]] | None = None,
) -> TierProgress:
    point_total = _coerce_integer(points, "Standing points")
    ladder = normalize_tiers(tiers)
    current = tier_for_points(point_total, ladder)
    index = ladder.index(current)
    if index == len(ladder) - 1:
        return TierProgress(
            points=point_total,
            tier=current,
            next_tier=None,
            points_into_tier=max(0, point_total - current.minimum_points),
            points_to_next=None,
            tier_span=None,
            fraction=1.0,
        )

    next_tier = ladder[index + 1]
    span = next_tier.minimum_points - current.minimum_points
    into_tier = max(0, point_total - current.minimum_points)
    fraction = max(0.0, min(1.0, into_tier / span))
    return TierProgress(
        points=point_total,
        tier=current,
        next_tier=next_tier,
        points_into_tier=into_tier,
        points_to_next=max(0, next_tier.minimum_points - point_total),
        tier_span=span,
        fraction=fraction,
    )


def points_until_tier(
    points: object,
    target: object,
    tiers: Iterable[StandingTier | Mapping[str, object]] | None = None,
) -> int:
    point_total = _coerce_integer(points, "Standing points")
    target_tier = resolve_tier(target, tiers)
    return max(0, target_tier.minimum_points - point_total)


def format_points(points: object, *, signed: bool = False) -> str:
    point_total = _coerce_integer(points, "Standing points")
    number = f"{point_total:+,}" if signed else f"{point_total:,}"
    noun = "point" if abs(point_total) == 1 else "points"
    return f"{number} {noun}"


def format_standing(
    points: object,
    tiers: Iterable[StandingTier | Mapping[str, object]] | None = None,
    *,
    include_points: bool = True,
    include_rank: bool = False,
) -> str:
    point_total = _coerce_integer(points, "Standing points")
    tier = tier_for_points(point_total, tiers)
    label = tier.name
    if include_rank:
        label += f" (rank {tier.rank:+d})"
    if include_points:
        label += f" — {format_points(point_total)}"
    return label


def format_progress(
    points: object,
    tiers: Iterable[StandingTier | Mapping[str, object]] | None = None,
) -> str:
    progress = progress_to_next_tier(points, tiers)
    if progress.is_maximum:
        return f"{progress.tier.name} — maximum standing"
    return (
        f"{progress.tier.name} — {progress.points_into_tier:,}/{progress.tier_span:,} "
        f"toward {progress.next_tier.name} ({progress.points_to_next:,} points remaining)"
    )


def format_standing_change(
    delta: object,
    *,
    before_points: object | None = None,
    after_points: object | None = None,
    faction_name: str | None = None,
    tiers: Iterable[StandingTier | Mapping[str, object]] | None = None,
) -> str:
    """Format a change and mention a tier transition when one occurred."""

    change = _coerce_integer(delta, "Standing change")
    before = None if before_points is None else _coerce_integer(before_points, "Previous points")
    after = None if after_points is None else _coerce_integer(after_points, "New points")
    if before is None and after is not None:
        before = after - change
    elif after is None and before is not None:
        after = before + change

    prefix = f"{str(faction_name).strip()}: " if str(faction_name or "").strip() else ""
    message = prefix + format_points(change, signed=True)
    if after is not None:
        message += f" — {format_standing(after, tiers)}"
    if before is not None and after is not None:
        old_tier = tier_for_points(before, tiers)
        new_tier = tier_for_points(after, tiers)
        if old_tier != new_tier:
            message += f" ({old_tier.name} → {new_tier.name})"
    return message


__all__ = [
    "DEFAULT_STANDING_TIERS",
    "DEFAULT_TIERS",
    "StandingTier",
    "TierProgress",
    "VALID_COMPARISON_OPERATORS",
    "compare_numbers",
    "compare_standing",
    "format_points",
    "format_progress",
    "format_standing",
    "format_standing_change",
    "normalize_comparison_operator",
    "normalize_faction_key",
    "normalize_tiers",
    "points_until_tier",
    "progress_to_next_tier",
    "rank_for_points",
    "resolve_tier",
    "resolve_tier_key",
    "resolve_tier_name",
    "resolve_tier_rank",
    "standing_meets",
    "tier_for_points",
]
