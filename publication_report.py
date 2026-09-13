"""Small, local, non-secret publication report for per-platform outcomes."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_SECRET_PATTERNS = (
    (re.compile(r"(?i)(Apikey|Bearer)\s+[^\s,;]+"), r"\1 [REDACTED]"),
    (re.compile(r"(?i)(api[_-]?key|token|password|secret)\s*[=:]\s*[^\s,;]+"), r"\1=[REDACTED]"),
)


def sanitize_error(value: Any) -> str:
    text = str(value or "").strip()
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text[:1000]


def report_path() -> Path:
    configured = os.getenv("PUBLISH_REPORT_PATH", "publish-report.jsonl").strip()
    return Path(configured or "publish-report.jsonl")


def _platform_result(result: dict, platform_key: str) -> dict:
    nested = result.get("results", {}).get(platform_key)
    return nested if isinstance(nested, dict) else result


def upload_result_succeeded(result: Any, platform_key: str) -> bool:
    if not isinstance(result, dict):
        return False
    if result.get("success") is False or result.get("status") == "failed":
        return False
    nested = result.get("results", {}).get(platform_key)
    if isinstance(nested, dict):
        return nested.get("success") is True and nested.get("status") != "failed"
    return result.get("success") is True


def result_details(result: Any, platform_key: str, success: bool) -> dict:
    """Keep useful provider identifiers without persisting raw responses."""

    if not isinstance(result, dict):
        return {
            "success": False,
            "error_type": "invalid_upload_response",
            "error": "Upload provider returned a non-object response",
        }

    nested = _platform_result(result, platform_key)
    details = {"success": bool(success)}
    for output_key, source_keys in {
        "request_id": ("request_id", "requestId"),
        "platform_post_id": ("platform_post_id", "post_id", "platformPostId"),
        "post_url": ("post_url", "url", "postUrl"),
        "provider_status": ("status",),
    }.items():
        for source_key in source_keys:
            value = nested.get(source_key) or result.get(source_key)
            if value:
                details[output_key] = str(value)[:500]
                break

    if not success:
        details["error_type"] = "provider_failure"
        details["error"] = sanitize_error(
            nested.get("message")
            or nested.get("error")
            or result.get("message")
            or result.get("error")
            or result.get("status")
            or "Upload provider reported failure"
        )
    return details


def append_publication_report(
    *,
    page_id: str,
    avatar: str,
    title: str,
    video_type: str,
    requested_platforms: list[str],
    outcomes: dict[str, dict],
    final_status: str,
    notion_status_error: str | None = None,
) -> Path:
    path = report_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "notion_page_id": page_id,
        "avatar": avatar,
        "title": title,
        "video_type": video_type,
        "requested_platforms": requested_platforms,
        "platforms": outcomes,
        "successful_platforms": [name for name, item in outcomes.items() if item.get("success")],
        "failed_platforms": [name for name, item in outcomes.items() if not item.get("success")],
        "final_notion_status": final_status,
    }
    if notion_status_error:
        payload["notion_status_error"] = sanitize_error(notion_status_error)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def final_status_for_successes(successes: int, requested_platforms: int) -> tuple[str, str]:
    """Return the locked terminal state for a completed platform attempt."""

    if successes <= 0:
        return "Echec", "no_platform_succeeded"
    if successes == 1:
        return "Partiel", "one_platform_succeeded"
    if successes >= 2:
        return "Publie", "minimum_success_threshold_reached"
    return "Publie", "publication_succeeded"
