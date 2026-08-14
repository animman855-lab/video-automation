import argparse
import os
import re
from datetime import datetime
from pathlib import Path

import pytz
import requests
from metadata_provider import deterministic_metadata, request_metadata
from publish_timing import queryable_dates, slot_is_due, slot_sort_value


NOTION_TOKEN = os.environ["NOTION_TOKEN"].strip()
NOTION_DATABASE_ID = os.environ["NOTION_DATABASE_ID"].strip()
UPLOAD_POST_API_KEY = os.environ["UPLOAD_POST_API_KEY"].strip()

EBOOK_LINK = "https://mybook.to/100EnglishMistakes"
PINTEREST_PROFILE = "thefluentbuild"
KAYLA_PROFILE = "kayla"
KAYLA_PINTEREST_BOARD_ID = "1108800439448657323"
MIN_SUCCESSFUL_PLATFORMS = 2
REQUIRED_HASHTAGS = [
    "#learnenglish",
    "#englishapp",
    "#englishpractice",
    "#speakenglish",
    "#salooenglish",
]
YOUTUBE_TITLE_HASHTAGS = ["#english", "#learnenglish", "#englishlearning"]
YOUTUBE_TITLE_MAX_LENGTH = 100

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}


def toronto_now():
    return datetime.now(pytz.timezone("America/Toronto"))


def get_text(prop):
    if not prop:
        return ""
    prop_type = prop.get("type")
    if prop_type == "title":
        return "".join(item.get("plain_text", "") for item in prop.get("title", []))
    if prop_type == "rich_text":
        return "".join(item.get("plain_text", "") for item in prop.get("rich_text", []))
    if prop_type == "select":
        value = prop.get("select")
        return value.get("name", "") if value else ""
    if prop_type == "url":
        return prop.get("url") or ""
    if prop_type == "date":
        value = prop.get("date")
        return value.get("start", "") if value else ""
    return ""


def get_multi_select(prop):
    return [item.get("name", "") for item in prop.get("multi_select", []) if item.get("name")]


def query_kayla_ads(target_dates):
    dates = [target_dates] if isinstance(target_dates, str) else list(target_dates)
    payload = {
        "filter": {
            "and": [
                {"property": "Avatar", "select": {"equals": "kayla"}},
                {"property": "Video Type", "select": {"equals": "Visual Vocabulary"}},
                {"property": "Statut", "select": {"equals": "A publier"}},
                {
                    "or": [
                        {"property": "Date Publication", "date": {"equals": value}}
                        for value in dates
                    ]
                },
            ]
        },
        "sorts": [
            {"property": "Slot", "direction": "ascending"},
            {"property": "Titre", "direction": "ascending"},
        ],
        "page_size": 50,
    }
    response = requests.post(
        f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
        headers=NOTION_HEADERS,
        json=payload,
        timeout=30,
    )
    print("NOTION STATUS:", response.status_code)
    response.raise_for_status()
    return response.json().get("results", [])


def repo_root() -> Path:
    return Path(__file__).resolve().parent


def local_output_dir() -> Path:
    configured = os.getenv("KAYLA_OUTPUT_DIR", "kayla-output").strip() or "kayla-output"
    output_dir = Path(configured)
    if not output_dir.is_absolute():
        output_dir = repo_root() / output_dir
    return output_dir


def local_kayla_video_path(row: dict) -> Path:
    props = row.get("properties", {})
    date = get_text(props.get("Date Publication"))
    slot = get_text(props.get("Slot")).replace(":", "")
    page_id = row.get("id", "")[:8]
    return local_output_dir() / f"kayla-flow-final-{date}-{slot}-{page_id}.mp4"


