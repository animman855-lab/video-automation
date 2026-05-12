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


def main():
    tz = pytz.timezone("America/Toronto")
    now = datetime.now(tz)
    print(f"Heure actuelle (Montreal) : {now.strftime('%H:%M')}")
    print(f"DB ID: {repr(NOTION_DATABASE_ID)}")
    print(f"Token debut: {NOTION_TOKEN[:20]}")

    url = f"https://api.notion.com/v1/blocks/{NOTION_DATABASE_ID}/children"
    response = requests.get(url, headers=NOTION_HEADERS)
    print("STATUS:", response.status_code)
    print("RESPONSE:", response.text[:2000])


if __name__ == "__main__":
    main()
