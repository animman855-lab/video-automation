import os
import requests
import re
import anthropic
from datetime import datetime
import pytz

NOTION_TOKEN = os.environ["NOTION_TOKEN"].strip()
NOTION_DATABASE_ID = os.environ["NOTION_DATABASE_ID"].strip()
UPLOAD_POST_API_KEY = os.environ["UPLOAD_POST_API_KEY"].strip()
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"].strip()

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

AVATAR_PROFILES = {
    "oliviaa": "oliviaa",
    "thefluentbuild": "thefluentbuild",
    "teacherryan": "teacherryan",
}

AVATAR_HASHTAG = {
    "oliviaa": "#oliviaa",
    "thefluentbuild": "#thefluentbuild",
    "teacherryan": "#teacherryan",
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

SLOT_HOURS = {
    "08:00": 8 * 60,
    "16:00": 16 * 60,
    "00:00": 0,
}


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

    avatar_hashtag = AVATAR_HASHTAG.get(avatar.lower(), "")
    youtube_hashtag = get_youtube_hashtag(video_index)

    avatar_context = {
        "oliviaa": "an elderly English teacher woman (Oliviaa) who teaches English vocabulary and pronunciation to beginners and ESL learners in a simple, clear and warm grandmotherly way.",
        "thefluentbuild": "a mature woman (TheFluentBuild) who teaches English fluency, speaking confidence and everyday conversation skills.",
        "teacherryan": "a business English teacher man (TeacherRyan) who teaches professional English, business vocabulary and communication skills.",
    }
    context = avatar_context.get(avatar.lower(), "an English teacher")
    platforms_str = ", ".join(plateformes)

    prompt = f"""You are a social media expert creating content for {context}

Based on the video script below, generate optimized content for these platforms: {platforms_str}

STRICT RULES PER PLATFORM:

=== YOUTUBE ===
TITLE rules:
- Use a VARIED pattern based on script content. Examples:
  * Don't Say "[wrong word]" ❌ Say This Instead {youtube_hashtag}
  * Stop Using "[phrase]" ❌ Native Speakers Say This {youtube_hashtag}
  * Most People Say "[X]" Wrong ❌ Here's The Right Way {youtube_hashtag}
  * Learn These [Topic] Words ❌ You Didn't Know {youtube_hashtag}
- Always end with exactly this hashtag: {youtube_hashtag}
- Max 90 characters total
- Adapt the pattern to match what the script is actually about

DESCRIPTION rules:
- Line 1: Repeat the title exactly
- Blank line
- 2-3 engaging intro sentences about the topic
- Blank line
- List with ✅ of 3-5 things viewers will learn (from the script)
- Blank line
- "Perfect for beginners, ESL learners, and anyone improving their English."
- "Watch, listen, and repeat to build strong vocabulary naturally."
- Blank line
- SEO keywords as comma-separated list (15-20 keywords related to the topic)
- Blank line
- Hashtags: #learnenglish #englishvocabulary #englishlesson #esl #spokenenglish {avatar_hashtag}

=== TIKTOK ===
TITLE rules:
- ONE line in CAPS, very punchy hook
- Vary the pattern based on script:
  * STOP SAYING "[X]" ❌
  * DON'T SAY THIS IN ENGLISH ❌
  * MOST PEOPLE SAY THIS WRONG ❌
  * YOU'RE PRONOUNCING THIS WRONG ❌
  * NATIVE SPEAKERS DON'T SAY "[X]" ❌
  * LEARN THIS NOW 🔥
- Adapt to match the script content
- Max 80 characters

DESCRIPTION rules:
- Very short, max 150 characters
- Quick list with dashes
- End with: #learnenglish #englishconversation #speakenglish #vocabulary #reallifeenglish #dailyenglish #LearnOnTikTok {avatar_hashtag}

=== INSTAGRAM ===
TITLE rules:
- Start with an intriguing question based on the script
- Add 1-2 relevant emojis
- Example: "Did you know most people say this wrong? 🤔"
- Adapt to the actual script topic

DESCRIPTION rules:
- Pedagogical list of points with emoji arrows (👉) - number of points based on script content
- Each point teaches something from the script
- Blank line
- End with engagement question: "Which [word/trick/expression] was new for you? 💬"
- Blank line
- Hashtags: #LearnEnglish #EnglishVocabulary #ESL #SpokenEnglish #EnglishLesson {avatar_hashtag} + 3-4 topic-specific hashtags

=== FACEBOOK ===
TITLE rules:
- Same as Instagram - intriguing question based on script
- Add 1-2 relevant emojis

DESCRIPTION rules:
- Same format as Instagram
- Pedagogical list with emoji arrows (👉)
- Engagement question at end
- Same hashtags as Instagram

Write everything in English.
Only generate sections for platforms in: {platforms_str}

Respond ONLY in this exact format:
YOUTUBE_TITLE: [title here]
YOUTUBE_DESCRIPTION: [description here]
TIKTOK_TITLE: [title here]
TIKTOK_DESCRIPTION: [description here]
INSTAGRAM_TITLE: [title here]
INSTAGRAM_DESCRIPTION: [description here]
FACEBOOK_TITLE: [title here]
FACEBOOK_DESCRIPTION: [description here]

SCRIPT:
{script}"""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )

    text = message.content[0].text
    result = {}
    current_key = None
    current_lines = []

    keys = ["YOUTUBE_TITLE", "YOUTUBE_DESCRIPTION", "TIKTOK_TITLE",
            "TIKTOK_DESCRIPTION", "INSTAGRAM_TITLE", "INSTAGRAM_DESCRIPTION",
            "FACEBOOK_TITLE", "FACEBOOK_DESCRIPTION"]

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
    }

    platform_key = platform_map.get(platform)
    if not platform_key:
        raise ValueError(f"Unknown platform: {platform}")

    print(f"  Publishing on: {platform_key}")
    url = "https://api.upload-post.com/api/upload"
    with open(video_path, "rb") as video_file:
        response = requests.post(
            url,
            headers={"Authorization": f"Apikey {UPLOAD_POST_API_KEY}"},
            data=[
                ("user", profile),
                ("title", titre),
                ("description", description),
                ("platform[]", platform_key),
            ],
            files={"video": ("video.mp4", video_file, "video/mp4")},
        )
    print(f"  UPLOAD STATUS {platform}: {response.status_code}")
    print(f"  UPLOAD RESPONSE {platform}: {response.text[:200]}")
    result = response.json()
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

        if not slot_is_due(slot):
            print(f"  Slot {slot} not due yet - skipping.")
            continue

        if not lien_video:
            print("  No video link - skipping.")
            continue

        if not script:
            print("  No script - skipping.")
            continue

        print("  Generating platform-specific content...")
        metadata = generate_metadata(script, avatar, plateformes, index)

        print("  Downloading video...")
        video_path = download_video(lien_video)

        all_success = True
        for platform in plateformes:
            platform_upper = platform.upper().replace(" ", "").replace("TIKTOK", "TIKTOK")
            titre = metadata.get(f"{platform_upper}_TITLE", "")
            description = metadata.get(f"{platform_upper}_DESCRIPTION", "")

            if not titre:
                titre = metadata.get("YOUTUBE_TITLE", titre_notion)
            if not description:
                description = metadata.get("YOUTUBE_DESCRIPTION", "")

            print(f"\n  [{platform}]")
            print(f"  Title: {titre[:80]}")
            print(f"  Description preview: {description[:100]}...")

            result = publish_video(video_path, titre, description, avatar, platform)

            if not (result.get("success") and result.get("status") != "failed"):
                all_success = False
                print(f"  Failed on {platform}")

        if all_success:
            mark_as_published(page_id)
        else:
            print("  Some platforms failed - Notion NOT updated")

        os.remove(video_path)

    print("\nDone.")


if __name__ == "__main__":
    main()
