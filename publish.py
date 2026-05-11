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

# Mapping avatar → profil Upload-Post (sensible à la casse)
AVATAR_PROFILES = {
    "oliviaa":        "oliviaa",
    "thefluentbuild": "thefluentbuild",
    "teacherryan":    "teacherryan",
}

# ── Étape 1 : Déterminer le slot courant ────────────────
def get_current_slot():
    tz = pytz.timezone("America/Toronto")
    now = datetime.now(tz)
    hour = now.hour
    if hour == 8:
        return "08:00"
    elif hour == 16:
        return "16:00"
    elif hour == 0:
        return "00:00"
    else:
        return None

# ── Étape 2 : Lire Notion ───────────────────────────────
def get_videos_to_publish(slot):
    tz = pytz.timezone("America/Toronto")
    today = datetime.now(tz).strftime("%Y-%m-%d")

    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
    payload = {
        "filter": {
            "and": [
                {"property": "Statut",           "select": {"equals": "À publier"}},
                {"property": "Date Publication",  "date":   {"equals": today}},
                {"property": "Slot",              "select": {"equals": slot}},
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
        model="claude-sonnet-4-20250514",
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
    # Convertir l'URL Drive en lien de téléchargement direct
    if "drive.google.com/file/d/" in drive_url:
        file_id = drive_url.split("/file/d/")[1].split("/")[0]
    elif "id=" in drive_url:
        file_id = drive_url.split("id=")[1].split("&")[0]
    else:
        raise ValueError(f"URL Google Drive non reconnue : {drive_url}")

    download_url = f"https://drive.google.com/uc?export=download&id={file_id}"
    session = requests.Session()
    response = session.get(download_url, stream=True)

    # Gérer la confirmation Google pour les gros fichiers
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

    # Mapping plateformes Notion → clés Upload-Post
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
    slot = get_current_slot()
    if not slot:
        tz = pytz.timezone("America/Toronto")
        print(f"Aucun slot actif à {datetime.now(tz).strftime('%H:%M')} — rien à publier.")
        return

    print(f"Slot actif : {slot}")
    videos = get_videos_to_publish(slot)
    print(f"{len(videos)} vidéo(s) trouvée(s) à publier.")

    for video in videos:
        props = video["properties"]
        page_id = video["id"]

        # Extraire les données Notion
        titre_notion = props["Titre"]["title"][0]["text"]["content"] if props["Titre"]["title"] else "Sans titre"
        script       = props["Script"]["rich_text"][0]["text"]["content"] if props["Script"]["rich_text"] else ""
        avatar       = props["Avatar"]["select"]["name"] if props["Avatar"]["select"] else ""
        lien_video   = props["Lien Video"]["url"] if props["Lien Video"]["url"] else ""
        plateformes  = [p["name"] for p in props["Plateforme"]["multi_select"]]

        print(f"\nTraitement : {titre_notion} | Avatar : {avatar} | Plateformes : {plateformes}")

        if not lien_video:
            print(f"  ⚠️  Pas de lien vidéo — ignoré.")
            continue
        if not script:
            print(f"  ⚠️  Pas de script — ignoré.")
            continue

        # Générer titre + description
        print("  → Génération titre/description via Claude...")
        titre, description = generate_metadata(script)
        print(f"  → Titre : {titre}")
        print(f"  → Description : {description}")

        # Télécharger la vidéo
        print("  → Téléchargement vidéo depuis Google Drive...")
        video_path = download_video(lien_video)
        print(f"  → Vidéo téléchargée : {video_path}")

        # Publier
        print("  → Publication via Upload-Post...")
        result = publish_video(video_path, titre, description, avatar, plateformes)
        print(f"  → Résultat : {result}")

        # Mettre à jour Notion
        mark_as_published(page_id)
        print(f"  ✅ Notion mis à jour → Publié")

        # Nettoyer le fichier temporaire
        os.remove(video_path)

    print("\nTerminé.")

if __name__ == "__main__":
    main()
