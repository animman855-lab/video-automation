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


def generate_metadata(script, avatar):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    avatar_context = {
        "oliviaa": "an elderly English teacher woman who teaches English vocabulary and pronunciation to beginners and ESL learners in a simple, clear and warm way.",
        "thefluentbuild": "a mature woman who teaches English fluency, speaking confidence and everyday conversation skills.",
        "teacherryan": "a business English teacher man who teaches professional English, business vocabulary and communication skills.",
    }

    context = avatar_context.get(avatar.lower(), "an English teacher")

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1000,
        messages=[
            {
                "role": "user",
                "content": (
                    f"You are a social media expert creating content for {context}\n\n"
                    "Based on the video script below, generate:\n\n"
                    "1. TITLE: An engaging, curiosity-driven title with 1-2 relevant emojis. "
                    "Max 90 characters. Examples: '99% of People Don't Know These Egg Names 🥚🍳', "
                    "'Most people say eating... but that's not always correct ❌ | English vocabulary'\n\n"
                    "2. DESCRIPTION: Write in this exact structure:\n"
                    "- First: 1 engaging hook sentence about the video topic\n"
                    "- Then: 2-3 sentences describing what viewers will learn\n"
                    "- Then: 1 sentence about who this is perfect for (ESL learners, beginners, etc.)\n"
                    "- Then: List the key vocabulary words from the script (if applicable)\n"
                    "- Then: SEO keywords as a comma-separated list (15-20 keywords related to the topic)\n"
                    "- Then: 5-8 hashtags: #learnenglish #englishvocabulary #englishlesson #esl and topic-specific ones\n"
                    "- End with: Follow for more videos!\n\n"
                    "Max 2000 characters total for description.\n"
                    "Write everything in English.\n\n"
                    "Respond ONLY in this exact format:\n"
                    "TITLE: [title here]\n"
                    "DESCRIPTION: [description here]\n\n"
                    f"SCRIPT:\n{script}"
                ),
            }
        ],
    )
    text = message.content[0].text
    titre = ""
    description = ""
    lines = text.splitlines()
    desc_lines = []
    in_desc = False
    for line in lines:
        if line.startswith("TITLE:"):
            titre = line.replace("TITLE:", "").strip()
        elif line.startswith("DESCRIPTION:"):
            in_desc = True
            first = line.replace("DESCRIPTION:", "").strip()
            if first:
                desc_lines.append(first)
        elif in_desc:
            desc_lines.append(line)
    description = "\n".join(desc_lines).strip()
    return titre, description


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


def publish_video(video_path, titre, description, avatar, plateformes):
    profile = AVATAR_PROFILES.get(avatar.lower())
    if not profile:
        raise ValueError(f"Unknown avatar: {avatar}")
    platform_map = {
        "YouTube": "youtube",
        "Facebook": "facebook",
        "Instagram": "instagram",
        "TikTok": "tiktok",
    }
    platforms = [platform_map[p] for p in plateformes if p in platform_map]
    print(f"  Publishing on: {platforms}")
    url = "https://api.upload-post.com/api/upload"
    with open(video_path, "rb") as video_file:
        response = requests.post(
            url,
            headers={"Authorization": f"Apikey {UPLOAD_POST_API_KEY}"},
            data=[
                ("user", profile),
                ("title", titre),
                ("description", description),
            ] + [("platform[]", p) for p in platforms],
            files={"video": video_file},
        )
    print("UPLOAD STATUS:", response.status_code)
    print("UPLOAD RESPONSE:", response.text)
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

    for video in videos:
        props = video["properties"]
        page_id = video["id"]

        titre_notion = props["Titre"]["title"][0]["plain_text"] if props["Titre"]["title"] else "No title"
        script = props["Script"]["rich_text"][0]["plain_text"] if props["Script"]["rich_text"] else ""
        avatar = props["Avatar"]["select"]["name"] if props["Avatar"]["select"] else ""
        lien_video = props["Lien Video"]["url"] if props["Lien Video"]["url"] else ""
        plateformes = [p["name"] for p in props["Plateforme"]["multi_select"]]
        slot = props["Slot"]["select"]["name"] if props["Slot"]["select"] else ""

        print(f"\n--- {titre_notion} | {avatar} | Slot: {slot} ---")

        if not slot_is_due(slot):
            print(f"  Slot {slot} not due yet - skipping.")
            continue

        if not lien_video:
            print("  No video link - skipping.")
            continue

        if not script:
            print("  No script - skipping.")
            continue

        print("  Generating title/description...")
        titre, description = generate_metadata(script, avatar)
        print(f"  Title: {titre}")
        print(f"  Description preview: {description[:150]}...")

        print("  Downloading video...")
        video_path = download_video(lien_video)

        print("  Publishing...")
        result = publish_video(video_path, titre, description, avatar, plateformes)
        print(f"  Result: {result}")

        if result.get("success") and result.get("status") != "failed":
            mark_as_published(page_id)
        else:
            print("  Publication failed - Notion NOT updated")

        os.remove(video_path)

    print("\nDone.")


if __name__ == "__main__":
    main()