def generate_kayla_ad_metadata(script, platforms):
    platforms_str = ", ".join(platforms)
    prompt = f"""You are a performance social media copywriter for Saloo English, an English learning app.

Create platform-specific titles and descriptions for a short UGC-style ad video.
The public account is Kayla, but the goal is to promote Saloo English App.

Core positioning:
- Saloo English helps learners practice useful English every day.
- Focus on speaking confidence, real-life phrases, fixing common mistakes, and building a daily habit.
- CTA should naturally mention the app, for example: "Try Saloo English. Link in bio."

Rules:
- English only.
- Keep the copy direct, natural, and conversion-focused.
- Do not make fake claims about guaranteed results.
- Do not mention discounts unless the script says so.
- Always include these exact mandatory hashtags in every platform description:
  #learnenglish #englishapp #englishpractice #speakenglish #salooenglish

Generate only for these platforms: {platforms_str}

Format exactly:
YOUTUBE_TITLE: [title]
YOUTUBE_DESCRIPTION: [description]
TIKTOK_TITLE: [title]
TIKTOK_DESCRIPTION: [description]
INSTAGRAM_TITLE: [title]
INSTAGRAM_DESCRIPTION: [description]
FACEBOOK_TITLE: [title]
FACEBOOK_DESCRIPTION: [description]
PINTEREST_TITLE: [title]
PINTEREST_DESCRIPTION: [description]

Video script/context:
{script}"""

    required_keys = [
        f"{platform.upper()}_{suffix}"
        for platform in platforms
        for suffix in ("TITLE", "DESCRIPTION")
    ]
    return request_metadata(
        prompt,
        required_keys,
        lambda: deterministic_metadata(
            script,
            "kayla",
            platforms,
            REQUIRED_HASHTAGS,
            youtube_hashtags=YOUTUBE_TITLE_HASHTAGS,
            app_focused=True,
        ),
        label="Kayla Ads",
    )


def missing_required_hashtags(text: str) -> list[str]:
    lowered = (text or "").lower()
    return [tag for tag in REQUIRED_HASHTAGS if tag.lower() not in lowered]


def ensure_required_hashtags(description: str, max_chars: int | None = None) -> str:
    clean_description = (description or "").strip()
    missing = missing_required_hashtags(clean_description)
    if not missing:
        if max_chars and len(clean_description) > max_chars:
            return clean_description[: max_chars - 3].rstrip() + "..."
        return clean_description

    hashtag_line = " ".join(missing)
    separator = "\n\n" if clean_description else ""
    candidate = f"{clean_description}{separator}{hashtag_line}".strip()

    if not max_chars or len(candidate) <= max_chars:
        return candidate

    available = max_chars - len(hashtag_line) - len(separator)
    if available <= 0:
        return hashtag_line[:max_chars]

    trimmed = clean_description[: max(0, available - 3)].rstrip()
    if trimmed:
        trimmed = trimmed.rsplit(" ", 1)[0].rstrip() or trimmed
        trimmed += "..."
    return f"{trimmed}{separator}{hashtag_line}".strip()


def title_with_required_hashtags(title: str, max_chars: int = 150) -> str:
    clean_title = " ".join((title or "").split()).strip()
    missing = missing_required_hashtags(clean_title)
    if missing:
        clean_title = f"{clean_title} {' '.join(missing)}".strip()
    if len(clean_title) <= max_chars:
        return clean_title
    return clean_title[: max_chars - 3].rstrip() + "..."


def youtube_title_with_hashtags(title: str) -> str:
    hashtag_line = " ".join(YOUTUBE_TITLE_HASHTAGS)
    clean_title = " ".join((title or "").split()).strip()
    clean_title = re.sub(r"#\w+", "", clean_title).strip()
    clean_title = re.sub(r"\s+", " ", clean_title).strip(" -|")
    available = YOUTUBE_TITLE_MAX_LENGTH - len(hashtag_line) - 1
    if available < 25:
        available = 25
    if len(clean_title) > available:
        clean_title = clean_title[:available].rstrip()
        clean_title = clean_title.rsplit(" ", 1)[0].rstrip() or clean_title
    return f"{clean_title} {hashtag_line}".strip()


def upload_title_for_platform(title: str, platform_key: str) -> str:
    if platform_key == "youtube":
        return youtube_title_with_hashtags(title)
    if platform_key == "tiktok":
        return title_with_required_hashtags(title, max_chars=150)
    return title


def kayla_ad_context(script: str, prompt: str, notion_title: str) -> str:
    if script.strip():
        return script.strip()
    if prompt.strip():
        return prompt.strip()
    return (
        f"Kayla UGC ad for Saloo English. Notion title: {notion_title}. "
        "Promote Saloo English as an app that helps English learners practice speaking, "
        "fix common mistakes, improve pronunciation, and build confidence for real-life conversations. "
        "Keep the copy natural, direct, and conversion-focused."
    )


