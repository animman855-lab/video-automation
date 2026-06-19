# Publishing Watchdog

Read-only checker for the Saloo English publishing system.

## Purpose

This is the first loop-engineering layer for Saloo English.

It does not publish, edit Notion, upload to Drive, trigger Upload-Post, or modify any content. It only checks the current state and reports what looks blocked.

## Files

- `.github/workflows/publishing_watchdog.yml`
- `hyperframes/scripts/publishing_watchdog.py`

## What It Checks

For the selected `Date Publication`, it reads Notion rows and reports:

- rows still `En cours` after their slot started
- HyperFrames rows with `Image HyperFrames` present but no `Lien Video`
- HyperFrames rows missing `Image HyperFrames`
- rows `A publier` but missing `Lien Video`
- rows `A publier` but missing `Plateforme`
- rows `Publie` with suspicious missing video link
- recent GitHub runs for:
  - `publish.yml`
  - `publish_kayla_ads.yml`

## Telegram Alerts

The watchdog can send a short summary to Telegram after each run.

GitHub Secrets needed:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

If these secrets are missing, the workflow still succeeds and only skips Telegram.

### Create Telegram Bot

1. Open Telegram.
2. Search for `BotFather`.
3. Send `/newbot`.
4. Choose a bot name.
5. Copy the bot token.
6. Add it to GitHub Secrets as `TELEGRAM_BOT_TOKEN`.

### Get Chat ID

For a private chat:

1. Send any message to your new bot.
2. Open this URL in a browser, replacing TOKEN:

`https://api.telegram.org/botTOKEN/getUpdates`

3. Find `"chat":{"id":...}`.
4. Copy the numeric ID.
5. Add it to GitHub Secrets as `TELEGRAM_CHAT_ID`.

For a group chat, add the bot to the group, send a message in the group, then use the same `getUpdates` URL.

## Schedule

The workflow runs:

- manually with `workflow_dispatch`
- automatically every hour at minute 20 UTC

It is safe because it is read-only.

Recommended production trigger:

- Use Make.com to trigger `publishing_watchdog.yml` after important publishing windows.
- Keep the GitHub schedule only as a backup.

## Manual Run

In GitHub Actions, run:

`Publishing Watchdog`

Optional input:

`date = YYYY-MM-DD`

If no date is provided, it checks today's date in Montreal time.

## Local Run

From the repo root:

```bash
python hyperframes/scripts/publishing_watchdog.py
```

For a specific date:

```bash
python hyperframes/scripts/publishing_watchdog.py --date 2026-06-19
```

## Required Secrets

- `NOTION_TOKEN`
- `NOTION_DATABASE_ID`
- optional: `TELEGRAM_BOT_TOKEN`
- optional: `TELEGRAM_CHAT_ID`

GitHub run summaries use the built-in `GITHUB_TOKEN` inside GitHub Actions.

## Current Rule

The watchdog is only a checker.

It should not automatically fix Notion fields, relaunch workflows, or republish anything until the read-only reports are trusted.
