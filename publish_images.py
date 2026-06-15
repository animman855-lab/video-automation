import argparse
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

import pytz
import requests
from PIL import Image, ImageFilter


UPLOAD_POST_PHOTO_ENDPOINT = "https://api.upload-post.com/api/upload_photos"
EBOOK_LINK = "https://mybook.to/100EnglishMistakes"
PINTEREST_PROFILE = "thefluentbuild"
MIN_SUCCESSFUL_IMAGE_PLATFORMS = 1
TIKTOK_TITLE_MAX_LENGTH = 85

PINTEREST_BOARDS = {
    "oliviaa": "1108800439448657315",
    "cindy": "1108800439448657317",
    "teacherryan": "1108800439448657320",
    "thefluentbuild": "1108800439448654918",
    "kayla": "1108800439448657323",
}

SLOT_HOURS = {
    "08:00": 8 * 60,
    "12:00": 12 * 60,
    "16:00": 16 * 60,
    "00:00": 0,
}

SLOT_WINDOW_MINUTES = 90
MIDNIGHT_SLOT_WINDOW_MINUTES = 180


def slot_is_due(slot_name):
    tz = pytz.timezone("America/Toronto")
    now = datetime.now(tz)
    current_minutes = now.hour * 60 + now.minute
    slot_minutes = SLOT_HOURS.get(slot_name)

    if slot_minutes is None:
        return False

    diff = current_minutes - slot_minutes

    if slot_minutes == 0:
        return 0 <= current_minutes <= MIDNIGHT_SLOT_WINDOW_MINUTES

    return 0 <= diff <= SLOT_WINDOW_MINUTES


def get_text(prop):
    if prop.get("type") == "title":
        return "".join(item.get("plain_text", "") for item in prop.get("title", []))
    if prop.get("type") == "rich_text":
        return "".join(item.get("plain_text", "") for item in prop.get("rich_text", []))
    if prop.get("type") == "url":
        return prop.get("url") or ""
    if prop.get("type") == "select":
        value = prop.get("select")
        return value.get("name", "") if value else ""
    return ""


def get_multi_select(prop):
    return [item["name"] for item in prop.get("multi_select", [])]


