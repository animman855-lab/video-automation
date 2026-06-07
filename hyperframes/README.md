# HyperFrames Pilot

This module is isolated from the existing publisher scripts.

Current pilot:

- Avatar: `teacherryan`
- Topic: `animals`
- Date: `2026-06-07`
- Slot: `08:00`
- Video Type: `HyperFrames`
- Expected status before generation: `En cours`

The pilot starts in dry-run mode by default. Dry-run mode only validates the
Notion row and prints what would happen. It does not:

- update Notion
- generate audio
- render video
- upload to Google Drive
- call Upload-Post
- publish anything

## Required Secrets

The full production flow will need:

- `NOTION_TOKEN`
- `NOTION_DATABASE_ID`
- `GOOGLE_TTS_CREDENTIALS_JSON`
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GOOGLE_REFRESH_TOKEN`
- `GOOGLE_DRIVE_FOLDER_ID`

For the current dry-run, only `NOTION_TOKEN` and `NOTION_DATABASE_ID` are used.

## Run Dry-Run Locally

```powershell
python hyperframes/scripts/run_pilot.py
```

The script loads `.env` locally if present, then checks exactly one Notion row.
