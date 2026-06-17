# Publishing And GitHub Actions

## Main Workflows

Normal content workflow:

- `.github/workflows/publish.yml`

Kayla ads workflow:

- `.github/workflows/publish_kayla_ads.yml`

## Normal Workflow Order

`publish.yml` currently runs:

1. Checkout repo
2. Setup Python
3. Install Python dependencies
4. Show server time
5. Run `publish_images.py`
6. Install Tesseract OCR
7. Run HyperFrames: `python hyperframes/scripts/run_pilot.py --execute`
8. Run `publish.py`

Important:

- `publish_images.py` runs before HyperFrames so image quiz posts are protected.
- HyperFrames is non-blocking.
- `publish.py` runs even if HyperFrames has an avatar failure.

## Root Scripts

- `publish.py` - publishes video posts from Notion.
- `publish_images.py` - publishes image quiz posts from Notion.
- `publish_kayla_ads.py` - publishes Kayla ads from Notion.

Avoid changing these unless the task explicitly requires it.

## Secrets

Do not reveal values.

Common GitHub secrets:

- `NOTION_TOKEN`
- `NOTION_DATABASE_ID`
- `NOTION_IMAGE_DATABASE_ID`
- `UPLOAD_POST_API_KEY`
- `ANTHROPIC_API_KEY`
- `GOOGLE_TTS_CREDENTIALS_JSON`
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GOOGLE_REFRESH_TOKEN`
- `GOOGLE_DRIVE_FOLDER_ID`

## Make.com

Make.com triggers GitHub workflows through GitHub REST API workflow dispatch.

HTTP request pattern:

- Method: `POST`
- URL: `https://api.github.com/repos/animman855-lab/video-automation/actions/workflows/<workflow-file>/dispatches`
- Header `Authorization: Bearer <GitHub token>`
- Header `Accept: application/vnd.github+json`
- Header `X-GitHub-Api-Version: 2022-11-28`
- Body:

```json
{"ref": "main"}
```

If Make returns `401 Unauthorized`, check the GitHub token and permissions.

## GitHub CLI

Useful read-only commands:

```powershell
gh run list --repo animman855-lab/video-automation --workflow publish.yml --limit 10
gh run view RUN_ID --repo animman855-lab/video-automation --log
```

Kayla:

```powershell
gh run list --repo animman855-lab/video-automation --workflow publish_kayla_ads.yml --limit 10
gh run view RUN_ID --repo animman855-lab/video-automation --log
```

## Common Safety Checks

Before changing code:

```powershell
git status --short
```

Before committing:

```powershell
python -m py_compile <changed-python-files>
git diff --check
git status --short
```

Do not launch workflows unless Yahya says to.
