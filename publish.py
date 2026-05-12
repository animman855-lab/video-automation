import os
import requests
from datetime import datetime
import pytz

NOTION_TOKEN = os.environ["NOTION_TOKEN"].strip()
NOTION_DATABASE_ID = os.environ["NOTION_DATABASE_ID"].strip()

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

def get_videos_to_publish():
    tz = pytz.timezone("America/Toronto")
    today = datetime.now(tz).strftime("%Y-%m-%d")

    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"

    payload = {
        "filter": {
            "and": [
                {
                    "property": "Statut",
                    "select": {
                        "equals": "À publier"
                    }
                },
                {
                    "property": "Date Publication",
                    "date": {
                        "equals": today
                    }
                }
            ]
        }
    }

    response = requests.post(
        url,
        headers=NOTION_HEADERS,
        json=payload
    )

    print("STATUS:", response.status_code)
    print("RESPONSE:", response.text)

    response.raise_for_status()

    return response.json().get("results", [])

videos = get_videos_to_publish()

print("VIDEOS TROUVÉES :", len(videos))
