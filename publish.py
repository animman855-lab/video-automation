import os
import requests
import json

NOTION_TOKEN = os.environ["NOTION_TOKEN"].strip()

headers = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

response = requests.post(
    "https://api.notion.com/v1/search",
    headers=headers,
    json={
        "filter": {
            "value": "database",
            "property": "object"
        }
    }
)

print("STATUS:", response.status_code)
print(json.dumps(response.json(), indent=2))
