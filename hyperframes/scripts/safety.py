from __future__ import annotations

from dataclasses import dataclass
import os


class SafetyError(RuntimeError):
    pass


@dataclass(frozen=True)
class PilotLimits:
    max_rows: int = 1
    max_tts_chars: int = 500
    max_images: int = 0
    max_audio_files: int = int(os.getenv("HYPERFRAMES_MAX_ITEMS", "20"))
    max_videos: int = 1


def require_single_row(rows: list[dict], limits: PilotLimits) -> dict:
    if len(rows) != limits.max_rows:
        raise SafetyError(f"Expected exactly {limits.max_rows} Notion row, found {len(rows)}.")
    return rows[0]


def require_at_most_one_row(rows: list[dict]) -> dict | None:
    if len(rows) > 1:
        raise SafetyError(f"Expected at most 1 Notion row, found {len(rows)}.")
    return rows[0] if rows else None


def require_non_empty(value: str, label: str) -> None:
    if not value or not value.strip():
        raise SafetyError(f"{label} is required but empty.")


def require_empty(value: str, label: str) -> None:
    if value and value.strip():
        raise SafetyError(f"{label} must be empty for this dry-run.")


def count_tts_chars(text: str) -> int:
    return len(text.strip())


def require_tts_budget(text: str, limits: PilotLimits) -> int:
    count = count_tts_chars(text)
    if count > limits.max_tts_chars:
        raise SafetyError(f"TTS text is {count} chars; limit is {limits.max_tts_chars}.")
    return count


def require_item_budget(items: list[str], limits: PilotLimits) -> None:
    if not items:
        raise SafetyError("No vocabulary items found.")
    if len(items) > limits.max_audio_files:
        raise SafetyError(f"Found {len(items)} items; limit is {limits.max_audio_files}.")


def require_file_created(path: str, label: str) -> None:
    from pathlib import Path

    candidate = Path(path)
    if not candidate.exists() or candidate.stat().st_size <= 0:
        raise SafetyError(f"{label} was not created or is empty: {path}")
