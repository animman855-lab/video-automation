import os
import requests
import re
from datetime import datetime
from pathlib import Path
import pytz

from metadata_provider import deterministic_metadata, request_metadata
from hashtag_utils import final_hashtags, prepare_video_metadata, validate_prepared_metadata
from profile_config import get_profile_config
from publication_report import (
    append_publication_report,
    final_status_for_successes,
    result_details,
    sanitize_error,
)

from publish_timing import (
    claim_slot,
    queryable_dates,
    read_slot_lock,
    slot_is_due,
    slot_key,
    slot_sort_value,
)

NOTION_TOKEN = os.environ["NOTION_TOKEN"].strip()
NOTION_DATABASE_ID = os.environ["NOTION_DATABASE_ID"].strip()
UPLOAD_POST_API_KEY = os.environ["UPLOAD_POST_API_KEY"].strip()

EBOOK_LINK = "https://mybook.to/100EnglishMistakes"
PINTEREST_PROFILE = "thefluentbuild"

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

AVATAR_PROFILES = {
    "oliviaa": "oliviaa",
    "thefluentbuild": "thefluentbuild",
    "teacherryan": "teacherryan",
    "cindy": "cindy",
    "kayla": "kayla",
}

AVATAR_HASHTAG = {
    "oliviaa": "#oliviaa",
    "thefluentbuild": "#thefluentbuild",
    "teacherryan": "#teacherryan",
    "cindy": "#cindy",
    "kayla": "#kayla",
}

AVATAR_CONTEXT = {
    "oliviaa": "a 23-year-old sassy Gen Z English teacher who teaches modern slang, idioms and social media English to 18-30 year olds.",
    "thefluentbuild": "an elegant older woman who teaches classic vocabulary, politeness and fundamental grammar to 35-70 year olds in a warm and pedagogical way.",
    "teacherryan": "a confident male business English coach who teaches corporate jargon, emails, meetings and negotiations to 25-50 year old professionals.",
    "cindy": "a spontaneous 28-year-old travel enthusiast who teaches airport survival, hotel vocabulary and international lifestyle English to 22-35 year old travelers.",
    "kayla": "a charismatic 32-year-old relationship expert who teaches flirting vocabulary, emotional intelligence and dating English to 20-35 year olds.",
}

PINTEREST_BOARDS = {
    "oliviaa": "1108800439448657315",
    "cindy": "1108800439448657317",
    "teacherryan": "1108800439448657320",
    "thefluentbuild": "1108800439448654918",
    "kayla": "1108800439448657323",
}

VIDEO_DOWNLOAD_TIMEOUT = (15, 90)
VIDEO_MIN_BYTES = 1024
ALLOWED_VIDEO_CONTENT_TYPES = {
    "application/octet-stream",
    "binary/octet-stream",
}

YOUTUBE_HASHTAGS = [
    "#englishmastery",
    "#englishvocab",
    "#englishvoice",
    "#learnenglish",
    "#englishlesson",
    "#spokenenglish",
    "#englishspeaking",
]

# Kayla remains on its dedicated manual publisher to preserve the existing
# workflow boundary. HyperFrames are legacy-only and excluded below.
SKIP_AVATARS_IN_MAIN_VIDEO_WORKFLOW = {"kayla"}


def repo_root():
    return Path(__file__).resolve().parent


def local_hyperframes_output_dir():
    configured = os.getenv("HYPERFRAMES_OUTPUT_DIR", "hyperframes-output").strip() or "hyperframes-output"
    output_dir = Path(configured)
    if not output_dir.is_absolute():
        output_dir = repo_root() / output_dir
    return output_dir


def local_hyperframes_video_path(row):
    props = row.get("properties", {})
    avatar = props["Avatar"]["select"]["name"].lower() if props.get("Avatar", {}).get("select") else ""
    publication_date = props["Date Publication"]["date"]["start"] if props.get("Date Publication", {}).get("date") else ""
    slot = props["Slot"]["select"]["name"].replace(":", "") if props.get("Slot", {}).get("select") else ""
    page_id = row.get("id", "")[:8]
    return local_hyperframes_output_dir() / f"hyperframes-{avatar}-{publication_date}-{slot}-{page_id}.mp4"