def get_drive_file_id(drive_url):
    match = re.search(r"/d/([a-zA-Z0-9_-]+)", drive_url)
    if match:
        return match.group(1)
    match = re.search(r"id=([a-zA-Z0-9_-]+)", drive_url)
    if match:
        return match.group(1)
    raise ValueError(f"Cannot extract file ID from: {drive_url}")


def download_video(drive_url):
    file_id = get_drive_file_id(drive_url)
    print(f"  File ID: {file_id}")
    session = requests.Session()
    download_url = f"https://drive.usercontent.google.com/download?id={file_id}&export=download&authuser=0&confirm=t"
    response = session.get(download_url, stream=True, timeout=90)
    print(f"  Download status: {response.status_code}")
    print(f"  Content-Type: {response.headers.get('Content-Type', 'unknown')}")
    response.raise_for_status()
    tmp_path = f"/tmp/kayla_ad_{file_id}.mp4"
    with open(tmp_path, "wb") as output:
        for chunk in response.iter_content(chunk_size=32768):
            if chunk:
                output.write(chunk)
    print(f"  Video downloaded: {tmp_path} ({os.path.getsize(tmp_path)} bytes)")
    return tmp_path


def platform_key(platform):
    return {
        "YouTube": "youtube",
        "Facebook": "facebook",
        "Instagram": "instagram",
        "TikTok": "tiktok",
        "Pinterest": "pinterest",
    }.get(platform)


def upload_video(video_path, title, description, platform):
    key = platform_key(platform)
    if not key:
        print(f"  Unknown platform skipped: {platform}")
        return False

    if key == "pinterest":
        pinterest_desc = ensure_required_hashtags(description, max_chars=440)
        link_text = f"\n\nTry Saloo English: {EBOOK_LINK}"
        if len(pinterest_desc) + len(link_text) <= 480:
            pinterest_desc += link_text
        data_params = [
            ("user", PINTEREST_PROFILE),
            ("pinterest_title", title),
            ("pinterest_description", pinterest_desc),
            ("platform[]", key),
            ("pinterest_board_id", KAYLA_PINTEREST_BOARD_ID),
            ("link", EBOOK_LINK),
        ]
    else:
        description = ensure_required_hashtags(description)
        upload_title = upload_title_for_platform(title, key)
        data_params = [
            ("user", KAYLA_PROFILE),
            ("title", upload_title),
            ("description", description),
            ("platform[]", key),
        ]
        if key == "youtube":
            data_params.extend(
                [
                    ("selfDeclaredMadeForKids", "false"),
                    ("privacyStatus", "public"),
                ]
            )
        elif key == "facebook":
            data_params.extend(
                [
                    ("facebook_title", title),
                    ("facebook_description", description),
                ]
            )
        elif key == "instagram":
            data_params.extend(
                [
                    ("instagram_title", title),
                    ("instagram_description", description),
                ]
            )
        elif key == "tiktok":
            data_params.extend(
                [
                    ("tiktok_title", upload_title),
                    ("tiktok_description", description),
                ]
            )

    try:
        with open(video_path, "rb") as video_file:
            response = requests.post(
                "https://api.upload-post.com/api/upload",
                headers={"Authorization": f"Apikey {UPLOAD_POST_API_KEY}"},
                data=data_params,
                files={"video": ("video.mp4", video_file, "video/mp4")},
                timeout=180,
            )
    except requests.exceptions.RequestException as exc:
        print(f"  UPLOAD ERROR {platform}: {exc}")
        return False

    print(f"  UPLOAD STATUS {platform}: {response.status_code}")
    print(f"  UPLOAD RESPONSE {platform}: {response.text[:200]}")
    if response.status_code >= 400:
        return False
    try:
        result = response.json()
    except Exception:
        return False
    if result.get("success") is False:
        return False
    platform_result = result.get("results", {}).get(key)
    if isinstance(platform_result, dict) and platform_result.get("success") is False:
        return False
    if result.get("status") == "failed":
        return False
    return True


