"""SFW-only Pinterest image search slash commands.

Pinterest searches are performed by the third-party ``pinscrape`` package,
which calls Pinterest's unauthenticated website endpoints. It needs no
Pinterest developer credentials, but it is inherently less stable than an
official API and may stop working if Pinterest changes those endpoints.

For temporary convenience, all editable settings and moderation credentials
live in the ``TEMPORARY CONFIGURATION`` block near the top of this file.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import random
import re
import tempfile
import time
import unicodedata
import warnings

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence
from urllib.parse import quote_plus

import aiohttp
import discord

from discord import app_commands
from discord.ext import commands
from PIL import Image, UnidentifiedImageError
from yarl import URL

try:
    with warnings.catch_warnings():
        # Pinscrape imports its deprecated v1 singleton alongside the v2 client.
        warnings.filterwarnings(
            "ignore",
            message="PinterestImageScraper is deprecated.*",
            category=DeprecationWarning,
        )
        from pinscrape import Pinterest as PinscrapePinterest
except ImportError:  # Keep the rest of the bot loadable before dependencies update.
    PinscrapePinterest = None  # type: ignore[assignment]


LOGGER = logging.getLogger(__name__)


# =============================================================================
# TEMPORARY CONFIGURATION
# Edit values only in this block. Pinterest credentials are no longer needed.
# Do not commit real Sightengine credentials to source control.
# =============================================================================
# Blank uses Pinscrape's browser-like default. Set this only if needed.
PINTEREST_SCRAPER_USER_AGENT = ""
# A small pause makes repeated website requests less aggressive.
PINTEREST_SCRAPER_SLEEP_SECONDS = 1.0

# Required for strict fail-closed SFW image checks.
SIGHTENGINE_API_USER = "99324714"
SIGHTENGINE_API_SECRET = "LJJvU9WF6GNAmpuGc8NevZNgWMdFkvFx"
SIGHTENGINE_UNSAFE_THRESHOLD = 0.15
SIGHTENGINE_MIN_SAFE_SCORE = 0.80
SIGHTENGINE_MINOR_THRESHOLD = 0.35

PINTEREST_CACHE_TTL_SECONDS = 20 * 60
PINTEREST_VIEW_TIMEOUT_SECONDS = 10 * 60
PINTEREST_USER_COOLDOWN_SECONDS = 5.0
PINTEREST_MAX_RESULTS = 50
PINTEREST_MAX_MODERATION_ATTEMPTS = 8
PINTEREST_MIN_IMAGE_BYTES = 16 * 1024
PINTEREST_MIN_IMAGE_WIDTH = 480
PINTEREST_MIN_IMAGE_HEIGHT = 480
PINTEREST_MAX_IMAGE_BYTES = 8 * 1024 * 1024
PINTEREST_MAX_CACHE_ENTRIES = 100
PINTEREST_HTTP_TIMEOUT_SECONDS = 15
PINTEREST_HTTP_CONNECT_TIMEOUT_SECONDS = 5
PINTEREST_HTTP_READ_TIMEOUT_SECONDS = 10

# Add extra exact terms or phrases here. The built-in conservative denylist is
# always enabled as well.
PINTEREST_EXTRA_SFW_DENYLIST: tuple[str, ...] = ()
# =============================================================================
# END TEMPORARY CONFIGURATION
# =============================================================================


PINTEREST_WEB_SEARCH_URL = "https://www.pinterest.com/search/pins/"
SIGHTENGINE_CHECK_URL = "https://api.sightengine.com/1.0/check.json"

SUPPORTED_IMAGE_CONTENT_TYPES = {
    "image/avif",
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
}

IMAGE_FILE_SUFFIXES = {
    "image/avif": ".avif",
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}

# This intentionally focuses on unambiguous adult/sexual terms.
DEFAULT_SFW_DENYLIST = frozenset(
    {
        "18+",
        "bdsm",
        "bondage",
        "boobs",
        "breasts",
        "camgirl",
        "ecchi",
        "erotic",
        "erotica",
        "explicit",
        "fetish",
        "genitals",
        "hentai",
        "kink",
        "lewd",
        "lingerie",
        "lolicon",
        "naked",
        "nsfw",
        "nude",
        "nudity",
        "onlyfans",
        "pinup",
        "porn",
        "pornographic",
        "r34",
        "rule 34",
        "sex",
        "sex toy",
        "sexual",
        "sextoy",
        "shota",
        "striptease",
        "xxx",
    }
)

LEET_TRANSLATION = str.maketrans(
    {
        "0": "o",
        "1": "i",
        "3": "e",
        "4": "a",
        "5": "s",
        "7": "t",
        "@": "a",
        "$": "s",
    }
)


def _config_int(
    name: str, raw_value: object, default: int, minimum: int, maximum: int
) -> int:
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        LOGGER.warning("Ignoring invalid integer configuration for %s", name)
        return default
    return max(minimum, min(value, maximum))


def _config_float(
    name: str, raw_value: object, default: float, minimum: float, maximum: float
) -> float:
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        LOGGER.warning("Ignoring invalid numeric configuration for %s", name)
        return default
    if not math.isfinite(value):
        return default
    return max(minimum, min(value, maximum))


def _normalize_spaces(value: str) -> str:
    return " ".join(value.split())


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as image_file:
        for chunk in iter(lambda: image_file.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_query(value: str) -> str:
    """Normalize a query for cache keys without changing its search meaning."""

    return _normalize_spaces(unicodedata.normalize("NFKC", value)).casefold()


def _safety_tokens(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", value).casefold().translate(
        LEET_TRANSLATION
    )
    return re.findall(r"[a-z0-9+]+", normalized)


def contains_blocked_sfw_term(value: str, denylist: Iterable[str]) -> bool:
    """Return whether text contains a denylisted term as whole token(s)."""

    tokens = _safety_tokens(value)
    if not tokens:
        return False

    for denied in denylist:
        denied_tokens = _safety_tokens(denied)
        if not denied_tokens:
            continue
        width = len(denied_tokens)
        if any(tokens[index : index + width] == denied_tokens for index in range(len(tokens) - width + 1)):
            return True

    # Catch deliberately spaced-out words such as "n s f w" while avoiding
    # substring matches such as "sex" in "Sussex".
    if len(tokens) >= 3 and all(len(token) == 1 for token in tokens):
        collapsed = "".join(tokens)
        return any(collapsed == "".join(_safety_tokens(term)) for term in denylist)

    return False


class PinterestError(RuntimeError):
    """Base class for user-facing Pinterest failures."""


class PinterestUnavailableError(PinterestError):
    """Pinscrape failed or returned an unusable response."""


class SafetyChecksUnavailableError(RuntimeError):
    """All attempted candidates had indeterminate moderation results."""


class ModerationVerdict(Enum):
    SAFE = "safe"
    UNSAFE = "unsafe"
    UNKNOWN = "unknown"


class ImageModerator(Protocol):
    """Replaceable image moderation provider interface."""

    async def moderate(
        self, image_path: Path, content_type: str
    ) -> ModerationVerdict:
        """Classify an image file, returning UNKNOWN on provider failure."""


class SightengineModerator:
    """Sightengine nudity-2.1 and face-age moderation provider."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        api_user: str,
        api_secret: str,
        *,
        unsafe_threshold: float = 0.15,
        minimum_safe_score: float = 0.80,
        minor_threshold: float = 0.35,
    ) -> None:
        self.session = session
        self.api_user = api_user
        self.api_secret = api_secret
        self.unsafe_threshold = unsafe_threshold
        self.minimum_safe_score = minimum_safe_score
        self.minor_threshold = minor_threshold

    @staticmethod
    def _score(value: object) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        score = float(value)
        if not math.isfinite(score) or not 0.0 <= score <= 1.0:
            return None
        return score

    @classmethod
    def _nested_scores(cls, value: object) -> list[float]:
        scores: list[float] = []
        if isinstance(value, Mapping):
            for name, nested in value.items():
                # Several fine-grained groups contain their own safe "none"
                # confidence.  It is not an unsafe signal.
                if name == "none":
                    continue
                scores.extend(cls._nested_scores(nested))
        else:
            score = cls._score(value)
            if score is not None:
                scores.append(score)
        return scores

    def interpret_response(self, payload: object) -> ModerationVerdict:
        """Interpret a Sightengine response conservatively and fail closed."""

        if not isinstance(payload, Mapping) or payload.get("status") != "success":
            return ModerationVerdict.UNKNOWN

        nudity = payload.get("nudity")
        faces = payload.get("faces")
        artificial_faces = payload.get("artificial_faces")
        if (
            not isinstance(nudity, Mapping)
            or not isinstance(faces, list)
            or not isinstance(artificial_faces, list)
        ):
            return ModerationVerdict.UNKNOWN

        intensity_names = (
            "sexual_activity",
            "sexual_display",
            "erotica",
            "very_suggestive",
            "suggestive",
            "mildly_suggestive",
        )
        intensity_scores = [self._score(nudity.get(name)) for name in intensity_names]
        safe_score = self._score(nudity.get("none"))
        suggestive_classes = nudity.get("suggestive_classes")
        if (
            any(score is None for score in intensity_scores)
            or safe_score is None
            or not isinstance(suggestive_classes, Mapping)
        ):
            return ModerationVerdict.UNKNOWN

        fine_grained_scores = self._nested_scores(suggestive_classes)
        if not fine_grained_scores:
            return ModerationVerdict.UNKNOWN

        numeric_intensities = [score for score in intensity_scores if score is not None]
        if (
            max(numeric_intensities) >= self.unsafe_threshold
            or max(fine_grained_scores) >= self.unsafe_threshold
            or safe_score < self.minimum_safe_score
        ):
            return ModerationVerdict.UNSAFE

        # A low-level suggestive signal involving a likely minor is rejected even
        # if it falls below the general threshold.  Artificial faces/illustrations
        # remain covered by nudity-2.1, as face-age deliberately does not age them.
        mildly_suggestive = numeric_intensities[-1]
        for face in faces:
            if not isinstance(face, Mapping):
                return ModerationVerdict.UNKNOWN
            attributes = face.get("attributes")
            age = attributes.get("age") if isinstance(attributes, Mapping) else None
            minor_score = age.get("minor") if isinstance(age, Mapping) else None
            parsed_minor_score = self._score(minor_score)
            if parsed_minor_score is None:
                return ModerationVerdict.UNKNOWN
            if parsed_minor_score >= self.minor_threshold and mildly_suggestive >= 0.05:
                return ModerationVerdict.UNSAFE

        return ModerationVerdict.SAFE

    async def moderate(
        self, image_path: Path, content_type: str
    ) -> ModerationVerdict:
        try:
            file_size = image_path.stat().st_size
            file_sha256 = _file_sha256(image_path)
        except OSError:
            LOGGER.error("Staged Pinterest image disappeared before moderation")
            return ModerationVerdict.UNKNOWN

        LOGGER.info(
            "Uploading staged Pinterest file to Sightengine: bytes=%s sha256=%s "
            "content_type=%s path=%s",
            file_size,
            file_sha256,
            content_type,
            image_path,
        )

        try:
            with image_path.open("rb") as media_file:
                form = aiohttp.FormData()
                form.add_field(
                    "media",
                    media_file,
                    filename=image_path.name,
                    content_type=content_type,
                )
                form.add_field("models", "nudity-2.1,face-age")
                form.add_field("api_user", self.api_user)
                form.add_field("api_secret", self.api_secret)

                async with self.session.post(
                    SIGHTENGINE_CHECK_URL,
                    data=form,
                    allow_redirects=False,
                ) as response:
                    if response.status != 200:
                        LOGGER.error(
                            "Sightengine moderation failed with HTTP %s",
                            response.status,
                        )
                        return ModerationVerdict.UNKNOWN
                    try:
                        payload = await response.json(content_type=None)
                    except (aiohttp.ContentTypeError, ValueError):
                        LOGGER.error("Sightengine returned malformed JSON")
                        return ModerationVerdict.UNKNOWN
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as error:
            LOGGER.error(
                "Sightengine moderation request failed: %s", type(error).__name__
            )
            return ModerationVerdict.UNKNOWN

        verdict = self.interpret_response(payload)
        if verdict is ModerationVerdict.UNKNOWN:
            LOGGER.error("Sightengine returned an incomplete moderation response")
        return verdict


