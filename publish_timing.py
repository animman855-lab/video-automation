"""Shared timing and per-run slot coordination for publishing scripts."""

import json
import os
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

import pytz


TORONTO = pytz.timezone("America/Toronto")
SLOT_HOURS = {
    "08:00": 8 * 60,
    "02:00": 2 * 60,
    "10:00": 10 * 60,
    "12:00": 12 * 60,
    "14:00": 14 * 60,
    "16:00": 16 * 60,
    "18:00": 18 * 60,
    "20:00": 20 * 60,
    "22:00": 22 * 60,
    "00:00": 0,
}
LATE_WINDOW_MINUTES = 180


def toronto_now() -> datetime:
    return datetime.now(TORONTO)


def queryable_dates(now: datetime | None = None) -> list[str]:
    """Include yesterday while a previous-day 22:00 catch-up is possible."""
    current = now or toronto_now()
    dates = [current.strftime("%Y-%m-%d")]
    if current.hour * 60 + current.minute <= LATE_WINDOW_MINUTES:
        dates.append((current.date() - timedelta(days=1)).isoformat())
    return dates


def slot_is_due(
    slot_name: str,
    publication_date: str | None = None,
    now: datetime | None = None,
) -> bool:
    current = now or toronto_now()
    slot_minutes = SLOT_HOURS.get(slot_name)
    if slot_minutes is None:
        return False

    row_date = date.fromisoformat(publication_date) if publication_date else current.date()
    current_date = current.date()
    current_minutes = current.hour * 60 + current.minute

    if row_date == current_date:
        diff = current_minutes - slot_minutes
        return 0 <= diff <= LATE_WINDOW_MINUTES

    if row_date == current_date - timedelta(days=1):
        elapsed = current_minutes + 24 * 60 - slot_minutes
        return 0 <= elapsed <= LATE_WINDOW_MINUTES

    return False


def slot_key(publication_date: str, slot_name: str) -> str:
    return f"{publication_date}|{slot_name}"


def slot_sort_value(publication_date: str, slot_name: str) -> datetime:
    slot_minutes = SLOT_HOURS.get(slot_name, 10**6)
    return datetime.fromisoformat(publication_date) + timedelta(minutes=slot_minutes)


def _lock_path() -> Path:
    configured = os.getenv("PUBLISH_SLOT_LOCK_FILE", "").strip()
    if configured:
        return Path(configured)
    run_id = os.getenv("GITHUB_RUN_ID", "local")
    return Path(tempfile.gettempdir()) / f"saloo-publish-slot-lock-{run_id}.json"


def read_slot_lock() -> str | None:
    path = _lock_path()
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("slot_key")
    except (FileNotFoundError, json.JSONDecodeError, OSError, AttributeError):
        return None


def claim_slot(slot_value: str) -> None:
    path = _lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"slot_key": slot_value}), encoding="utf-8")