def mark_as_published(page_id):
    response = requests.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers=NOTION_HEADERS,
        json={"properties": {"Statut": {"select": {"name": "Publie"}}}},
        timeout=30,
    )
    response.raise_for_status()
    print("  Notion updated -> Publie")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="YYYY-MM-DD. Defaults to today in Montreal.")
    args = parser.parse_args()

    now = toronto_now()
    target_dates = [args.date] if args.date else queryable_dates(now)
    print(f"Current time (Montreal): {now.strftime('%Y-%m-%d %H:%M')}")
    print(f"Kayla Ads target dates: {', '.join(target_dates)}")

    rows = query_kayla_ads(target_dates)
    print(f"Ready Kayla ad row(s): {len(rows)}")
    print(f"Local Kayla output dir: {local_output_dir()}")

    candidates = []
    for row in rows:
        props = row.get("properties", {})
        slot = get_text(props.get("Slot"))
        publication_date = get_text(props.get("Date Publication"))
        title = get_text(props.get("Titre")) or "Untitled Kayla Ad"
        if not slot_is_due(slot, publication_date, now):
            print(f"Skipping {title}: slot {slot} not due.")
            continue
        local_video = local_kayla_video_path(row)
        video_url = get_text(props.get("Lien Video"))
        if not local_video.exists() and not video_url:
            print(f"Skipping {title}: no local MP4 and no Lien Video.")
            continue
        candidates.append((slot_sort_value(publication_date, slot), row, local_video))

    selected = None
    selected_local_video = None
    if candidates:
        _, selected, local_video = min(candidates, key=lambda item: item[0])
        selected_local_video = local_video if local_video.exists() else None

    if not selected:
        print("No due Kayla ad row with an available video found. Nothing to publish.")
        return 0

    props = selected["properties"]
    page_id = selected["id"]
    notion_title = get_text(props.get("Titre")) or "Kayla Ad"
    script = get_text(props.get("Script"))
    prompt = get_text(props.get("Prompt 1"))
    video_url = get_text(props.get("Lien Video"))
    platforms = get_multi_select(props.get("Plateforme", {}))
    slot = get_text(props.get("Slot"))
    context = kayla_ad_context(script, prompt, notion_title)

    print(f"Selected Kayla ad: {notion_title}")
    print(f"Slot: {slot}")
    print(f"Platforms: {platforms}")
    if selected_local_video:
        print(f"Video source: local MP4 ({selected_local_video})")
    elif video_url:
        print("Video source: Notion Lien Video")
    if script:
        print("Metadata context source: Script")
    elif prompt:
        print("Metadata context source: Prompt 1")
    else:
        print("Metadata context source: generic Kayla/Saloo fallback")
    if not platforms:
        print("No platforms - skipping.")
        return 0

    metadata = generate_kayla_ad_metadata(context, platforms)
    delete_video_after = False
    if selected_local_video:
        video_path = str(selected_local_video)
    else:
        video_path = download_video(video_url)
        delete_video_after = True
    successes = 0
    failures = 0

    try:
        for platform in platforms:
            key = platform.upper()
            title = metadata.get(f"{key}_TITLE") or metadata.get("YOUTUBE_TITLE") or notion_title
            description = metadata.get(f"{key}_DESCRIPTION") or metadata.get("YOUTUBE_DESCRIPTION") or context
            description = ensure_required_hashtags(description)
            missing_hashtags = missing_required_hashtags(description)
            print(f"\n  [{platform}]")
            print(f"  Title: {title[:90]}")
            upload_title_preview = upload_title_for_platform(title, platform_key(platform) or "")
            if upload_title_preview != title:
                print(f"  Upload title: {upload_title_preview[:120]}")
            print(f"  Description: {description[:120]}...")
            if missing_hashtags:
                print(f"  Required hashtags present: no - missing {missing_hashtags}")
            else:
                print("  Required hashtags present: yes")
            if upload_video(video_path, title, description, platform):
                successes += 1
                print(f"  {platform} success")
            else:
                failures += 1
                print(f"  {platform} failed/skipped")
    finally:
        if delete_video_after and os.path.exists(video_path):
            os.remove(video_path)

    print(f"Kayla Ads result: successes={successes} failures={failures}")
    if successes >= MIN_SUCCESSFUL_PLATFORMS:
        mark_as_published(page_id)
    else:
        print("Not enough successful platforms. Keeping A publier.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
