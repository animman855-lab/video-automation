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
    return 0 <= diff <= 180


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


def generate_metadata(script):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        messages=[
            {
                "role": "user",
                "content": (
                    "Tu es un expert en marketing de contenu video pour les reseaux sociaux.\n"
                    "A partir du script ci-dessous, genere :\n"
                    "1. Un TITRE accrocheur (maximum 30 caracteres STRICT)\n"
                    "2. Une DESCRIPTION (maximum 20 caracteres STRICT, pas de hashtags)\n\n"
                    "Reponds UNIQUEMENT dans ce format exact :\n"
                    "TITRE: [titre ici]\n"
                    "DESCRIPTION: [description ici]\n\n"
                    f"SCRIPT:\n{script}"
                ),
            }
        ],
    )
    text = message.content[0].text
    titre = ""
    description = ""
    for line in text.splitlines():
        if line.startswith("TITRE:"):
            titre = line.replace("TITRE:", "").strip()
        elif line.startswith("DESCRIPTION:"):
            description = line.replace("DESCRIPTION:", "").strip()
    return titre, description


def get_drive_file_id(drive_url):
    match = re.search(r"/d/([a-zA-Z0-9_-]+)", drive_url)
    if match:
        return match.group(1)
    match = re.search(r"id=([a-zA-Z0-9_-]+)", drive_url)
    if match:
        return match.group(1)
    raise ValueError(f"Impossible d'extraire le file ID depuis : {drive_url}")


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
    print(f"  Video telechargee: {tmp_path} ({size} bytes)")
    return tmp_path


def publish_video(video_path, titre, description, avatar, plateformes):
    profile = AVATAR_PROFILES.get(avatar.lower())
    if not profile:
        raise ValueError(f"Avatar inconnu : {avatar}")
    platform_map = {
        "YouTube": "youtube",
        "Facebook": "facebook",
        "Instagram": "instagram",
        "TikTok": "tiktok",
    }
    platforms = [platform_map[p] for p in plateformes if p in platform_map]
    print(f"  Publication sur: {platforms}")
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
    print("UPLOAD STATUT:", response.status_code)
    print("UPLOAD REPONSE:", response.text)
    result = response.json()
    return result


def mark_as_published(page_id):
    url = f"https://api.notion.com/v1/pages/{page_id}"
    payload = {"properties": {"Statut": {"select": {"name": "Publie"}}}}
    response = requests.patch(url, headers=NOTION_HEADERS, json=payload)
    response.raise_for_status()
    print("  Notion mis a jour -> Publie")


def main():
    tz = pytz.timezone("America/Toronto")
    now = datetime.now(tz)
    print(f"Heure actuelle (Montreal) : {now.strftime('%H:%M')}")

    videos = get_videos_to_publish()
    print(f"{len(videos)} video(s) trouvee(s).")

    for video in videos:
        props = video["properties"]
        page_id = video["id"]

        titre_notion = props["Titre"]["title"][0]["plain_text"] if props["Titre"]["title"] else "Sans titre"
        script = props["Script"]["rich_text"][0]["plain_text"] if props["Script"]["rich_text"] else ""
        avatar = props["Avatar"]["select"]["name"] if props["Avatar"]["select"] else ""
        lien_video = props["Lien Video"]["url"] if props["Lien Video"]["url"] else ""
        plateformes = [p["name"] for p in props["Plateforme"]["multi_select"]]
        slot = props["Slot"]["select"]["name"] if props["Slot"]["select"] else ""

        print(f"\n--- {titre_notion} | {avatar} | Slot: {slot} ---")

        if not slot_is_due(slot):
            print(f"  Slot {slot} pas encore du - ignore.")
            continue

        if not lien_video:
            print("  Pas de lien video - ignore.")
            continue

        if not script:
            print("  Pas de script - ignore.")
            continue

        print("  Generation titre/description...")
        titre, description = generate_metadata(script)
        print(f"  Titre: {titre}")
        print(f"  Description: {description}")

        print("  Telechargement video...")
        video_path = download_video(lien_video)

        print("  Publication...")
        result = publish_video(video_path, titre, description, avatar, plateformes)
        print(f"  Resultat: {result}")

        if result.get("success") and result.get("status") != "failed":
            mark_as_published(page_id)
        else:
            print("  Publication echouee - Notion NON mis a jour")

        os.remove(video_path)

    print("\nTermine.")


if __name__ == "__main__":
    main()
