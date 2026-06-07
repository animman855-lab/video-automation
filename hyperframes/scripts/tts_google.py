from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import requests


REQUIRED_TTS_SECRETS = ["GOOGLE_TTS_CREDENTIALS_JSON"]


def check_tts_secrets() -> list[str]:
    return [name for name in REQUIRED_TTS_SECRETS if not os.getenv(name)]


def _access_token() -> str:
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    missing = check_tts_secrets()
    if missing:
        raise RuntimeError(f"Missing Google TTS secrets: {', '.join(missing)}")

    info = json.loads(os.environ["GOOGLE_TTS_CREDENTIALS_JSON"])
    credentials = service_account.Credentials.from_service_account_info(
        info,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    credentials.refresh(Request())
    return credentials.token


def synthesize_words(words: list[str], output_path: Path) -> Path:
    if not words:
        raise RuntimeError("Refusing to synthesize an empty word list.")

    ssml = "<speak>" + '<break time="900ms"/>'.join(words) + '<break time="1200ms"/></speak>'
    response = requests.post(
        "https://texttospeech.googleapis.com/v1/text:synthesize",
        headers={
            "Authorization": f"Bearer {_access_token()}",
            "Content-Type": "application/json",
        },
        json={
            "input": {"ssml": ssml},
            "voice": {"languageCode": "en-US", "name": "en-US-Neural2-D"},
            "audioConfig": {"audioEncoding": "MP3", "speakingRate": 0.78},
        },
        timeout=60,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Google TTS failed: {response.status_code} {response.text}")

    output_path.write_bytes(base64.b64decode(response.json()["audioContent"]))
    return output_path
