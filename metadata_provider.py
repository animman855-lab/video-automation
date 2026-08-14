"""Provider routing for publication metadata.

The publisher uses one request per video. Providers are tried in order and a
deterministic local result is used when no remote provider is available.
"""

from __future__ import annotations

import json
import os
import re
from typing import Callable, Iterable

import requests


DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"
DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"
METADATA_KEYS = (
    "YOUTUBE_TITLE",
    "YOUTUBE_DESCRIPTION",
    "TIKTOK_TITLE",
    "TIKTOK_DESCRIPTION",
    "INSTAGRAM_TITLE",
    "INSTAGRAM_DESCRIPTION",
    "FACEBOOK_TITLE",
    "FACEBOOK_DESCRIPTION",
    "PINTEREST_TITLE",
    "PINTEREST_DESCRIPTION",
)


def _required_keys(platforms: Iterable[str]) -> list[str]:
    return [
        f"{platform.upper()}_{suffix}"
        for platform in platforms
        for suffix in ("TITLE", "DESCRIPTION")
    ]


def _clean_response_text(text: str) -> str:
    cleaned = (text or "").strip()
    if cleaned.startswith("```") and cleaned.endswith("```"):
        cleaned = re.sub(r"^```(?:json|text)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def parse_metadata_response(text: str, keys: Iterable[str] = METADATA_KEYS) -> dict[str, str]:
    """Parse the existing KEY: value format, with JSON support as a fallback."""

    cleaned = _clean_response_text(text)
    try:
        parsed = json.loads(cleaned)
    except (TypeError, ValueError):
        parsed = None
    if isinstance(parsed, dict):
        return {key: str(parsed.get(key, "") or "").strip() for key in keys}

    result: dict[str, str] = {}
    known_keys = tuple(keys)
    current_key = None
    current_lines: list[str] = []
    for line in cleaned.splitlines():
        matched_key = next(
            (key for key in known_keys if line.strip().startswith(f"{key}:")),
            None,
        )
        if matched_key:
            if current_key:
                result[current_key] = "\n".join(current_lines).strip()
            current_key = matched_key
            current_lines = [line.split(":", 1)[1].strip()]
        elif current_key:
            current_lines.append(line)
    if current_key:
        result[current_key] = "\n".join(current_lines).strip()
    return result


def _has_required_values(result: dict[str, str], required_keys: Iterable[str]) -> bool:
    return all(result.get(key, "").strip() for key in required_keys)


def _call_groq(prompt: str) -> str:
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not configured")
    response = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": os.getenv("GROQ_MODEL", DEFAULT_GROQ_MODEL),
            "messages": [
                {
                    "role": "system",
                    "content": "Return only the requested metadata format. Do not add commentary.",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.4,
            "max_tokens": 2500,
        },
        timeout=45,
    )
    response.raise_for_status()
    payload = response.json()
    return payload["choices"][0]["message"]["content"]


def _call_gemini(prompt: str) -> str:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")
    model = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        params={"key": api_key},
        headers={"Content-Type": "application/json"},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.4,
                "maxOutputTokens": 2500,
            },
        },
        timeout=45,
    )
    response.raise_for_status()
    payload = response.json()
    return payload["candidates"][0]["content"]["parts"][0]["text"]


def request_metadata(
    prompt: str,
    required_keys: Iterable[str],
    deterministic_fallback: Callable[[], dict[str, str]],
    label: str,
) -> dict[str, str]:
    """Try Groq, then Gemini, then local deterministic metadata."""

    required = list(required_keys)
    for provider, call in (("Groq", _call_groq), ("Gemini", _call_gemini)):
        try:
            result = parse_metadata_response(call(prompt), required)
            if not _has_required_values(result, required):
                raise ValueError("provider response is missing one or more required fields")
            print(f"  Metadata provider: {provider} ({label})")
            return result
        except Exception as exc:
            print(f"  Metadata provider {provider} unavailable: {type(exc).__name__}: {exc}")

    print(f"  Metadata provider: deterministic local fallback ({label})")
    result = deterministic_fallback()
    if not _has_required_values(result, required):
        raise RuntimeError("deterministic metadata fallback did not produce all required fields")
    return result


def _topic_from_script(script: str) -> str:
    text = re.sub(r"\s+", " ", (script or "")).strip()
    text = re.sub(r"^(?:script|cta|hook)\s*:\s*", "", text, flags=re.IGNORECASE)
    words = text.split()
    return " ".join(words[:9]).rstrip(".,!?\"") or "real conversations"


def deterministic_metadata(
    script: str,
    avatar: str,
    platforms: Iterable[str],
    hashtags: Iterable[str],
    youtube_hashtags: Iterable[str] = (),
    app_focused: bool = False,
) -> dict[str, str]:
    """Create valid, predictable metadata without any model/API."""

    topic = _topic_from_script(script)
    avatar_name = avatar.strip().title() or "Saloo English"
    tags = " ".join(dict.fromkeys(tag.strip() for tag in hashtags if tag.strip()))
    youtube_tags = " ".join(dict.fromkeys(tag.strip() for tag in youtube_hashtags if tag.strip()))
    if app_focused:
        base_title = f"Practice English with Saloo English: {topic}"
        base_description = (
            f"Practice useful English with Saloo English. {topic}. "
            "Build speaking confidence with short, real-life practice.\n\n"
            f"{tags}"
        )
    else:
        base_title = f"Practice English: {topic}"
        base_description = (
            f"Practice useful English with {avatar_name}. {topic}. "
            "Improve your speaking and listening through real-life English.\n\n"
            f"{tags}"
        )

    result: dict[str, str] = {}
    for platform in platforms:
        key = platform.upper()
        title = base_title
        if key == "YOUTUBE" and youtube_tags:
            title = f"{title} {youtube_tags}"
        result[f"{key}_TITLE"] = title[:140].rstrip()
        result[f"{key}_DESCRIPTION"] = base_description[:4800].rstrip()
    return result
