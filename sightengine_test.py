"""Standalone byte-upload test for the Pinterest Cog's Sightengine policy.

Usage:
    python sightengine_test.py path/to/image.jpg
    python sightengine_test.py path/to/image.jpg --json

This script does not import Discord or Pinscrape. It reads the local file into
bytes, uploads those exact bytes with the same models and multipart field name
as the Pinterest Cog, and applies the same fail-closed verdict thresholds.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import math
import mimetypes
import sys

from collections.abc import Mapping
from enum import Enum
from pathlib import Path

import aiohttp

from PIL import Image, UnidentifiedImageError


# -----------------------------------------------------------------------------
# CONFIGURATION: copy the same values used by cogs/pinterest/__init__.py here.
# Do not commit or share the file after inserting real credentials.
# -----------------------------------------------------------------------------
SIGHTENGINE_API_USER = "99324714"
SIGHTENGINE_API_SECRET = "LJJvU9WF6GNAmpuGc8NevZNgWMdFkvFx"

SIGHTENGINE_UNSAFE_THRESHOLD = 0.15
SIGHTENGINE_MIN_SAFE_SCORE = 0.80
SIGHTENGINE_MINOR_THRESHOLD = 0.35

SIGHTENGINE_CHECK_URL = "https://api.sightengine.com/1.0/check.json"
SIGHTENGINE_MODELS = "nudity-2.1,face-age"
MAX_IMAGE_BYTES = 8 * 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 30

SUPPORTED_IMAGE_CONTENT_TYPES = {
    "image/avif",
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
}


class Verdict(Enum):
    SAFE = "safe"
    UNSAFE = "unsafe"
    UNKNOWN = "unknown"


def score(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    if not math.isfinite(parsed) or not 0.0 <= parsed <= 1.0:
        return None
    return parsed


def nested_scores(value: object) -> list[float]:
    scores: list[float] = []
    if isinstance(value, Mapping):
        for name, nested in value.items():
            if name == "none":
                continue
            scores.extend(nested_scores(nested))
    else:
        parsed = score(value)
        if parsed is not None:
            scores.append(parsed)
    return scores


def interpret_response(payload: object) -> Verdict:
    """Apply the exact same conservative policy as the Pinterest Cog."""

    if not isinstance(payload, Mapping) or payload.get("status") != "success":
        return Verdict.UNKNOWN

    nudity = payload.get("nudity")
    faces = payload.get("faces")
    artificial_faces = payload.get("artificial_faces")
    if (
        not isinstance(nudity, Mapping)
        or not isinstance(faces, list)
        or not isinstance(artificial_faces, list)
    ):
        return Verdict.UNKNOWN

    intensity_names = (
        "sexual_activity",
        "sexual_display",
        "erotica",
        "very_suggestive",
        "suggestive",
        "mildly_suggestive",
    )
    intensity_scores = [score(nudity.get(name)) for name in intensity_names]
    safe_score = score(nudity.get("none"))
    suggestive_classes = nudity.get("suggestive_classes")
    if (
        any(value is None for value in intensity_scores)
        or safe_score is None
        or not isinstance(suggestive_classes, Mapping)
    ):
        return Verdict.UNKNOWN

    fine_grained_scores = nested_scores(suggestive_classes)
    if not fine_grained_scores:
        return Verdict.UNKNOWN

    numeric_intensities = [
        value for value in intensity_scores if value is not None
    ]
    if (
        max(numeric_intensities) >= SIGHTENGINE_UNSAFE_THRESHOLD
        or max(fine_grained_scores) >= SIGHTENGINE_UNSAFE_THRESHOLD
        or safe_score < SIGHTENGINE_MIN_SAFE_SCORE
    ):
        return Verdict.UNSAFE

    mildly_suggestive = numeric_intensities[-1]
    for face in faces:
        if not isinstance(face, Mapping):
            return Verdict.UNKNOWN
        attributes = face.get("attributes")
        age = attributes.get("age") if isinstance(attributes, Mapping) else None
        minor = age.get("minor") if isinstance(age, Mapping) else None
        minor_score = score(minor)
        if minor_score is None:
            return Verdict.UNKNOWN
        if (
            minor_score >= SIGHTENGINE_MINOR_THRESHOLD
            and mildly_suggestive >= 0.05
        ):
            return Verdict.UNSAFE

    return Verdict.SAFE


def summarize_scores(payload: object) -> None:
    if not isinstance(payload, Mapping):
        return
    nudity = payload.get("nudity")
    if not isinstance(nudity, Mapping):
        return

    print("\nNudity intensity scores:")
    for name in (
        "sexual_activity",
        "sexual_display",
        "erotica",
        "very_suggestive",
        "suggestive",
        "mildly_suggestive",
        "none",
    ):
        value = score(nudity.get(name))
        print(f"  {name:20} {value if value is not None else 'missing'}")

    suggestive_classes = nudity.get("suggestive_classes")
    if isinstance(suggestive_classes, Mapping):
        ranked = sorted(
            (
                (name, parsed)
                for name, value in suggestive_classes.items()
                if name != "none" and (parsed := score(value)) is not None
            ),
            key=lambda item: item[1],
            reverse=True,
        )
        if ranked:
            print("\nHighest top-level suggestive classes:")
            for name, value in ranked[:8]:
                print(f"  {name:20} {value}")


def inspect_image(path: Path) -> tuple[bytes, str]:
    image_bytes = path.read_bytes()
    if not image_bytes:
        raise ValueError("The image file is empty.")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise ValueError(
            f"The image is {len(image_bytes):,} bytes; the test limit is "
            f"{MAX_IMAGE_BYTES:,} bytes."
        )

    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            image_format = image.format or "unknown"
            dimensions = image.size
            detected_type = Image.MIME.get(image_format)
    except (UnidentifiedImageError, OSError) as error:
        raise ValueError("Pillow could not decode this image.") from error

    guessed_type = mimetypes.guess_type(path.name)[0]
    content_type = (detected_type or guessed_type or "").lower()
    if content_type not in SUPPORTED_IMAGE_CONTENT_TYPES:
        raise ValueError(f"Unsupported image content type: {content_type or 'unknown'}")

    print(f"File:         {path.resolve()}")
    print(f"Bytes read:   {len(image_bytes):,}")
    print(f"SHA-256:      {hashlib.sha256(image_bytes).hexdigest()}")
    print(f"Dimensions:   {dimensions[0]}x{dimensions[1]}")
    print(f"Image format: {image_format}")
    print(f"Content-Type: {content_type}")
    print(f"Models:       {SIGHTENGINE_MODELS}")
    return image_bytes, content_type


async def submit_bytes(image_bytes: bytes, content_type: str) -> dict[str, object]:
    form = aiohttp.FormData()
    form.add_field(
        "media",
        image_bytes,
        filename="pinterest-candidate",
        content_type=content_type,
    )
    form.add_field("models", SIGHTENGINE_MODELS)
    form.add_field("api_user", SIGHTENGINE_API_USER)
    form.add_field("api_secret", SIGHTENGINE_API_SECRET)

    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            SIGHTENGINE_CHECK_URL,
            data=form,
            allow_redirects=False,
        ) as response:
            response_text = await response.text()
            if response.status != 200:
                raise RuntimeError(
                    f"Sightengine returned HTTP {response.status}: "
                    f"{response_text[:500]}"
                )
            try:
                payload = json.loads(response_text)
            except json.JSONDecodeError as error:
                raise RuntimeError("Sightengine returned malformed JSON.") from error

    if not isinstance(payload, dict):
        raise RuntimeError("Sightengine returned a non-object JSON response.")
    return payload


async def run(path: Path, *, show_json: bool) -> int:
    if not SIGHTENGINE_API_USER.strip() or not SIGHTENGINE_API_SECRET.strip():
        raise ValueError(
            "Fill in SIGHTENGINE_API_USER and SIGHTENGINE_API_SECRET at the top "
            "of this script first."
        )
    if not path.is_file():
        raise ValueError(f"Image file does not exist: {path}")

    image_bytes, content_type = inspect_image(path)
    print(f"\nUploading exactly {len(image_bytes):,} bytes to Sightengine...")
    payload = await submit_bytes(image_bytes, content_type)

    request = payload.get("request")
    media = payload.get("media")
    if isinstance(request, Mapping):
        print(f"Request ID:    {request.get('id', 'missing')}")
        print(f"Operations:    {request.get('operations', 'missing')}")
    if isinstance(media, Mapping):
        print(f"Media ID:      {media.get('id', 'missing')}")
        print(f"Media URI:     {media.get('uri', 'missing')}")

    summarize_scores(payload)
    verdict = interpret_response(payload)
    print("\n" + "=" * 56)
    print(f"COG VERDICT: {verdict.value.upper()}")
    print("=" * 56)

    if show_json:
        print("\nFull Sightengine response:")
        print(json.dumps(payload, indent=2, sort_keys=True))

    # SAFE and UNSAFE are both successful, definitive tests. UNKNOWN is a
    # fail-closed provider/schema failure and returns a non-zero exit code.
    return 2 if verdict is Verdict.UNKNOWN else 0


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Upload an image's exact bytes to Sightengine and apply the same "
            "moderation policy as the Pinterest Discord Cog."
        )
    )
    parser.add_argument("image", type=Path, help="Path to a local image file")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the complete Sightengine JSON response",
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    try:
        return asyncio.run(run(arguments.image, show_json=arguments.json))
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
