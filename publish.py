import os
import requests
import anthropic
from datetime import datetime
import pytz

# ── Config ──────────────────────────────────────────────
NOTION_TOKEN        = os.environ["NOTION_TOKEN"]
NOTION_DATABASE_ID  = os.environ["NOTION_DATABASE_ID"]
UPLOAD_POST_API_KEY = os.environ["UPLOAD_POST_API_KEY"]
ANTHROPIC_API_KEY   = os.environ["ANTHROPIC_API_KEY"]

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

AVATAR_PROFILES = {
    "oliviaa":        "oliviaa",
    "thefluentbuild": "thefluentbuild",
    "teacherryan":    "teacherryan",
}

# Heure de chaque slot en minutes depuis minuit
SLOT_HOURS = {
    "08:00": 8 * 60,
    "16:00": 16 * 60,
    "00:00": 0,
}

# ── Étape 1 : Vérifier si un slot est dû ────────────────
def slot_is_due(slot_name):
    tz = pytz.timezone("America/Toronto")
    now = datetime.now(tz)
    current_minutes = now.hour * 60 + now.minute

    slot_minutes = SLOT_HOURS.get(slot_name)
    if slot_minutes is None:
        return False

    # Le slot est dû si l'heure actuelle est >= l'heure du slot
    # et pas plus de 3 heures après (pour éviter de republier)
    diff = current_minutes - slot_minutes
    if slot_minutes == 0:  # minuit
        diff = current_minutes if current_minutes < 180 else -1

    return 0 <= diff <= 180  # Dans les 3 heures suivant le slot

# ── Étape 2 : Lire Notion ───────────────────────────────
def get_videos_to_publish():
    tz = pytz.timezone("America/Toronto")
    today = datetime.now(tz).strftime("%Y-%m-%d")

    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
    payload = {
        "filter": {
            "and": [
                {"property": "Statut",          "select": {"equals": "À publier"}},
                {"property": "Date Publication", "date":   {"equals": today}},
            ]
        }
    }
    response = requests.post(url, headers=NOTION_HEADERS, json=payload)
    response.raise_for_status()
    return response.json().get("results", [])

# ── Étape 3 : Générer titre + description via Claude ────
def generate_metadata(script):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        messages=[
            {
                "role": "user",
                "content": (
                    "Tu es un expert en marketing de contenu vidéo pour les réseaux sociaux.\n"
                    "À partir du script ci-dessous, génère :\n"
                    "1. Un TITRE accrocheur (maximum 80 caractères)\n"
                    "2. Une DESCRIPTION engageante (maximum 200 caractères) avec 3-5 hashtags pertinents\n\n"
                    "Réponds UNIQUEMENT dans ce format exact :\n"
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

# ── Étape 4 : Télécharger la vidéo depuis Google Drive ──
def download_video(drive_url):
    if "drive.google.com/file/d/" in drive_url:
        file_id = drive_url.split("/file/d/")[1].split("/")[0]
    elif "id=" in drive_url:
        file_id = drive_url.split("id=")[1].split("&")[0]
    else:
        raise ValueError(f"URL Google Drive non reconnue : {drive_url}")

    download_url = f"https://drive.google.com/uc?export=download&id={file_id}"
    session = requests.Session()
    response = session.get(download_url, stream=True)

    for key, value in response.cookies.items():
        if key.startswith("download_warning"):
            download_url = f"{download_url}&confirm={value}"
            response = session.get(download_url, stream=True)
            break

    tmp_path = f"/tmp/video_{file_id}.mp4"
    with open(tmp_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=32768):
            if chunk:
                f.write(chunk)
    return tmp_path

# ── Étape 5 : Publier via Upload-Post ───────────────────
def publish_video(video_path, titre, description, avatar, plateformes):
    profile = AVATAR_PROFILES.get(avatar.lower())
    if not profile:
        raise ValueError(f"Avatar inconnu : {avatar}")

    platform_map = {
        "YouTube":   "youtube",
        "Facebook":  "facebook",
        "Instagram": "instagram",
        "TikTok":    "tiktok",
    }
    platforms = [platform_map[p] for p in plateformes if p in platform_map]

    url = "https://api.upload-post.com/v1/upload"
    with open(video_path, "rb") as video_file:
        response = requests.post(
            url,
            headers={"Authorization": f"Bearer {UPLOAD_POST_API_KEY}"},
            data={
                "profile":     profile,
                "title":       titre,
                "description": description,
                "platforms":   ",".join(platforms),
            },
            files={"video": video_file},
        )
    response.raise_for_status()
    return response.json()

# ── Étape 6 : Mettre à jour Notion → "Publié" ───────────
def mark_as_published(page_id):
    url = f"https://api.notion.com/v1/pages/{page_id}"
    payload = {
        "properties": {
            "Statut": {"select": {"name": "Publié"}}
        }
    }
    response = requests.patch(url, headers=NOTION_HEADERS, json=payload)
    response.raise_for_status()

# ── Main ─────────────────────────────────────────────────
def main():
    tz = pytz.timezone("America/Toronto")
    now = datetime.now(tz)
    print(f"Heure actuelle (Montréal) : {now.strftime('%H:%M')}")

    videos = get_videos_to_publish()
    print(f"{len(videos)} vidéo(s) trouvée(s) avec statut À publier aujourd'hui.")

    for video in videos:
        props = video["properties"]
        page_id = video["id"]

        titre_notion = props["Titre"]["title"][0]["text"]["content"] if props["Titre"]["title"] else "Sans titre"
        script       = props["Script"]["rich_text"][0]["text"]["content"] if props["Script"]["rich_text"] else ""
        avatar       = props["Avatar"]["select"]["name"] if props["Avatar"]["select"] else ""
        lien_video   = props["Lien Video"]["url"] if props["Lien Video"]["url"] else ""
        plateformes  = [p["name"] for p in props["Plateforme"]["multi_select"]]
        slot         = props["Slot"]["select"]["name"] if props["Slot"]["select"] else ""

        # Vérifier si le slot est dû
        if not slot_is_due(slot):
            print(f"  ⏳ Slot {slot} pas encore dû pour '{titre_notion}' — ignoré.")
            continue

        print(f"\nTraitement : {titre_notion} | Avatar : {avatar} | Slot : {slot} | Plateformes : {plateformes}")

        if not lien_video:
            print("  ⚠️  Pas de lien vidéo — ignoré.")
            continue
        if not script:
            print("  ⚠️  Pas de script — ignoré.")
            continue

        print("  → Génération titre/description via Claude...")
        titre, description = generate_metadata(script)
        print(f"  → Titre : {titre}")
        print(f"  → Description : {description}")

        print("  → Téléchargement vidéo depuis Google Drive...")
        video_path = download_video(lien_video)
        print(f"  → Vidéo téléchargée : {video_path}")

        print("  → Publication via Upload-Post...")
        result = publish_video(video_path, titre, description, avatar, plateformes)
        print(f"  → Résultat : {result}")

        mark_as_published(page_id)
        print("  ✅ Notion mis à jour → Publié")

        os.remove(video_path)

    print("\nTerminé.")

if __name__ == "__main__":
    main()