def get_videos_to_publish():
    tz = pytz.timezone("America/Toronto")
    now = datetime.now(tz)
    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
    payload = {
        "filter": {
            "and": [
                {"property": "Statut", "select": {"equals": "A publier"}},
                {
                    "or": [
                        {"property": "Date Publication", "date": {"equals": value}}
                        for value in queryable_dates(now)
                    ]
                },
            ]
        }
    }
    response = requests.post(url, headers=NOTION_HEADERS, json=payload, timeout=30)
    print("NOTION STATUT:", response.status_code)
    response.raise_for_status()
    return response.json().get("results", [])


def select_due_slot_group(videos, now):
    candidates = []
    for video in videos:
        props = video["properties"]
        status = props["Statut"]["select"]["name"] if props["Statut"]["select"] else ""
        if status != "A publier":
            continue
        avatar = props["Avatar"]["select"]["name"].lower() if props["Avatar"]["select"] else ""
        if avatar in SKIP_AVATARS_IN_MAIN_VIDEO_WORKFLOW:
            continue
        video_type = props["Video Type"]["select"]["name"] if props["Video Type"]["select"] else ""
        if video_type.lower() == "hyperframes":
            continue
        publication_date = props["Date Publication"]["date"]["start"] if props["Date Publication"]["date"] else ""
        slot = props["Slot"]["select"]["name"] if props["Slot"]["select"] else ""
        script = props["Script"]["rich_text"]
        lien_video = props["Lien Video"]["url"] if props["Lien Video"]["url"] else ""
        if not slot_is_due(slot, publication_date, now):
            continue
        if not script or not lien_video:
            continue
        candidates.append((slot_sort_value(publication_date, slot), slot_key(publication_date, slot)))

    if not candidates:
        return [], None

    selected_key = min(candidates, key=lambda item: item[0])[1]
    selected = []
    for video in videos:
        props = video["properties"]
        status = props["Statut"]["select"]["name"] if props["Statut"]["select"] else ""
        if status != "A publier":
            continue
        avatar = props["Avatar"]["select"]["name"].lower() if props["Avatar"]["select"] else ""
        if avatar in SKIP_AVATARS_IN_MAIN_VIDEO_WORKFLOW:
            continue
        video_type = props["Video Type"]["select"]["name"] if props["Video Type"]["select"] else ""
        if video_type.lower() == "hyperframes":
            continue
        publication_date = props["Date Publication"]["date"]["start"] if props["Date Publication"]["date"] else ""
        slot = props["Slot"]["select"]["name"] if props["Slot"]["select"] else ""
        if slot_key(publication_date, slot) == selected_key:
            selected.append(video)
    return selected, selected_key


def get_youtube_hashtag(index):
    return YOUTUBE_HASHTAGS[index % len(YOUTUBE_HASHTAGS)]


