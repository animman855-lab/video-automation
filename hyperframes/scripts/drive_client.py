from __future__ import annotations

import json
import os
from pathlib import Path

import requests


REQUIRED_DRIVE_SECRETS = [
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "GOOGLE_REFRESH_TOKEN",
    "GOOGLE_DRIVE_FOLDER_ID",
]


def check_drive_secrets() -> list[str]:
    return [name for name in REQUIRED_DRIVE_SECRETS if not os.getenv(name)]


def _require_drive_secrets() -> dict[str, str]:
    missing = check_drive_secrets()
    if missing:
        raise RuntimeError(f"Missing Google Drive secrets: {', '.join(missing)}")
    return {name: os.environ[name].strip() for name in REQUIRED_DRIVE_SECRETS}


def get_access_token() -> str:
    secrets = _require_drive_secrets()
    response = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": secrets["GOOGLE_CLIENT_ID"],
            "client_secret": secrets["GOOGLE_CLIENT_SECRET"],
            "refresh_token": secrets["GOOGLE_REFRESH_TOKEN"],
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Google OAuth refresh failed: {response.status_code} {response.text}")
    return response.json()["access_token"]


def upload_video_make_public(video_path: Path, name: str) -> str:
    secrets = _require_drive_secrets()
    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}"}
    metadata = {
        "name": name,
        "parents": [secrets["GOOGLE_DRIVE_FOLDER_ID"]],
        "mimeType": "video/mp4",
    }

    with video_path.open("rb") as handle:
        response = requests.post(
            "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id,webViewLink",
            headers=headers,
            files={
                "metadata": ("metadata", json.dumps(metadata), "application/json"),
                "file": (name, handle, "video/mp4"),
            },
            timeout=180,
        )
    if response.status_code >= 400:
        raise RuntimeError(f"Google Drive upload failed: {response.status_code} {response.text}")

    file_id = response.json()["id"]

    permission_response = requests.post(
        f"https://www.googleapis.com/drive/v3/files/{file_id}/permissions",
        headers={**headers, "Content-Type": "application/json"},
        json={"type": "anyone", "role": "reader"},
        timeout=30,
    )
    if permission_response.status_code >= 400:
        raise RuntimeError(
            f"Google Drive permission failed: {permission_response.status_code} {permission_response.text}"
        )

    verify_response = requests.get(
        f"https://www.googleapis.com/drive/v3/files/{file_id}?fields=id,webViewLink,permissions",
        headers=headers,
        timeout=30,
    )
    if verify_response.status_code >= 400:
        raise RuntimeError(f"Google Drive verify failed: {verify_response.status_code} {verify_response.text}")

    data = verify_response.json()
    public = any(
        item.get("type") == "anyone" and item.get("role") == "reader"
        for item in data.get("permissions", [])
    )
    if not public:
        raise RuntimeError("Google Drive file is not public after permission update.")

    return data.get("webViewLink") or f"https://drive.google.com/file/d/{file_id}/view?usp=sharing"
