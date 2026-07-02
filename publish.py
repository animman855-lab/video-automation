import os
import requests
import re
import anthropic
from datetime import datetime
from pathlib import Path
import pytz

NOTION_TOKEN = os.environ["NOTION_TOKEN"].strip()
NOTION_DATABASE_ID = os.environ["NOTION_DATABASE_ID"].strip()
UPLOAD_POST_API_KEY = os.environ["UPLOAD_POST_API_KEY"].strip()
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"].strip()

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

MIN_SUCCESSFUL_PLATFORMS = 2

YOUTUBE_HASHTAGS = [
    "#englishmastery",
    "#englishvocab",
    "#englishvoice",
    "#learnenglish",
    "#englishlesson",
    "#spokenenglish",
    "#englishspeaking",
]

SLOT_HOURS = {
    "08:00": 8 * 60,
    "10:00": 10 * 60,
    "12:00": 12 * 60,
    "16:00": 16 * 60,
    "20:00": 20 * 60,
    "22:00": 22 * 60,
    "00:00": 0,
}

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


def slot_is_due(slot_name):
    tz = pytz.timezone("America/Toronto")
    now = datetime.now(tz)
    current_minutes = now.hour * 60 + now.minute
    slot_minutes = SLOT_HOURS.get(slot_name)
    if slot_minutes is None:
        return False
    diff = current_minutes - slot_minutes
    if slot_minutes == 0:
        diff = current_minutes if current_minutes < 180 else -1
    return 0 <= diff <= 480


def get_videos_to_publish():
    tz = pytz.timezone("America/Toronto")
    today = datetime.now(tz).strftime("%Y-%m-%d")
    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
    payload = {
        "filter": {
            "and": [
                {"property": "Statut", "select": {"equals": "A publier"}},
                {"property": "Date Publication", "date": {"equals": today}},
            ]
        }
    }
    response = requests.post(url, headers=NOTION_HEADERS, json=payload)
    print("NOTION STATUT:", response.status_code)
    response.raise_for_status()
    return response.json().get("results", [])


def get_youtube_hashtag(index):
    return YOUTUBE_HASHTAGS[index % len(YOUTUBE_HASHTAGS)]


def generate_metadata(script, avatar, plateformes, video_index=0):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    avatar_hashtag = AVATAR_HASHTAG.get(avatar.lower(), f"#{avatar.lower()}")
    youtube_hashtag = get_youtube_hashtag(video_index)
    context = AVATAR_CONTEXT.get(avatar.lower(), "an English teacher")
    platforms_str = ", ".join(plateformes)

    prompt = f"""You are a social media expert creating content for {context}

Based on the video script below, generate optimized content for these platforms: {platforms_str}

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
- After the hook add: #learnenglish #englishvocabulary #englishspeakingpractice #english {avatar_hashtag}
- Max 150 characters total

DESCRIPTION rules:
- Leave completely empty.

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

Write everything in English.
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
{script}"""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2500,
        messages=[{"role": "user", "content": prompt}],
    )

    text = message.content[0].text
    result = {}
    current_key = None
    current_lines = []

    keys = [
        "YOUTUBE_TITLE", "YOUTUBE_DESCRIPTION",
        "TIKTOK_TITLE", "TIKTOK_DESCRIPTION",
        "INSTAGRAM_TITLE", "INSTAGRAM_DESCRIPTION",
        "FACEBOOK_TITLE", "FACEBOOK_DESCRIPTION",
        "PINTEREST_TITLE", "PINTEREST_DESCRIPTION",
    ]

    for line in text.splitlines():
        matched = False
        for key in keys:
            if line.startswith(f"{key}:"):
                if current_key:
                    result[current_key] = "\n".join(current_lines).strip()
                current_key = key
                current_lines = [line[len(f"{key}:"):].strip()]
                matched = True
                break
        if not matched and current_key:
            current_lines.append(line)

    if current_key:
        result[current_key] = "\n".join(current_lines).strip()

    return result


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
    response = session.get(download_url, stream=True)
    print(f"  Download status: {response.status_code}")
    print(f"  Content-Type: {response.headers.get('Content-Type', 'unknown')}")
    tmp_path = f"/tmp/video_{file_id}.mp4"
    with open(tmp_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=32768):
            if chunk:
                f.write(chunk)
    size = os.path.getsize(tmp_path)
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

    with open(video_path, "rb") as video_file:
        response = requests.post(
            url,
            headers={"Authorization": f"Apikey {UPLOAD_POST_API_KEY}"},
            data=data_params,
            files={"video": ("video.mp4", video_file, "video/mp4")},
        )

    print(f"  UPLOAD STATUS {platform}: {response.status_code}")
    print(f"  UPLOAD RESPONSE {platform}: {response.text[:200]}")

    try:
        result = response.json()
    except Exception:
        result = {"success": False, "message": "Invalid JSON response"}

    return result