@dataclass(frozen=True, slots=True)
class PinCandidate:
    pin_id: str
    title: str
    image_url: str
    pin_url: str
    raw: dict[str, Any] = field(repr=False, compare=False)

    @property
    def identity(self) -> str:
        return self.pin_id or self.image_url


@dataclass(frozen=True, slots=True)
class StagedPin:
    """A downloaded Pin whose local file is owned by the current operation."""

    candidate: PinCandidate
    image_path: Path
    content_type: str
    width: int
    height: int
    size: int
    sha256: str

    @property
    def attachment_name(self) -> str:
        return self.image_path.name

    def is_unchanged(self) -> bool:
        try:
            return (
                self.image_path.stat().st_size == self.size
                and _file_sha256(self.image_path) == self.sha256
            )
        except OSError:
            return False

    def cleanup(self) -> None:
        try:
            self.image_path.unlink(missing_ok=True)
        except OSError:
            LOGGER.warning(
                "Could not delete staged Pinterest image: %s", self.image_path
            )


@dataclass(slots=True)
class SearchCacheEntry:
    query: str
    candidates: list[PinCandidate]
    created_at: float
    approved: dict[str, PinCandidate] = field(default_factory=dict)
    rejected: set[str] = field(default_factory=set)
    moderation_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class PinterestResultView(discord.ui.View):
    """Owner-only controls for rotating through a cached Pinterest search."""

    def __init__(
        self,
        cog: PinterestCog,
        owner_id: int,
        entry: SearchCacheEntry,
        initial_pin: PinCandidate,
    ) -> None:
        super().__init__(timeout=cog.view_timeout)
        self.cog = cog
        self.owner_id = owner_id
        self.entry = entry
        self.used_ids = {initial_pin.identity}
        self._interaction_lock = asyncio.Lock()

        self.another_button = discord.ui.Button(
            label="Another",
            emoji="🔄",
            style=discord.ButtonStyle.primary,
        )
        self.another_button.callback = self.show_another
        self.add_item(self.another_button)
        self._set_open_button(initial_pin.pin_url)

    def _set_open_button(self, pin_url: str) -> None:
        for child in list(self.children):
            if isinstance(child, discord.ui.Button) and child.style is discord.ButtonStyle.link:
                self.remove_item(child)
        self.add_item(
            discord.ui.Button(
                label="Open Pinterest",
                emoji="📌",
                style=discord.ButtonStyle.link,
                url=pin_url,
            )
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "Only the person who started this search can use this button.",
            ephemeral=True,
        )
        return False

    async def show_another(self, interaction: discord.Interaction) -> None:
        if self._interaction_lock.locked():
            await interaction.response.send_message(
                "I am already finding another safe Pin.", ephemeral=True
            )
            return

        async with self._interaction_lock:
            await interaction.response.defer()
            try:
                staged = await self.cog.select_safe_pin(self.entry, self.used_ids)
            except SafetyChecksUnavailableError:
                await interaction.followup.send(
                    "Image safety checks are currently unavailable. Try again later.",
                    ephemeral=True,
                )
                return
            except Exception:
                LOGGER.exception("Unexpected failure while handling Pinterest Another")
                await interaction.followup.send(
                    "I couldn't load another Pinterest image. Try again later.",
                    ephemeral=True,
                )
                return

            if staged is None:
                await interaction.followup.send(
                    "I couldn't find another image that passed the SFW safety checks.",
                    ephemeral=True,
                )
                return

            pin = staged.candidate
            attachment: discord.File | None = None
            try:
                if not staged.is_unchanged():
                    raise RuntimeError(
                        "Staged Pinterest file changed before Discord upload"
                    )
                attachment = discord.File(
                    staged.image_path, filename=staged.attachment_name
                )
                self.used_ids.add(pin.identity)
                self._set_open_button(pin.pin_url)
                LOGGER.info(
                    "Sending moderated Pinterest file to Discord: bytes=%s "
                    "sha256=%s filename=%s",
                    staged.size,
                    staged.sha256,
                    staged.attachment_name,
                )
                await interaction.edit_original_response(
                    embed=self.cog.build_pin_embed(
                        pin, self.entry.query, staged.attachment_name
                    ),
                    attachments=[attachment],
                    view=self,
                )
            finally:
                try:
                    if attachment is not None:
                        attachment.close()
                finally:
                    staged.cleanup()

    async def on_timeout(self) -> None:
        self.cog._views.discard(self)


