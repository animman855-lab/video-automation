from __future__ import annotations

import argparse
import os
from pathlib import Path

import requests


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send a text file to Telegram.")
    parser.add_argument("message_file", help="Path to a UTF-8 text file.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Telegram secrets not configured. Skipping Telegram alert.")
        return 0

    message_path = Path(args.message_file)
    if not message_path.exists():
        print(f"Telegram message file not found: {message_path}")
        return 0

    text = message_path.read_text(encoding="utf-8").strip()
    if not text:
        print("Telegram message is empty. Skipping Telegram alert.")
        return 0

    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data={
            "chat_id": chat_id,
            "text": text[:3900],
            "disable_web_page_preview": "true",
        },
        timeout=30,
    )
    print(f"Telegram status: {response.status_code}")
    if response.status_code >= 400:
        raise RuntimeError(response.text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
