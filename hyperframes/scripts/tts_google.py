from __future__ import annotations

import base64
import html
import json
import os
from pathlib import Path
import re

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


def _safe_audio_name(index: int, word: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", word.lower()).strip("-")
    if not slug:
        slug = "item"
    return f"{index:02d}_{slug}.mp3"


def synthesize_item_audios(words: list[str], output_dir: Path) -> dict[str, Path]:
    if not words:
        raise RuntimeError("Refusing to synthesize an empty word list.")

    output_dir.mkdir(parents=True, exist_ok=True)
    token = _access_token()
    audio_paths: dict[str, Path] = {}

    for index, word in enumerate(words, start=1):
        output_path = output_dir / _safe_audio_name(index, word)
        ssml = f"<speak><prosody rate=\"slow\">{html.escape(word)}</prosody></speak>"
        response = requests.post(
            "https://texttospeech.googleapis.com/v1/text:synthesize",
            headers={
                "Authorization": f"Bearer {token}",
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
            raise RuntimeError(f"Google TTS failed for '{word}': {response.status_code} {response.text}")

        audio_content = response.json().get("audioContent")
        if not audio_content:
            raise RuntimeError(f"Google TTS returned empty audio for '{word}'.")

        output_path.write_bytes(base64.b64decode(audio_content))
        if output_path.stat().st_size <= 0:
            raise RuntimeError(f"Google TTS wrote an empty audio file for '{word}'.")
        audio_paths[word] = output_path

    return audio_paths


def _synthesize_text(
    text: str,
    output_path: Path,
    token: str,
    voice_name: str,
    speaking_rate: float = 0.92,
) -> Path:
    ssml = f"<speak><prosody rate=\"medium\">{html.escape(text)}</prosody></speak>"
    response = requests.post(
        "https://texttospeech.googleapis.com/v1/text:synthesize",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={
            "input": {"ssml": ssml},
            "voice": {"languageCode": "en-US", "name": voice_name},
            "audioConfig": {"audioEncoding": "MP3", "speakingRate": speaking_rate},
        },
        timeout=60,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Google TTS failed for '{text}': {response.status_code} {response.text}")

    audio_content = response.json().get("audioContent")
    if not audio_content:
        raise RuntimeError(f"Google TTS returned empty audio for '{text}'.")

    output_path.write_bytes(base64.b64decode(audio_content))
    if output_path.stat().st_size <= 0:
        raise RuntimeError(f"Google TTS wrote an empty audio file for '{text}'.")
    return output_path


def synthesize_dialogue_audios(
    lines: list[str],
    cta: str,
    output_dir: Path,
) -> tuple[list[Path], Path]:
    if not lines:
        raise RuntimeError("Refusing to synthesize an empty dialogue.")
    if not cta:
        raise RuntimeError("Refusing to synthesize dialogue without CTA.")

    output_dir.mkdir(parents=True, exist_ok=True)
    token = _access_token()
    line_paths: list[Path] = []
    voices = ["en-US-Neural2-F", "en-US-Neural2-D"]

    for index, line in enumerate(lines, start=1):
        voice = voices[(index - 1) % len(voices)]
        output_path = output_dir / f"line_{index:02d}.mp3"
        line_paths.append(_synthesize_text(line, output_path, token, voice_name=voice, speaking_rate=0.92))

    cta_path = output_dir / "cta.mp3"
    _synthesize_text(cta, cta_path, token, voice_name="en-US-Neural2-F", speaking_rate=0.9)
    return line_paths, cta_path


def synthesize_thefluentbuild_audios(
    lines: list[str],
    cta: str,
    output_dir: Path,
) -> tuple[list[Path], Path]:
    if not lines:
        raise RuntimeError("Refusing to synthesize an empty TheFluentBuild dialogue.")
    if not cta:
        raise RuntimeError("Refusing to synthesize TheFluentBuild dialogue without CTA.")

    output_dir.mkdir(parents=True, exist_ok=True)
    token = _access_token()
    line_paths: list[Path] = []

    for index, line in enumerate(lines, start=1):
        is_grandma = index % 2 == 0
        voice = "en-US-Neural2-F" if is_grandma else "en-US-Neural2-C"
        rate = 0.86 if is_grandma else 0.92
        output_path = output_dir / f"line_{index:02d}.mp3"
        line_paths.append(_synthesize_text(line, output_path, token, voice_name=voice, speaking_rate=rate))

    cta_path = output_dir / "cta.mp3"
    _synthesize_text(cta, cta_path, token, voice_name="en-US-Neural2-F", speaking_rate=0.88)
    return line_paths, cta_path


def synthesize_words(words: list[str], output_path: Path) -> Path:
    """Backward-compatible helper kept for older manual calls.

    The HyperFrames pipeline now uses synthesize_item_audios() so each item can
    be synchronized with its own arrow and on-screen label.
    """
    if not words:
        raise RuntimeError("Refusing to synthesize an empty word list.")

    ssml = "<speak>" + '<break time="900ms"/>'.join(html.escape(word) for word in words) + '<break time="1200ms"/></speak>'
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