def notion_headers():
    return {
        "Authorization": f"Bearer {os.environ['NOTION_TOKEN'].strip()}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }


def get_images_to_publish(target_date=None):
    tz = pytz.timezone("America/Toronto")
    today = target_date or datetime.now(tz).strftime("%Y-%m-%d")

    payload = {
        "filter": {
            "and": [
                {"property": "Statut", "select": {"equals": "A publier"}},
                {"property": "Date Publication", "date": {"equals": today}},
            ]
        }
    }

    database_id = os.environ["NOTION_IMAGE_DATABASE_ID"].strip()
    url = f"https://api.notion.com/v1/databases/{database_id}/query"

    response = requests.post(url, headers=notion_headers(), json=payload, timeout=30)
    response.raise_for_status()
    return response.json().get("results", [])


def prop_text(props, name):
    prop = props.get(name)
    return get_text(prop) if prop else ""


def google_drive_download_url(url):
    for pattern in [r"/file/d/([^/]+)", r"[?&]id=([^&]+)"]:
        match = re.search(pattern, url)
        if match:
            return f"https://drive.google.com/uc?export=download&id={match.group(1)}"
    return url


def download_image(image_url):
    if not image_url:
        raise ValueError("Image File is empty")

    response = requests.get(google_drive_download_url(image_url), timeout=60)
    response.raise_for_status()

    suffix = ".png" if "png" in response.headers.get("content-type", "") else ".jpg"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(response.content)
    tmp.close()

    return Path(tmp.name)


def make_tiktok_image(source_path):
    target_w, target_h = 1080, 1920
    source = Image.open(source_path).convert("RGB")

    bg = source.copy()
    bg_ratio = target_w / target_h
    source_ratio = bg.width / bg.height

    if source_ratio > bg_ratio:
        new_h = target_h
        new_w = int(new_h * source_ratio)
    else:
        new_w = target_w
        new_h = int(new_w / source_ratio)

    bg = bg.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    bg = bg.crop((left, top, left + target_w, top + target_h)).filter(ImageFilter.GaussianBlur(28))

    fg_w = target_w
    fg_h = int(source.height * (fg_w / source.width))

    if fg_h > target_h:
        fg_h = target_h
        fg_w = int(source.width * (fg_h / source.height))

    fg = source.resize((fg_w, fg_h), Image.LANCZOS)

    output = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
    output.close()

    bg.paste(fg, ((target_w - fg_w) // 2, (target_h - fg_h) // 2))
    bg.save(output.name, "JPEG", quality=92, optimize=True)

    return Path(output.name)


def first_caption_line(caption):
    return caption.splitlines()[0].strip() if caption.splitlines() else caption.strip()


def truncate_clean(text, max_length):
    text = " ".join(text.split())
    if len(text) <= max_length:
        return text
    shortened = text[: max_length - 1].rstrip()
    if " " in shortened:
        shortened = shortened.rsplit(" ", 1)[0]
    return shortened.rstrip(".,;:!?") + "…"


def upload_photo_to_platform(image_path, avatar, caption, platform):
    normalized = platform.lower()

    photo_file = image_path.open("rb")
    files = {"photos[]": (image_path.name, photo_file, "image/jpeg")}

    try:
        if normalized == "pinterest":
            board_id = PINTEREST_BOARDS.get(avatar)
            if not board_id:
                print(f"Skipping Pinterest: no board ID for {avatar}")
                return True

            data = {
                "user": PINTEREST_PROFILE,
                "platform[]": "pinterest",
                "pinterest_title": caption.splitlines()[0][:100],
                "pinterest_description": caption[:480],
                "pinterest_board_id": board_id,
                "link": EBOOK_LINK,
            }

        else:
            title_text = caption if normalized == "facebook" else first_caption_line(caption)[:100]

            data = {
                "user": avatar,
                "platform[]": normalized,
                "title": title_text,
                "description": caption,
            }

            if normalized == "facebook":
                data["facebook_title"] = caption
                data["facebook_description"] = caption

            if normalized == "tiktok":
                tiktok_title = truncate_clean(first_caption_line(caption), TIKTOK_TITLE_MAX_LENGTH)
                data["title"] = tiktok_title
                data["tiktok_title"] = tiktok_title
                data["tiktok_description"] = caption
                data["auto_add_music"] = "true"

        response = requests.post(
            UPLOAD_POST_PHOTO_ENDPOINT,
            headers={"Authorization": f"Apikey {os.environ['UPLOAD_POST_API_KEY'].strip()}"},
            data=data,
            files=files,
            timeout=120,
        )

        if response.status_code >= 400:
            print(f"{platform} failed: {response.status_code} {response.text}")
            return False

        result = response.json()

        if result.get("success") is False:
            print(f"{platform} failed: {result}")
            return False

        platform_result = result.get("results", {}).get(normalized)
        if isinstance(platform_result, dict) and platform_result.get("success") is False:
            print(f"{platform} failed: {platform_result}")
            return False

        print(f"{platform} success: {result}")
        return True

    finally:
        photo_file.close()


def publish_to_upload_post(image_path, avatar, caption, platforms):
    successes = 0
    failures = 0

    for platform in platforms:
        tiktok_path = None
        upload_path = image_path

        if platform.lower() == "tiktok":
            tiktok_path = make_tiktok_image(image_path)
            upload_path = tiktok_path

        try:
            if upload_photo_to_platform(upload_path, avatar, caption, platform):
                successes += 1
            else:
                failures += 1

        finally:
            if tiktok_path:
                tiktok_path.unlink(missing_ok=True)

    print(f"Image publish result: successes={successes} failures={failures}")
    return successes >= MIN_SUCCESSFUL_IMAGE_PLATFORMS


def mark_as_published(page_id):
    payload = {"properties": {"Statut": {"select": {"name": "Publie"}}}}

    response = requests.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers=notion_headers(),
        json=payload,
        timeout=30,
    )

    response.raise_for_status()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="YYYY-MM-DD. Defaults to today.")
    parser.add_argument("--avatar", help="Optional avatar slug, for testing one account.")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    required = ["NOTION_TOKEN", "NOTION_IMAGE_DATABASE_ID", "UPLOAD_POST_API_KEY"]
    missing = [name for name in required if not os.getenv(name)]

    if missing:
        raise SystemExit(f"Missing environment variables: {', '.join(missing)}")

    pages = get_images_to_publish(args.date)

    if args.avatar:
        pages = [page for page in pages if prop_text(page["properties"], "Avatar") == args.avatar]

    if args.limit:
        pages = pages[: args.limit]

    print(f"Found {len(pages)} image posts to publish")

    for page in pages:
        props = page["properties"]

        avatar = prop_text(props, "Avatar")
        caption = prop_text(props, "Caption")
        image_url = prop_text(props, "Image File")
        slot = prop_text(props, "Slot")
        platforms = get_multi_select(props.get("Plateforme", {}))

        print(f"\n--- {avatar} | Slot: {slot} | Platforms: {platforms} ---")

        if not slot_is_due(slot):
            print(f"Slot {slot} not due yet - skipping.")
            continue

        if not image_url:
            print("No image link - skipping.")
            continue

        image_path = download_image(image_url)

        try:
            if publish_to_upload_post(image_path, avatar, caption, platforms):
                mark_as_published(page["id"])
                print(f"Marked as Publie: {page['id']}")
            else:
                print(f"Keeping A publier: {page['id']}")

        finally:
            image_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
