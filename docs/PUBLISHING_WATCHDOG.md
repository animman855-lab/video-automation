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

## Schedule

The workflow runs:

- manually with `workflow_dispatch`
- automatically every hour at minute 20 UTC

It is safe because it is read-only.

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

GitHub run summaries use the built-in `GITHUB_TOKEN` inside GitHub Actions.

## Current Rule

The watchdog is only a checker.

It should not automatically fix Notion fields, relaunch workflows, or republish anything until the read-only reports are trusted.
