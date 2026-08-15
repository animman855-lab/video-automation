"""Deterministic hashtag and title normalization before Upload-Post."""

from __future__ import annotations

import re


CORE_HASHTAGS = [
    "#learnenglish",
    "#englishvocabulary",
    "#englishspeakingpractice",
    "#english",
]
YOUTUBE_TITLE_HASHTAGS = ["#english", "#learnenglish", "#englishlearning"]


def _avatar_hashtag(avatar: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "", (avatar or "").lower())
    return f"#{value}" if value else "#english"


def _unique_tags(tags: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        clean = tag.strip()
        if not clean.startswith("#"):
            continue
        key = clean.lower()
        if key not in seen:
            seen.add(key)
            result.append(clean)
    return result


def _extract_tags(text: str) -> list[str]:
    return _unique_tags(re.findall(r"(?<![\w])#[\w]+", text or ""))


def ensure_description_hashtags(
    description: str,
    avatar: str,
    max_chars: int | None = None,
) -> str:
    """Guarantee core/avatar tags while preserving a few relevant AI tags."""

    clean = re.sub(r"\s+", " ", (description or "").strip())
    existing = _extract_tags(clean)
    body = re.sub(r"(?<![\w])#[\w]+", "", clean)
    body = re.sub(r"\s+", " ", body).strip()
    required = _unique_tags(CORE_HASHTAGS + [_avatar_hashtag(avatar)])
    tags = _unique_tags(required + existing)
    if max_chars is not None:
        extras = [tag for tag in tags if tag.lower() not in {item.lower() for item in required}]
        tags = required + extras[:3]

    if max_chars is None:
        return f"{body}\n\n{' '.join(tags)}".strip()

    tag_line = " ".join(tags)
    separator = "\n\n" if body else ""
    body_budget = max_chars - len(separator) - len(tag_line)
    if body_budget <= 0:
        return tag_line[:max_chars]

    if len(body) > body_budget:
        if body_budget <= 4:
            body = body[:body_budget]
        else:
            body = body[: body_budget - 3].rstrip().rsplit(" ", 1)[0].rstrip() + "..."
    return f"{body}{separator}{tag_line}".strip()


def _title_with_reserved_tags(title: str, tags: list[str], max_chars: int) -> str:
    clean = re.sub(r"(?<![\w])#[\w]+", "", title or "")
    clean = re.sub(r"\s+", " ", clean).strip(" -|")
    tag_line = " ".join(_unique_tags(tags))
    body_budget = max_chars - len(tag_line) - 1
    if body_budget < 1:
        return tag_line[:max_chars]
    if len(clean) > body_budget:
        clean = clean[: max(1, body_budget - 3)].rstrip()
        if " " in clean:
            clean = clean.rsplit(" ", 1)[0]
        clean = clean.rstrip(" -|…") + "..."
    return f"{clean} {tag_line}".strip()


def title_with_hashtags(title: str, tags: list[str], max_chars: int) -> str:
    """Keep all reserved hashtags while trimming only the title body."""

    return _title_with_reserved_tags(title, tags, max_chars)


def tiktok_title_with_hashtags(title: str, avatar: str, max_chars: int = 150) -> str:
    return title_with_hashtags(title, CORE_HASHTAGS[:3] + [_avatar_hashtag(avatar)], max_chars)


def prepare_video_metadata(
    title: str,
    description: str,
    avatar: str,
    platform: str,
) -> tuple[str, str]:
    """Return the exact title/description to send to Upload-Post."""

    key = (platform or "").lower()
    avatar_tag = _avatar_hashtag(avatar)
    if key == "tiktok":
        return tiktok_title_with_hashtags(title, avatar, 150), ""
    if key == "youtube":
        return title_with_hashtags(title, YOUTUBE_TITLE_HASHTAGS, 100), ensure_description_hashtags(
            description, avatar, 5000
        )
    if key == "pinterest":
        return title, ensure_description_hashtags(description, avatar, 440)
    limits = {"facebook": 500, "instagram": 2200}
    return title, ensure_description_hashtags(description, avatar, limits.get(key))


def final_hashtags(title: str, description: str) -> list[str]:
    return _unique_tags(_extract_tags(title) + _extract_tags(description))


def validate_prepared_metadata(
    title: str,
    description: str,
    avatar: str,
    platform: str,
) -> None:
    """Fail closed if the final payload is missing required metadata."""

    if not (title or "").strip():
        raise ValueError(f"{platform}: title is empty")

    key = (platform or "").lower()
    if key == "tiktok":
        required = CORE_HASHTAGS[:3] + [_avatar_hashtag(avatar)]
        haystack = title.lower()
        missing = [tag for tag in required if tag.lower() not in haystack]
        if missing:
            raise ValueError(f"TikTok title is missing hashtags: {' '.join(missing)}")
        if (description or "").strip():
            raise ValueError("TikTok description must be empty")
        return

    if key == "youtube":
        title_missing = [tag for tag in YOUTUBE_TITLE_HASHTAGS if tag.lower() not in title.lower()]
        if title_missing:
            raise ValueError(f"YouTube title is missing hashtags: {' '.join(title_missing)}")

    required_description = CORE_HASHTAGS + [_avatar_hashtag(avatar)]
    description_lower = (description or "").lower()
    missing = [tag for tag in required_description if tag.lower() not in description_lower]
    if missing:
        raise ValueError(f"{platform} description is missing hashtags: {' '.join(missing)}")