def generate_metadata(script, avatar, plateformes, video_index=0, source_title=""):
    avatar_hashtag = AVATAR_HASHTAG.get(avatar.lower(), f"#{avatar.lower()}")
    youtube_hashtag = get_youtube_hashtag(video_index)
    context = AVATAR_CONTEXT.get(avatar.lower(), "an English teacher")
    profile_config = get_profile_config(avatar)
    localization_instruction = profile_config["metadata_instruction"]
    localized_cta = profile_config["cta"]
    platforms_str = ", ".join(plateformes)

    prompt = f"""You are a social media expert creating content for {context}

Based on the video script below, generate optimized content for these platforms: {platforms_str}

PROFILE LOCALIZATION:
- Target market: {profile_config['market']}
- Audience: {profile_config['audience']}
- Metadata language: {profile_config['metadata_language']}
- {localization_instruction}
- Use this market CTA when a CTA is needed: {localized_cta}
- Do not invent a source, creative family, variation, or relationship that is not present in the input.
- Make the wording natural and distinct from generic metadata. Avoid repeating the same opening pattern.

MANDATORY HASHTAGS — must appear in ALL platforms: #learnenglish #englishvocabulary #englishspeakingpractice #english
These 4 hashtags are REQUIRED on every platform without exception. Add topic-specific hashtags on top.

STRICT RULES PER PLATFORM:

=== YOUTUBE ===
TITLE rules:
- Use a VARIED pattern based on script content:
  * Don't Say "[wrong word]" ❌ Say This Instead {youtube_hashtag}
  * Stop Using "[phrase]" ❌ Native Speakers Say This {youtube_hashtag}
  * Most People Say "[X]" Wrong ❌ Here's The Right Way {youtube_hashtag}
  * Learn These [Topic] Words ❌ You Didn't Know {youtube_hashtag}
- Always end with: {youtube_hashtag}
- Max 90 characters total

DESCRIPTION rules:
- Line 1: Repeat the title
- 2-3 engaging intro sentences
- List with ✅ of 3-5 things viewers will learn
- "Perfect for beginners, ESL learners, and anyone improving their English."
- "Watch, listen, and repeat to build strong vocabulary naturally."
- SEO keywords as comma-separated list (15-20 keywords)
- Hashtags: #learnenglish #englishvocabulary #englishspeakingpractice #english #englishlesson #esl #spokenenglish {avatar_hashtag} + up to 10 topic-specific hashtags
- Max 5000 characters total

=== TIKTOK ===
TITLE rules:
- ONE line in CAPS, punchy hook
- Do not add hashtags yourself; the publisher will append three short topic hashtags and one avatar hashtag.
- Max 150 characters total

DESCRIPTION rules:
- Write one or two short sentences describing the video.
- Do not add hashtags yourself; the publisher will append the required TikTok hashtags.
- Keep the description concise and natural.

=== INSTAGRAM ===
TITLE rules:
- Intriguing question with 1-2 emojis based on script topic

DESCRIPTION rules:
- Start with 👉 then ONE continuous engaging paragraph (2-4 sentences) about what viewers will learn
- Skip one line
- Engagement question on its own line (ex: "Which expression will you use first? 💬")
- Skip one line
- Hashtags: #learnenglish #englishvocabulary #englishspeakingpractice #english {avatar_hashtag} + topic-specific hashtags
- Max 2200 characters total — NEVER exceed

=== FACEBOOK ===
TITLE rules:
- Intriguing question with 1-2 emojis based on script topic

DESCRIPTION rules:
- Start with 👉 then ONE continuous engaging paragraph (2-4 sentences) about what viewers will learn
- Skip one line
- Engagement question on its own line
- Skip one line
- Hashtags: #learnenglish #englishvocabulary #englishspeakingpractice #english {avatar_hashtag} + topic-specific hashtags
- Keep under 500 characters for best engagement

=== PINTEREST ===
TITLE rules:
- SEO-optimized title with main keyword first
- How-to or number format when possible
- Max 100 characters

DESCRIPTION rules:
- Rich SEO description with keywords naturally included
- End with: "Save this pin to learn later!"
- Hashtags: #learnenglish #englishvocabulary #englishspeakingpractice #english {avatar_hashtag} + 3-5 topic hashtags
- STRICT max 480 characters total including hashtags — NEVER exceed

Use the profile localization rules above. English must remain visible for the English phrases being taught.
Only generate sections for platforms in: {platforms_str}

Respond ONLY in this exact format:
YOUTUBE_TITLE: [title here]
YOUTUBE_DESCRIPTION: [description here]
TIKTOK_TITLE: [title here]
TIKTOK_DESCRIPTION:
INSTAGRAM_TITLE: [title here]
INSTAGRAM_DESCRIPTION: [description here]
FACEBOOK_TITLE: [title here]
FACEBOOK_DESCRIPTION: [description here]
PINTEREST_TITLE: [title here]
PINTEREST_DESCRIPTION: [description here]

SCRIPT:
{script}

NOTION TITLE CONTEXT:
{source_title}"""

    required_keys = [
        f"{platform.upper()}_{suffix}"
        for platform in plateformes
        for suffix in ("TITLE", "DESCRIPTION")
    ]
    fallback_hashtags = [
        "#learnenglish",
        "#englishvocabulary",
        "#englishspeakingpractice",
        "#english",
        avatar_hashtag,
        "#englishlesson",
        "#esl",
        *profile_config.get("localized_hashtags", []),
    ]
    return request_metadata(
        prompt,
        required_keys,
        lambda: deterministic_metadata(
            script,
            avatar,
            plateformes,
            fallback_hashtags,
            youtube_hashtags=[youtube_hashtag],
            source_title=source_title,
            profile_config=profile_config,
        ),
        label="main publisher",
    )