def mark_as_published(page_id):
    url = f"https://api.notion.com/v1/pages/{page_id}"
    payload = {"properties": {"Statut": {"select": {"name": "Publie"}}}}
    response = requests.patch(url, headers=NOTION_HEADERS, json=payload)
    response.raise_for_status()
    print("  Notion updated -> Publie")


def main():
    tz = pytz.timezone("America/Toronto")
    now = datetime.now(tz)
    print(f"Current time (Montreal): {now.strftime('%H:%M')}")

    videos = get_videos_to_publish()
    print(f"{len(videos)} video(s) found.")
    print(f"Local HyperFrames output dir: {local_hyperframes_output_dir()}")

    for index, video in enumerate(videos):
        props = video["properties"]
        page_id = video["id"]

        titre_notion = props["Titre"]["title"][0]["plain_text"] if props["Titre"]["title"] else "No title"
        script = props["Script"]["rich_text"][0]["plain_text"] if props["Script"]["rich_text"] else ""
        avatar = props["Avatar"]["select"]["name"] if props["Avatar"]["select"] else ""
        lien_video = props["Lien Video"]["url"] if props["Lien Video"]["url"] else ""
        plateformes = [p["name"] for p in props["Plateforme"]["multi_select"]]
        slot = props["Slot"]["select"]["name"] if props["Slot"]["select"] else ""

        print(f"\n--- {titre_notion} | {avatar} | Slot: {slot} | Platforms: {plateformes} ---")

        if avatar.lower() in SKIP_AVATARS_IN_MAIN_VIDEO_WORKFLOW:
            print(f"  Avatar {avatar} is handled by a dedicated workflow - skipping.")
            continue

        if not slot_is_due(slot):
            print(f"  Slot {slot} not due yet - skipping.")
            continue

        local_video = local_hyperframes_video_path(video)
        if local_video.exists():
            video_source = "local"
            print(f"  Video source: local MP4 ({local_video})")
        elif lien_video:
            video_source = "drive"
            print("  Video source: Lien Video")
        else:
            print("  No local MP4 and no video link - skipping.")
            continue

        if not script:
            print("  No script - skipping.")
            continue

        print("  Generating platform-specific content...")
        metadata = generate_metadata(script, avatar, plateformes, index)

        delete_video_after = False
        if video_source == "local":
            video_path = str(local_video)
        else:
            print("  Downloading video...")
            video_path = download_video(lien_video)
            delete_video_after = True

        successes = 0
        failures = 0
        for platform in plateformes:
            platform_upper = platform.upper()
            titre = metadata.get(f"{platform_upper}_TITLE", "")
            description = metadata.get(f"{platform_upper}_DESCRIPTION", "")

            if not titre:
                titre = metadata.get("YOUTUBE_TITLE", titre_notion)
            if not description:
                description = metadata.get("YOUTUBE_DESCRIPTION", "")

            print(f"\n  [{platform}]")
            print(f"  Title: {titre[:80]}")
            print(f"  Description: {description[:100]}...")

            try:
                result = publish_video(video_path, titre, description, avatar, platform)
            except requests.RequestException as exc:
                failures += 1
                print(f"  Upload exception on {platform}: {type(exc).__name__}: {exc}")
                continue
            except Exception as exc:
                failures += 1
                print(f"  Unexpected upload exception on {platform}: {type(exc).__name__}: {exc}")
                continue

            if not isinstance(result, dict):
                failures += 1
                print(f"  Invalid upload response on {platform}: {result!r}")
                continue

            if not (result.get("success") and result.get("status") != "failed"):
                failures += 1
                print(f"  Failed on {platform}")
            else:
                successes += 1

        print(f"  Publish result: successes={successes} failures={failures}")
        if successes >= MIN_SUCCESSFUL_PLATFORMS:
            mark_as_published(page_id)
        else:
            print("  Not enough successful platforms - Notion NOT updated")

        if delete_video_after:
            os.remove(video_path)

    print("\nDone.")


if __name__ == "__main__":
    main()