class PinterestCog(commands.Cog):
    """Search Pinterest through Pinscrape and post moderated images."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.scraper_user_agent = str(PINTEREST_SCRAPER_USER_AGENT).strip()
        self.scraper_sleep = _config_float(
            "PINTEREST_SCRAPER_SLEEP_SECONDS",
            PINTEREST_SCRAPER_SLEEP_SECONDS,
            1.0,
            0.0,
            30.0,
        )

        configured_terms = {
            str(term).strip()
            for term in PINTEREST_EXTRA_SFW_DENYLIST
            if str(term).strip()
        }
        self.sfw_denylist = DEFAULT_SFW_DENYLIST | configured_terms
        self.cache_ttl = _config_int(
            "PINTEREST_CACHE_TTL_SECONDS",
            PINTEREST_CACHE_TTL_SECONDS,
            20 * 60,
            60,
            24 * 60 * 60,
        )
        self.view_timeout = _config_int(
            "PINTEREST_VIEW_TIMEOUT_SECONDS",
            PINTEREST_VIEW_TIMEOUT_SECONDS,
            10 * 60,
            60,
            60 * 60,
        )
        self.user_cooldown = _config_float(
            "PINTEREST_USER_COOLDOWN_SECONDS",
            PINTEREST_USER_COOLDOWN_SECONDS,
            5.0,
            1.0,
            60.0,
        )
        self.max_results = _config_int(
            "PINTEREST_MAX_RESULTS", PINTEREST_MAX_RESULTS, 50, 1, 50
        )
        self.max_moderation_attempts = _config_int(
            "PINTEREST_MAX_MODERATION_ATTEMPTS",
            PINTEREST_MAX_MODERATION_ATTEMPTS,
            8,
            1,
            20,
        )
        self.min_image_bytes = _config_int(
            "PINTEREST_MIN_IMAGE_BYTES",
            PINTEREST_MIN_IMAGE_BYTES,
            16 * 1024,
            1024,
            1024 * 1024,
        )
        self.min_image_width = _config_int(
            "PINTEREST_MIN_IMAGE_WIDTH",
            PINTEREST_MIN_IMAGE_WIDTH,
            480,
            64,
            4096,
        )
        self.min_image_height = _config_int(
            "PINTEREST_MIN_IMAGE_HEIGHT",
            PINTEREST_MIN_IMAGE_HEIGHT,
            480,
            64,
            4096,
        )
        self.max_image_bytes = _config_int(
            "PINTEREST_MAX_IMAGE_BYTES",
            PINTEREST_MAX_IMAGE_BYTES,
            8 * 1024 * 1024,
            256 * 1024,
            16 * 1024 * 1024,
        )
        self.max_cache_entries = _config_int(
            "PINTEREST_MAX_CACHE_ENTRIES",
            PINTEREST_MAX_CACHE_ENTRIES,
            100,
            1,
            1000,
        )

        self.session: aiohttp.ClientSession | None = None
        self.moderator: ImageModerator | None = None
        self.scraper: Any | None = None
        self._cache: dict[str, SearchCacheEntry] = {}
        self._query_locks: dict[str, asyncio.Lock] = {}
        self._cache_lock = asyncio.Lock()
        self._scraper_lock = asyncio.Lock()
        self._cooldown_lock = asyncio.Lock()
        self._user_cooldowns: dict[int, float] = {}
        self._views: set[PinterestResultView] = set()

    async def cog_load(self) -> None:
        timeout = aiohttp.ClientTimeout(
            total=_config_int(
                "PINTEREST_HTTP_TIMEOUT_SECONDS",
                PINTEREST_HTTP_TIMEOUT_SECONDS,
                15,
                5,
                120,
            ),
            connect=_config_int(
                "PINTEREST_HTTP_CONNECT_TIMEOUT_SECONDS",
                PINTEREST_HTTP_CONNECT_TIMEOUT_SECONDS,
                5,
                1,
                60,
            ),
            sock_read=_config_int(
                "PINTEREST_HTTP_READ_TIMEOUT_SECONDS",
                PINTEREST_HTTP_READ_TIMEOUT_SECONDS,
                10,
                2,
                120,
            ),
        )
        self.session = aiohttp.ClientSession(
            timeout=timeout,
            headers={"User-Agent": "FableReborn-PinterestCog/1.0"},
        )

        if PinscrapePinterest is None:
            LOGGER.error(
                "Pinterest Cog loaded without pinscrape; install requirements.txt"
            )
        else:
            try:
                self.scraper = await asyncio.to_thread(
                    PinscrapePinterest,
                    user_agent=self.scraper_user_agent,
                    sleep_time=self.scraper_sleep,
                )
            except Exception:
                LOGGER.exception("Failed to initialize Pinscrape")

        sightengine_user = str(SIGHTENGINE_API_USER).strip()
        sightengine_secret = str(SIGHTENGINE_API_SECRET).strip()
        if sightengine_user and sightengine_secret:
            self.moderator = SightengineModerator(
                self.session,
                sightengine_user,
                sightengine_secret,
                unsafe_threshold=_config_float(
                    "SIGHTENGINE_UNSAFE_THRESHOLD",
                    SIGHTENGINE_UNSAFE_THRESHOLD,
                    0.15,
                    0.01,
                    0.95,
                ),
                minimum_safe_score=_config_float(
                    "SIGHTENGINE_MIN_SAFE_SCORE",
                    SIGHTENGINE_MIN_SAFE_SCORE,
                    0.80,
                    0.05,
                    0.99,
                ),
                minor_threshold=_config_float(
                    "SIGHTENGINE_MINOR_THRESHOLD",
                    SIGHTENGINE_MINOR_THRESHOLD,
                    0.35,
                    0.05,
                    0.95,
                ),
            )
        else:
            LOGGER.warning(
                "Pinterest Cog loaded without Sightengine credentials; "
                "commands will fail closed"
            )

    async def cog_unload(self) -> None:
        for view in self._views:
            view.stop()
        self._views.clear()
        if self.session is not None and not self.session.closed:
            await self.session.close()

    @staticmethod
    def _allowed_image_url(value: object) -> bool:
        if not isinstance(value, str) or not value:
            return False
        try:
            url = URL(value)
        except ValueError:
            return False
        host = (url.host or "").lower()
        return (
            url.scheme == "https"
            and url.user is None
            and url.password is None
            and (host == "pinimg.com" or host.endswith(".pinimg.com"))
        )

    @staticmethod
    def _image_score(details: Mapping[str, Any], size_name: str) -> int:
        width = details.get("width")
        height = details.get("height")
        if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
            return width * height
        numbers = [int(value) for value in re.findall(r"\d+", size_name)]
        if len(numbers) >= 2:
            return numbers[0] * numbers[1]
        if numbers:
            return numbers[0] * numbers[0]
        return 0

    def _best_image_variant(self, images: object) -> tuple[str, int] | None:
        if not isinstance(images, Mapping):
            return None
        choices: list[tuple[int, str]] = []
        for size_name, details in images.items():
            if not isinstance(size_name, str) or not isinstance(details, Mapping):
                continue
            url = details.get("url")
            if not self._allowed_image_url(url):
                continue
            choices.append((self._image_score(details, size_name), str(url)))
        if not choices:
            return None
        score, url = max(choices, key=lambda choice: choice[0])
        return url, score

    def extract_pin_image(self, pin: Mapping[str, Any]) -> str | None:
        """Return the largest image from a current Pinterest v5 Pin payload."""

        media = pin.get("media")
        if not isinstance(media, Mapping):
            return None
        media_type = media.get("media_type")
        if media_type == "image":
            best = self._best_image_variant(media.get("images"))
            return best[0] if best else None
        if media_type == "multiple_images":
            items = media.get("items")
            if not isinstance(items, list):
                return None
            choices = []
            for item in items:
                if isinstance(item, Mapping):
                    best = self._best_image_variant(item.get("images"))
                    if best:
                        choices.append(best)
            return max(choices, key=lambda choice: choice[1])[0] if choices else None

        # Pure video and mixed image/video Pins are deliberately excluded.
        return None

    def _pin_metadata_is_sfw(self, pin: Mapping[str, Any]) -> bool:
        # This helper also supports richer Pin-shaped payloads supplied by tests
        # or future scraper versions. Image moderation remains authoritative.
        text_values: list[str] = []
        for field_name in ("title", "description", "alt_text"):
            value = pin.get(field_name)
            if isinstance(value, str):
                text_values.append(value)

        media = pin.get("media")
        if isinstance(media, Mapping) and isinstance(media.get("items"), list):
            for item in media["items"]:
                if not isinstance(item, Mapping):
                    continue
                for field_name in ("title", "description"):
                    value = item.get(field_name)
                    if isinstance(value, str):
                        text_values.append(value)

        return not any(
            contains_blocked_sfw_term(value, self.sfw_denylist)
            for value in text_values
        )

    def _candidate_from_pin(self, pin: Mapping[str, Any]) -> PinCandidate | None:
        if not self._pin_metadata_is_sfw(pin):
            return None
        image_url = self.extract_pin_image(pin)
        if image_url is None:
            return None
        raw_pin_id = pin.get("id")
        pin_id = str(raw_pin_id) if raw_pin_id is not None else ""
        if pin_id and not pin_id.isdigit():
            return None
        title_value = pin.get("title")
        title = _normalize_spaces(title_value) if isinstance(title_value, str) else ""
        if not title:
            title = "Pinterest image"
        title = title[:240]
        pin_url = (
            f"https://www.pinterest.com/pin/{pin_id}/"
            if pin_id
            else "https://www.pinterest.com/"
        )
        return PinCandidate(
            pin_id=pin_id,
            title=title,
            image_url=image_url,
            pin_url=pin_url,
            raw=dict(pin),
        )

    def _require_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            raise RuntimeError("Pinterest HTTP session is not available")
        return self.session

    def _candidate_from_scraped_url(
        self, image_url: object, query: str
    ) -> PinCandidate | None:
        """Convert Pinscrape's URL-only result into a display candidate."""

        if not self._allowed_image_url(image_url):
            return None
        normalized_url = str(image_url)
        search_url = f"{PINTEREST_WEB_SEARCH_URL}?q={quote_plus(query)}"
        return PinCandidate(
            pin_id="",
            title=f"{query[:220]} — Pinterest image",
            image_url=normalized_url,
            pin_url=search_url,
            raw={"image_url": normalized_url, "query": query, "source": "pinscrape"},
        )

    async def search_pinterest(self, query: str) -> list[PinCandidate]:
        """Run Pinscrape off-thread and deduplicate its image URL results."""

        scraper = self.scraper
        if scraper is None:
            raise PinterestUnavailableError

        async with self._scraper_lock:
            scrape_task = asyncio.create_task(
                asyncio.to_thread(scraper.search, query, self.max_results)
            )
            try:
                raw_urls = await asyncio.shield(scrape_task)
            except asyncio.CancelledError:
                # The worker thread cannot be force-cancelled. Keep the scraper
                # locked until it finishes so its requests.Session is never used
                # concurrently by a later command.
                await scrape_task
                raise
            except Exception as error:
                LOGGER.error(
                    "Pinscrape search failed: %s", type(error).__name__, exc_info=True
                )
                raise PinterestUnavailableError from error

        if not isinstance(raw_urls, (list, tuple)):
            LOGGER.error("Pinscrape returned a non-list search response")
            raise PinterestUnavailableError

        candidates: list[PinCandidate] = []
        seen: set[str] = set()
        for image_url in raw_urls:
            candidate = self._candidate_from_scraped_url(image_url, query)
            if candidate is None or candidate.identity in seen:
                continue
            seen.add(candidate.identity)
            candidates.append(candidate)
            if len(candidates) >= self.max_results:
                break

        return candidates

    async def _cleanup_cache(self) -> None:
        now = time.monotonic()
        expired_keys = [
            key
            for key, entry in self._cache.items()
            if now - entry.created_at >= self.cache_ttl
        ]
        for key in expired_keys:
            self._cache.pop(key, None)
            self._query_locks.pop(key, None)

        if len(self._cache) > self.max_cache_entries:
            oldest = sorted(
                self._cache.items(), key=lambda item: item[1].created_at
            )[: len(self._cache) - self.max_cache_entries]
            for key, _ in oldest:
                self._cache.pop(key, None)
                self._query_locks.pop(key, None)

    async def get_search_entry(self, query: str) -> SearchCacheEntry:
        cache_key = normalize_query(query)
        async with self._cache_lock:
            await self._cleanup_cache()
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached
            query_lock = self._query_locks.setdefault(cache_key, asyncio.Lock())

        async with query_lock:
            async with self._cache_lock:
                await self._cleanup_cache()
                cached = self._cache.get(cache_key)
                if cached is not None:
                    return cached

            candidates = await self.search_pinterest(query)
            entry = SearchCacheEntry(
                query=query,
                candidates=candidates,
                created_at=time.monotonic(),
            )
            async with self._cache_lock:
                self._cache[cache_key] = entry
                await self._cleanup_cache()
            return entry

    async def _download_candidate_image(
        self, candidate: PinCandidate
    ) -> StagedPin | None:
        image_url = candidate.image_url
        if not self._allowed_image_url(image_url):
            return None
        session = self._require_session()
        scraper_user_agent = str(
            getattr(self.scraper, "user_agent", "") or self.scraper_user_agent
        ).strip()
        image_headers = {
            "User-Agent": scraper_user_agent or "Mozilla/5.0",
            "Referer": "https://www.pinterest.com/",
            "Accept": "image/jpeg,image/png,image/webp,image/avif,image/*;q=0.8",
            # Preserve the media file bytes instead of accepting HTTP-level
            # compression that can make size diagnostics ambiguous.
            "Accept-Encoding": "identity",
        }
        staged_path: Path | None = None
        content_length: int | None = None
        content_encoding = "identity"
        content_type = ""
        total_bytes = 0
        try:
            async with session.get(
                image_url,
                headers=image_headers,
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    LOGGER.warning(
                        "Pinterest image download failed with HTTP %s", response.status
                    )
                    return None
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
                if content_type not in SUPPORTED_IMAGE_CONTENT_TYPES:
                    LOGGER.warning("Pinterest candidate had an unsupported image type")
                    return None
                content_length = response.content_length
                if content_length is not None and content_length > self.max_image_bytes:
                    LOGGER.warning("Pinterest candidate image exceeded the size limit")
                    return None
                content_encoding = response.headers.get("Content-Encoding", "identity")

                suffix = IMAGE_FILE_SUFFIXES[content_type]
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    prefix="fablereborn-pinterest-",
                    suffix=suffix,
                    delete=False,
                ) as staged_file:
                    staged_path = Path(staged_file.name)
                    async for chunk in response.content.iter_chunked(64 * 1024):
                        total_bytes += len(chunk)
                        if total_bytes > self.max_image_bytes:
                            break
                        staged_file.write(chunk)
                    staged_file.flush()
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as error:
            LOGGER.warning(
                "Pinterest image download failed: %s", type(error).__name__
            )
            if staged_path is not None:
                staged_path.unlink(missing_ok=True)
            return None

        if (
            staged_path is None
            or total_bytes == 0
            or total_bytes > self.max_image_bytes
        ):
            LOGGER.warning("Pinterest candidate image was empty or too large")
            if staged_path is not None:
                staged_path.unlink(missing_ok=True)
            return None

        if total_bytes < self.min_image_bytes:
            LOGGER.warning(
                "Rejected suspiciously small Pinterest candidate: %s bytes",
                total_bytes,
            )
            staged_path.unlink(missing_ok=True)
            return None

        try:
            with Image.open(staged_path) as image:
                width, height = image.size
                detected_content_type = Image.MIME.get(image.format or "", "").lower()
                image.verify()
        except (Image.DecompressionBombError, UnidentifiedImageError, OSError):
            LOGGER.warning("Pinterest candidate was not a valid decodable image")
            staged_path.unlink(missing_ok=True)
            return None

        if detected_content_type not in SUPPORTED_IMAGE_CONTENT_TYPES:
            LOGGER.warning("Pinterest candidate had an unsupported decoded image type")
            staged_path.unlink(missing_ok=True)
            return None
        content_type = detected_content_type

        if width < self.min_image_width or height < self.min_image_height:
            LOGGER.warning(
                "Rejected low-resolution Pinterest candidate: %sx%s", width, height
            )
            staged_path.unlink(missing_ok=True)
            return None

        try:
            image_sha256 = _file_sha256(staged_path)
        except OSError:
            LOGGER.warning("Staged Pinterest candidate disappeared after download")
            staged_path.unlink(missing_ok=True)
            return None

        LOGGER.info(
            "Pinterest candidate staged: bytes=%s sha256=%s dimensions=%sx%s "
            "content_type=%s response_content_length=%s content_encoding=%s path=%s",
            total_bytes,
            image_sha256,
            width,
            height,
            content_type,
            content_length,
            content_encoding,
            staged_path,
        )
        return StagedPin(
            candidate=candidate,
            image_path=staged_path,
            content_type=content_type,
            width=width,
            height=height,
            size=total_bytes,
            sha256=image_sha256,
        )

    async def _prepare_candidate(
        self, candidate: PinCandidate
    ) -> tuple[ModerationVerdict, StagedPin | None]:
        """Stage and moderate one candidate, retaining only a safe local file."""

        if self.moderator is None:
            return ModerationVerdict.UNKNOWN, None
        staged = await self._download_candidate_image(candidate)
        if staged is None:
            return ModerationVerdict.UNKNOWN, None

        try:
            verdict = await self.moderator.moderate(
                staged.image_path, staged.content_type
            )
        except BaseException:
            staged.cleanup()
            raise

        if verdict is not ModerationVerdict.SAFE:
            staged.cleanup()
            return verdict, None
        if not staged.is_unchanged():
            LOGGER.error("Staged Pinterest file changed during moderation")
            staged.cleanup()
            return ModerationVerdict.UNKNOWN, None
        return verdict, staged

    async def _moderate_candidate(
        self, candidate: PinCandidate
    ) -> ModerationVerdict:
        verdict, staged = await self._prepare_candidate(candidate)
        if staged is not None:
            staged.cleanup()
        return verdict

    async def is_image_sfw(self, image_url: str, pin: dict[str, Any]) -> bool:
        """Fail-closed public helper for testing/replacing moderation flows."""

        candidate = self._candidate_from_pin(pin)
        if candidate is None or candidate.image_url != image_url:
            return False
        return await self._moderate_candidate(candidate) is ModerationVerdict.SAFE

    @staticmethod
    def choose_random_pin(
        pins: Sequence[PinCandidate], excluded_ids: set[str] | None = None
    ) -> PinCandidate | None:
        excluded = excluded_ids or set()
        available = [pin for pin in pins if pin.identity not in excluded]
        return random.choice(available) if available else None

    async def select_safe_pin(
        self, entry: SearchCacheEntry, used_ids: set[str]
    ) -> StagedPin | None:
        """Return a staged file that passed moderation in this exact operation."""

        async with entry.moderation_lock:
            approved = [
                candidate
                for candidate in entry.approved.values()
                if candidate.identity not in used_ids
            ]
            unreviewed = [
                candidate
                for candidate in entry.candidates
                if candidate.identity not in entry.approved
                and candidate.identity not in entry.rejected
                and candidate.identity not in used_ids
            ]
            previously_used = [
                candidate
                for candidate in entry.approved.values()
                if candidate.identity in used_ids
            ]
            random.shuffle(approved)
            random.shuffle(unreviewed)
            random.shuffle(previously_used)

            # Known-safe URLs are tried first, but their newly downloaded file is
            # always moderated again. Previously used results are only a fallback
            # after unused and unreviewed options have been exhausted.
            attempts = (approved + unreviewed + previously_used)[
                : self.max_moderation_attempts
            ]
            saw_unknown = False
            saw_definitive = False
            for candidate in attempts:
                verdict, staged = await self._prepare_candidate(candidate)
                if verdict is ModerationVerdict.SAFE:
                    if staged is None:
                        saw_unknown = True
                        continue
                    entry.approved[candidate.identity] = candidate
                    if candidate.identity in used_ids:
                        used_ids.clear()
                    return staged
                if verdict is ModerationVerdict.UNSAFE:
                    entry.approved.pop(candidate.identity, None)
                    entry.rejected.add(candidate.identity)
                    saw_definitive = True
                else:
                    saw_unknown = True

            if saw_unknown and not saw_definitive:
                raise SafetyChecksUnavailableError
            return None

    @staticmethod
    def build_pin_embed(
        pin: PinCandidate, query: str, attachment_name: str
    ) -> discord.Embed:
        embed = discord.Embed(
            title=f"📌 {pin.title}",
            url=pin.pin_url,
            description=f'Search: "{query[:200]}"',
            colour=discord.Colour.from_rgb(230, 0, 35),
        )
        embed.set_image(url=f"attachment://{attachment_name}")
        embed.set_footer(text="Pinterest • SFW checked")
        return embed

    def _configuration_error(self) -> str | None:
        if self.scraper is None:
            return (
                "Pinterest scraping is unavailable. The bot owner must install "
                "the dependencies from requirements.txt and reload this Cog."
            )
        if self.moderator is None:
            return (
                "Image safety checks are not configured. Add SIGHTENGINE_API_USER "
                "and SIGHTENGINE_API_SECRET to the temporary configuration block "
                "in cogs/pinterest/__init__.py."
            )
        return None

    async def _claim_user_cooldown(self, user_id: int) -> float:
        """Apply one shared cooldown across both slash-command names."""

        cooldown_seconds = self.user_cooldown
        now = time.monotonic()
        async with self._cooldown_lock:
            stale_ids = [
                cached_user_id
                for cached_user_id, last_used in self._user_cooldowns.items()
                if now - last_used >= 60.0
            ]
            for cached_user_id in stale_ids:
                self._user_cooldowns.pop(cached_user_id, None)

            last_used = self._user_cooldowns.get(user_id)
            if last_used is not None:
                retry_after = cooldown_seconds - (now - last_used)
                if retry_after > 0:
                    return retry_after
            self._user_cooldowns[user_id] = now
            return 0.0

    async def _run_search_command(
        self, interaction: discord.Interaction, query: str
    ) -> None:
        cleaned_query = _normalize_spaces(query)
        if not cleaned_query or len(cleaned_query) > 100:
            await interaction.response.send_message(
                "Please provide a search query between 1 and 100 characters.",
                ephemeral=True,
            )
            return
        retry_after = await self._claim_user_cooldown(interaction.user.id)
        if retry_after > 0:
            await interaction.response.send_message(
                f"Please wait {retry_after:.1f}s before searching again.",
                ephemeral=True,
            )
            return
        if contains_blocked_sfw_term(cleaned_query, self.sfw_denylist):
            await interaction.response.send_message(
                "That search is not allowed because this Pinterest command is SFW-only.",
                ephemeral=True,
            )
            return
        configuration_error = self._configuration_error()
        if configuration_error:
            await interaction.response.send_message(configuration_error, ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        try:
            entry = await self.get_search_entry(cleaned_query)
            if not entry.candidates:
                await interaction.followup.send(
                    f'I couldn\'t find any usable Pinterest images for "{cleaned_query}".',
                    ephemeral=True,
                )
                return
            staged = await self.select_safe_pin(entry, set())
        except PinterestUnavailableError:
            await interaction.followup.send(
                "Pinterest scraping is currently unavailable. Try again later.",
                ephemeral=True,
            )
            return
        except SafetyChecksUnavailableError:
            await interaction.followup.send(
                "Image safety checks are currently unavailable. Try again later.",
                ephemeral=True,
            )
            return
        except Exception:
            LOGGER.exception("Unexpected Pinterest command failure")
            await interaction.followup.send(
                "I couldn't complete that Pinterest search. Try again later.",
                ephemeral=True,
            )
            return

        if staged is None:
            await interaction.followup.send(
                f'I found Pins for "{cleaned_query}", but none passed the SFW safety checks.',
                ephemeral=True,
            )
            return

        pin = staged.candidate
        view = PinterestResultView(self, interaction.user.id, entry, pin)
        self._views.add(view)
        attachment: discord.File | None = None
        try:
            if not staged.is_unchanged():
                raise RuntimeError(
                    "Staged Pinterest file changed before Discord upload"
                )
            attachment = discord.File(
                staged.image_path, filename=staged.attachment_name
            )
            LOGGER.info(
                "Sending moderated Pinterest file to Discord: bytes=%s sha256=%s "
                "filename=%s",
                staged.size,
                staged.sha256,
                staged.attachment_name,
            )
            await interaction.followup.send(
                embed=self.build_pin_embed(
                    pin, cleaned_query, staged.attachment_name
                ),
                file=attachment,
                view=view,
                wait=True,
            )
        except BaseException:
            view.stop()
            self._views.discard(view)
            raise
        finally:
            try:
                if attachment is not None:
                    attachment.close()
            finally:
                staged.cleanup()

    @app_commands.command(
        name="pinterest", description="Search Pinterest and return a random SFW image."
    )
    @app_commands.describe(query="What to search Pinterest for")
    async def pinterest(self, interaction: discord.Interaction, query: str) -> None:
        await self._run_search_command(interaction, query)

    @app_commands.command(
        name="pin", description="Search Pinterest and return a random SFW image."
    )
    @app_commands.describe(query="What to search Pinterest for")
    async def pin(self, interaction: discord.Interaction, query: str) -> None:
        await self._run_search_command(interaction, query)

    async def _handle_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.CommandOnCooldown):
            message = f"Please wait {error.retry_after:.1f}s before searching again."
        else:
            LOGGER.error(
                "Pinterest application command error",
                exc_info=(type(error), error, error.__traceback__),
            )
            message = "I couldn't complete that Pinterest search. Try again later."

        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    @pinterest.error
    async def pinterest_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        await self._handle_command_error(interaction, error)

    @pin.error
    async def pin_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        await self._handle_command_error(interaction, error)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PinterestCog(bot))