def get_drive_file_id(drive_url):
    match = re.search(r"/d/([a-zA-Z0-9_-]+)", drive_url)
    if match:
        return match.group(1)
    match = re.search(r"id=([a-zA-Z0-9_-]+)", drive_url)
    if match:
        return match.group(1)
    raise ValueError(f"Cannot extract file ID from: {drive_url}")


class VideoDownloadError(RuntimeError):
    """A row-level video download failure that must not abort the batch."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def validate_video_payload(content_type, first_chunk):
    content_type = (content_type or "").split(";", 1)[0].strip().lower()
    if "text/html" in content_type:
        raise VideoDownloadError(
            "invalid_video_link_html",
            "Google Drive returned HTML instead of a video file",
        )

    is_video_type = content_type.startswith("video/") or content_type in ALLOWED_VIDEO_CONTENT_TYPES
    if not is_video_type:
        raise VideoDownloadError(
            "invalid_video_content_type",
            f"Unexpected video content type: {content_type or 'unknown'}",
        )

    if b"ftyp" not in (first_chunk or b"")[:65536]:
        raise VideoDownloadError(
            "invalid_video_payload",
            "Downloaded content is not a recognizable MP4 payload",
        )


def download_video(drive_url):
    try:
        file_id = get_drive_file_id(drive_url)
    except ValueError as exc:
        raise VideoDownloadError("invalid_video_link_format", str(exc)) from exc
    print(f"  File ID: {file_id}")
    session = requests.Session()
    download_url = f"https://drive.usercontent.google.com/download?id={file_id}&export=download&authuser=0&confirm=t"
    try:
        response = session.get(download_url, stream=True, timeout=VIDEO_DOWNLOAD_TIMEOUT)
    except requests.RequestException as exc:
        raise VideoDownloadError("video_download_failed", f"Video download failed: {exc}") from exc
    print(f"  Download status: {response.status_code}")
    print(f"  Content-Type: {response.headers.get('Content-Type', 'unknown')}")
    try:
        response.raise_for_status()
        chunks = response.iter_content(chunk_size=32768)
        first_chunk = next(chunks, b"")
        validate_video_payload(response.headers.get("Content-Type", ""), first_chunk)
    except VideoDownloadError:
        response.close()
        raise
    except requests.RequestException as exc:
        response.close()
        raise VideoDownloadError("video_download_failed", f"Video download failed: {exc}") from exc

    tmp_path = f"/tmp/video_{file_id}.mp4"
    try:
        with open(tmp_path, "wb") as f:
            if first_chunk:
                f.write(first_chunk)
            for chunk in chunks:
                if chunk:
                    f.write(chunk)
        size = os.path.getsize(tmp_path)
    except (OSError, requests.RequestException) as exc:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise VideoDownloadError("video_download_failed", f"Could not save downloaded video: {exc}") from exc
    finally:
        response.close()

    if size < VIDEO_MIN_BYTES:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise VideoDownloadError("invalid_video_payload", f"Downloaded video is too small: {size} bytes")

    print(f"  Video downloaded: {tmp_path} ({size} bytes)")
    return tmp_path


def publish_video(video_path, titre, description, avatar, platform):
    profile = AVATAR_PROFILES.get(avatar.lower())
    if not profile:
        raise ValueError(f"Unknown avatar: {avatar}")

    platform_map = {
        "YouTube": "youtube",
        "Facebook": "facebook",
        "Instagram": "instagram",
        "TikTok": "tiktok",
        "Pinterest": "pinterest",
    }

    platform_key = platform_map.get(platform)
    if not platform_key:
        raise ValueError(f"Unknown platform: {platform}")

    print(f"  Publishing on: {platform_key}")
    titre, description = prepare_video_metadata(titre, description, avatar, platform_key)
    validate_prepared_metadata(titre, description, avatar, platform_key)
    print(f"  Final hashtags sent: {' '.join(final_hashtags(titre, description))}")
    url = "https://api.upload-post.com/api/upload"

    if platform_key == "pinterest":
        board_id = PINTEREST_BOARDS.get(avatar.lower(), "")
        if not board_id:
            print(f"  [Pinterest] No board ID for {avatar} - skipping")
            return {"success": True, "status": "skipped"}

        print(f"  Pinterest Board ID: {board_id}")

        pinterest_desc = description if description else ""
        if len(pinterest_desc) > 440:
            pinterest_desc = pinterest_desc[:437] + "..."

        link_text = f"\n\nLearn more: {EBOOK_LINK}"
        if len(pinterest_desc) + len(link_text) <= 480:
            pinterest_desc += link_text

        data_params = [
            ("user", PINTEREST_PROFILE),
            ("pinterest_title", titre),
            ("pinterest_description", pinterest_desc),
            ("platform[]", platform_key),
            ("pinterest_board_id", board_id),
            ("link", EBOOK_LINK),
        ]
    else:
        data_params = [
            ("user", profile),
            ("title", titre),
            ("description", description),
            ("platform[]", platform_key),
        ]
        if platform_key == "youtube":
            data_params.extend(
                [
                    ("selfDeclaredMadeForKids", "false"),
                    ("privacyStatus", "public"),
                ]
            )
        elif platform_key == "tiktok":
            data_params.extend(
                [
                    ("tiktok_title", titre),
                    ("tiktok_description", description),
                ]
            )

    with open(video_path, "rb") as video_file:
        response = requests.post(
            url,
            headers={"Authorization": f"Apikey {UPLOAD_POST_API_KEY}"},
            data=data_params,
            files={"video": ("video.mp4", video_file, "video/mp4")},
            timeout=180,
        )

    print(f"  UPLOAD STATUS {platform}: {response.status_code}")
    print(f"  UPLOAD RESPONSE {platform}: {response.text[:200]}")

    if response.status_code >= 400:
        return {"success": False, "status": "failed", "message": f"HTTP {response.status_code}"}

    try:
        result = response.json()
    except Exception:
        result = {"success": False, "message": "Invalid JSON response"}

    return result


def upload_result_succeeded(result, platform_key):
    """Require the platform-level result when Upload-Post provides one."""

    if not isinstance(result, dict):
        return False
    if result.get("success") is False or result.get("status") == "failed":
        return False
    platform_result = result.get("results", {}).get(platform_key)
    if isinstance(platform_result, dict):
        return platform_result.get("success") is True and platform_result.get("status") != "failed"
    return result.get("success") is True


def publish_video_to_platforms(video_path, metadata, avatar, platforms, notion_title):
    """Attempt each platform independently and preserve every outcome."""

    successes = 0
    failures = 0
    outcomes = {}
    for platform in platforms:
        platform_upper = platform.upper()
        title = metadata.get(f"{platform_upper}_TITLE", "") or metadata.get("YOUTUBE_TITLE", notion_title)
        description = metadata.get(f"{platform_upper}_DESCRIPTION", "") or metadata.get("YOUTUBE_DESCRIPTION", "")
        title, description = prepare_video_metadata(title, description, avatar, platform)

        print(f"\n  [{platform}]")
        print(f"  Title: {title[:80]}")
        print(f"  Description: {description[:100]}...")

        try:
            result = publish_video(video_path, title, description, avatar, platform)
        except requests.RequestException as exc:
            failures += 1
            error = sanitize_error(exc)
            outcomes[platform] = {"success": False, "error_type": type(exc).__name__, "error": error}
            print(f"  Upload exception on {platform}: {type(exc).__name__}: {error}")
            continue
        except Exception as exc:
            failures += 1
            error = sanitize_error(exc)
            outcomes[platform] = {"success": False, "error_type": type(exc).__name__, "error": error}
            print(f"  Unexpected upload exception on {platform}: {type(exc).__name__}: {error}")
            continue

        if not isinstance(result, dict):
            failures += 1
            outcomes[platform] = {"success": False, "error_type": "invalid_upload_response", "error": "non-object response"}
            print(f"  Invalid upload response on {platform}")
            continue

        platform_key = platform.lower()
        if not upload_result_succeeded(result, platform_key):
            failures += 1
            outcomes[platform] = result_details(result, platform_key, False)
            print(f"  Failed on {platform}")
        else:
            successes += 1
            outcomes[platform] = result_details(result, platform_key, True)
            print(f"  Succeeded on {platform}")

    return successes, failures, outcomes


def set_notion_status(page_id, status):
    url = f"https://api.notion.com/v1/pages/{page_id}"
    payload = {"properties": {"Statut": {"select": {"name": status}}}}
    response = requests.patch(url, headers=NOTION_HEADERS, json=payload, timeout=30)
    response.raise_for_status()
    print(f"  Notion updated -> {status}")


def mark_as_published(page_id):
    set_notion_status(page_id, "Publie")


def main():
    tz = pytz.timezone("America/Toronto")
    now = datetime.now(tz)
    print(f"Current time (Montreal): {now.strftime('%H:%M')}")

    videos = get_videos_to_publish()
    print(f"{len(videos)} video(s) found.")
    print("Manual-first video workflow: HyperFrames generation is disabled for new publications.")

    videos, selected_key = select_due_slot_group(videos, now)
    if not videos:
        print("No ready due video slot found. Nothing to do.")
        return

    existing_lock = read_slot_lock()
    if existing_lock and existing_lock != selected_key:
        print(f"Another slot is already active in this run ({existing_lock}); keeping videos unchanged.")
        return
    slot_claimed = existing_lock == selected_key
    print(f"Selected publication slot: {selected_key}")

    for index, video in enumerate(videos):
        props = video["properties"]
        page_id = video["id"]

        titre_notion = props["Titre"]["title"][0]["plain_text"] if props["Titre"]["title"] else "No title"
        script = props["Script"]["rich_text"][0]["plain_text"] if props["Script"]["rich_text"] else ""
        avatar = props["Avatar"]["select"]["name"] if props["Avatar"]["select"] else ""
        video_type = props["Video Type"]["select"]["name"] if props["Video Type"]["select"] else ""
        lien_video = props["Lien Video"]["url"] if props["Lien Video"]["url"] else ""
        plateformes = [p["name"] for p in props["Plateforme"]["multi_select"]]
        slot = props["Slot"]["select"]["name"] if props["Slot"]["select"] else ""
        publication_date = props["Date Publication"]["date"]["start"] if props["Date Publication"]["date"] else ""

        print(f"\n--- {titre_notion} | {avatar} | Slot: {slot} | Platforms: {plateformes} ---")

        if avatar.lower() in SKIP_AVATARS_IN_MAIN_VIDEO_WORKFLOW:
            print(f"  Avatar {avatar} is handled by a dedicated workflow - skipping.")
            continue

        if video_type.lower() == "hyperframes":
            print("  Legacy HyperFrames row skipped by the manual-first publisher.")
            continue

        if not slot_is_due(slot, publication_date, now):
            print(f"  Slot {slot} not due yet - skipping.")
            continue

        if not lien_video:
            print("  PUBLISH ISSUE: missing_manual_video_link - no Lien Video; skipping row.")
            continue
        video_source = "drive"
        print("  Video source: Lien Video")

        if not script:
            print("  No script - skipping.")
            continue

        print("  Generating platform-specific content...")
        metadata = generate_metadata(script, avatar, plateformes, index, titre_notion)

        print("  Downloading video...")
        try:
            video_path = download_video(lien_video)
        except VideoDownloadError as exc:
            print(f"  PUBLISH ISSUE: {exc.code} - {exc}. Notion remains A publier; continuing with next row.")
            continue
        delete_video_after = True

        successes, failures, outcomes = publish_video_to_platforms(
            video_path,
            metadata,
            avatar,
            plateformes,
            titre_notion,
        )

        print(f"  Publish result: successes={successes} failures={failures}")
        final_status, final_reason = final_status_for_successes(successes, len(plateformes))
        print(f"  Final outcome: {final_status} ({final_reason})")
        notion_status_error = None
        if final_status in {"Publie", "Partiel", "Echec"}:
            try:
                if not slot_claimed:
                    claim_slot(selected_key)
                    slot_claimed = True
                set_notion_status(page_id, final_status)
            except Exception as exc:
                notion_status_error = f"{type(exc).__name__}: {sanitize_error(exc)}"
                print(f"  Notion status update failed: {notion_status_error}")
        else:
            print("  No terminal status computed - Notion NOT updated")

        report_file = append_publication_report(
            page_id=page_id,
            avatar=avatar,
            title=titre_notion,
            video_type=video_type,
            requested_platforms=plateformes,
            outcomes=outcomes,
            final_status=final_status,
            notion_status_error=notion_status_error,
        )
        print(f"  Publication report: {report_file}")

        if delete_video_after:
            os.remove(video_path)

    print("\nDone.")


if __name__ == "__main__":
    main()
