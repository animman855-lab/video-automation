# New Conversation Prompts

Use these copy/paste prompts to start focused Codex conversations without bringing the full old chat history.

## General Automation Thread

```text
We are working on Saloo English automation.

Repo:
C:\Users\yahya\Documents\video-automation-work
GitHub:
animman855-lab/video-automation

Before doing anything, read:
docs/START_HERE.md
docs/SYSTEM_OVERVIEW.md
docs/PUBLISHING_GITHUB_ACTIONS.md

Important:
- Do not modify files unless I ask.
- Do not launch GitHub workflows unless I ask.
- Do not modify Notion unless I ask.
- Do not publish anything unless I ask.

My task is:
[WRITE TASK HERE]
```

## HyperFrames Video Thread

```text
We are working on the HyperFrames video system for Saloo English.

Repo:
C:\Users\yahya\Documents\video-automation-work

Before doing anything, read:
docs/START_HERE.md
docs/HYPERFRAMES_VIDEO_SYSTEM.md
docs/PUBLISHING_GITHUB_ACTIONS.md
hyperframes/README.md

Important:
- Do not touch publish.py or publish_images.py unless I explicitly ask.
- Do not launch workflows unless I explicitly ask.
- Keep TeacherRyan, Oliviaa, TheFluentBuild, Cindy separated.
- HyperFrames should not block image quiz publishing.

My task is:
[WRITE TASK HERE]
```

## TeacherRyan Thread

```text
We are working only on TeacherRyan HyperFrames.

Read:
docs/START_HERE.md
docs/HYPERFRAMES_VIDEO_SYSTEM.md
hyperframes/scripts/run_pilot.py
hyperframes/scripts/render_video.py
hyperframes/scripts/image_analyzer.py

Current TeacherRyan style:
- Visual Vocabulary Grid
- 10 items
- 2 columns x 5 rows
- green arrow
- OCR arrow targeting with fallback for 1-2 missed labels
- audio per item

Do not touch other avatars unless I ask.

My task is:
[WRITE TASK HERE]
```

## Kayla Ads Thread

```text
We are working only on Kayla Ads for Saloo English.

Repo:
C:\Users\yahya\Documents\video-automation-work

Read:
docs/START_HERE.md
docs/KAYLA_ADS_SYSTEM.md
docs/PUBLISHING_GITHUB_ACTIONS.md
publish_kayla_ads.py
hyperframes/scripts/run_kayla_postprocess.py

Important:
- Kayla uses a separate workflow.
- Do not touch normal publish.py or publish_images.py unless I ask.
- Flow source MP4 is in Notion Image HyperFrames.
- Final processed MP4 goes into Lien Video.
- Cards should come from explicit Card N lines in Script.
- Outro should be appended.

My task is:
[WRITE TASK HERE]
```

## Image Generation / Drive Upload Thread

```text
We are working only on source image generation and Google Drive upload for HyperFrames.

Repo:
C:\Users\yahya\Documents\video-automation-work

Read:
docs/START_HERE.md
docs/IMAGE_GENERATION_DRIVE_SYSTEM.md
docs/NOTION_CONTENT_PLANNING.md

Goal:
Generate/upload source images and fill Notion Image HyperFrames.

Important:
- Only update Image HyperFrames.
- Do not update Lien Video.
- Do not change Statut.
- Do not publish.
- Use the same Google account as HyperFrames but a separate Drive folder for images.

My task is:
[WRITE TASK HERE]
```

## Notion Planning Thread

```text
We are working only on Notion content planning for Saloo English.

Repo:
C:\Users\yahya\Documents\video-automation-work

Read:
docs/START_HERE.md
docs/NOTION_CONTENT_PLANNING.md
docs/SYSTEM_OVERVIEW.md

Important:
- Do not modify Notion until I approve exact changes.
- Avoid duplicates.
- Do not touch unrelated avatars.
- Do not rename columns.

My task is:
[WRITE TASK HERE]
```

## Publishing Bug Thread

```text
We are debugging a publishing/GitHub Actions issue.

Repo:
C:\Users\yahya\Documents\video-automation-work
GitHub:
animman855-lab/video-automation

Read:
docs/START_HERE.md
docs/PUBLISHING_GITHUB_ACTIONS.md
docs/TROUBLESHOOTING.md

Important:
- Read-only first.
- Do not modify code until the cause is clear.
- Do not launch workflows unless I ask.
- Use gh run logs to inspect failures.

Problem:
[PASTE ERROR OR RUN ID HERE]
```
