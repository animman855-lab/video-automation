# Start Here

Use this file at the beginning of any new Codex conversation about Saloo English automation.

## Repo

- GitHub repo: `animman855-lab/video-automation`
- Local repo path: `C:\Users\yahya\Documents\video-automation-work`
- Main branch: `main`

## Read Order

For general work, read:

1. `docs/SYSTEM_OVERVIEW.md`
2. `docs/PUBLISHING_GITHUB_ACTIONS.md`
3. The system-specific doc for the task.

For HyperFrames video work, read:

1. `docs/HYPERFRAMES_VIDEO_SYSTEM.md`
2. `hyperframes/README.md`
3. Relevant files in `hyperframes/scripts/`

For Kayla ads work, read:

1. `docs/KAYLA_ADS_SYSTEM.md`
2. `publish_kayla_ads.py`
3. `hyperframes/scripts/run_kayla_postprocess.py`

For image generation and Drive upload work, read:

1. `docs/IMAGE_GENERATION_DRIVE_SYSTEM.md`
2. `docs/NOTION_CONTENT_PLANNING.md`

## Safety Rules

- Do not modify `publish.py` unless Yahya explicitly asks.
- Do not modify `publish_images.py` unless Yahya explicitly asks.
- Do not launch GitHub workflows unless Yahya explicitly asks.
- Do not change Notion unless Yahya explicitly asks.
- Do not publish or trigger Upload-Post unless Yahya explicitly asks.
- Keep normal publications protected: image quiz and normal video publishing should not be blocked by experimental HyperFrames work.
- If a HyperFrames avatar fails, the other avatars and image quiz should continue whenever possible.

## Current Priority

The system is moving from manual prototypes to reliable automation:

- TeacherRyan HyperFrames works with OCR + fallback arrow placement.
- Oliviaa HyperFrames works as drama dialogue.
- TheFluentBuild HyperFrames works as grandma correction.
- Cindy HyperFrames works as podcast listening practice.
- Kayla ads use a separate workflow and are manually generated Flow videos post-processed by HyperFrames.

## How To Work

Before changing code:

1. Run `git status --short`.
2. Inspect the relevant workflow/script.
3. Explain the likely change if the task is risky.
4. Make the smallest safe change.
5. Run syntax checks.
6. Commit and push only if Yahya asks or the task clearly requires it.

Never expose secret values in logs or messages.
