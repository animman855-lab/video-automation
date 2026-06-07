from __future__ import annotations

import os
from pathlib import Path

import requests


NOTION_VERSION = "2022-06-28"
DEFAULT_VIDEO_PUBLISHING_DATABASE_ID = "909c1124-2b0c-48f3-938e-c0521b9d7bb2"


class NotionError(RuntimeError):
    pass


def load_local_env(repo_root: Path) -> None:
    env_path = repo_root / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _headers() -> dict[str, str]:
    token = os.getenv("NOTION_TOKEN")
    if not token:
        raise NotionError("Missing NOTION_TOKEN.")

    return {
        "Authorization": f"Bearer {token.strip()}",
        "Notion-Version": os.getenv("NOTION_VERSION", NOTION_VERSION),
        "Content-Type": "application/json",
    }


def get_database_id() -> str:
    database_id = os.getenv("NOTION_DATABASE_ID") or DEFAULT_VIDEO_PUBLISHING_DATABASE_ID
    return database_id.strip()


def query_teacher_ryan_animals_pilot() -> list[dict]:
    database_id = get_database_id()
    payload = {
        "filter": {
            "and": [
                {"property": "Avatar", "select": {"equals": "teacherryan"}},
                {"property": "Date Publication", "date": {"equals": "2026-06-07"}},
                {"property": "Slot", "select": {"equals": "08:00"}},
                {"property": "Video Type", "select": {"equals": "HyperFrames"}},
                {"property": "Statut", "select": {"equals": "En cours"}},
            ]
        },
        "page_size": 5,
    }

    response = requests.post(
        f"https://api.notion.com/v1/databases/{database_id}/query",
        headers=_headers(),
        json=payload,
        timeout=30,
    )
    if response.status_code >= 400:
        raise NotionError(f"Notion query failed: {response.status_code} {response.text}")

    return response.json().get("results", [])


def patch_page(page_id: str, properties: dict) -> dict:
    response = requests.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers=_headers(),
        json={"properties": properties},
        timeout=30,
    )
    if response.status_code >= 400:
        raise NotionError(f"Notion update failed: {response.status_code} {response.text}")
    return response.json()


def set_ready_to_publish(page_id: str, drive_url: str) -> dict:
    return patch_page(
        page_id,
        {
            "Lien Video": {"url": drive_url},
            "Statut": {"select": {"name": "A publier"}},
        },
    )


def prop_text(props: dict, name: str) -> str:
    prop = props.get(name)
    if not prop:
        return ""

    prop_type = prop.get("type")
    if prop_type == "title":
        return "".join(item.get("plain_text", "") for item in prop.get("title", []))
    if prop_type == "rich_text":
        return "".join(item.get("plain_text", "") for item in prop.get("rich_text", []))
    if prop_type == "url":
        return prop.get("url") or ""
    if prop_type == "select":
        value = prop.get("select")
        return value.get("name", "") if value else ""
    if prop_type == "date":
        value = prop.get("date")
        return value.get("start", "") if value else ""

    return ""
