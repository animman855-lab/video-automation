# HyperFrames Video System

## Purpose

HyperFrames replaces part of the old HeyGen-style workflow. It generates local/composed MP4 videos from:

- `Script`
- `Prompt 1`
- `Image HyperFrames`
- Google TTS audio
- per-avatar renderers

Then it uploads the MP4 to Google Drive, writes the Drive link into Notion `Lien Video`, and sets `Statut = A publier` only after success.

## Workflow

GitHub Actions `publish.yml`:

1. Runs `publish_images.py` first.
2. Installs Tesseract OCR for TeacherRyan arrow detection.
3. Runs `python hyperframes/scripts/run_pilot.py --execute`.
4. Runs `publish.py`.

HyperFrames is configured as non-blocking in the workflow so normal publication can continue if one avatar fails.

## Core Files

- `hyperframes/scripts/run_pilot.py` - routes ready Notion rows by avatar.
- `hyperframes/scripts/render_video.py` - TeacherRyan visual vocabulary renderer.
- `hyperframes/scripts/render_oliviaa_drama.py` - Oliviaa drama renderer.
- `hyperframes/scripts/render_thefluentbuild_grandma.py` - TheFluentBuild grandma correction renderer.
- `hyperframes/scripts/render_cindy_podcast.py` - Cindy podcast renderer.
- `hyperframes/scripts/tts_google.py` - Google TTS.
- `hyperframes/scripts/drive_client.py` - Google Drive upload/public link.
- `hyperframes/scripts/notion_client.py` - Notion reads/updates.
- `hyperframes/scripts/image_analyzer.py` - TeacherRyan OCR/grid analysis.

## Ready Row Criteria

HyperFrames looks for Notion rows like:

- `Video Type = HyperFrames`
- `Statut = En cours`
- `Image HyperFrames` is filled
- `Lien Video` is empty
- `Date Publication = today`
- `Slot` is currently due
- Supported avatar

It processes a limited number per run and avoids multiple rows for the same avatar in the same run.

## TeacherRyan

Style:

- Visual Vocabulary Grid.
- 10 items for now.
- 2 columns x 5 rows.
- All labels visible from the start.
- Green arrow points to the current item.
- Audio is generated separately per item.
- CTA at end.

Current arrow logic:

1. OCR tries to read labels in the image.
2. If all labels are found, arrow targets use OCR positions.
3. If OCR misses 1 or 2 labels, those labels fall back to fixed grid coordinates.
4. If OCR misses more than 2 labels, TeacherRyan skips safely.

This protects against random arrows while avoiding failure from one OCR miss such as `mouse`.

Environment override:

- `TEACHERRYAN_ARROW_TARGET_MODE=fixed` forces fixed 2x5 targets.

## Oliviaa

Style:

- Drama Dialogue English.
- One scene image.
- Two characters.
- Dialogue bubbles one at a time.
- Audio per line.
- CTA final.
- No arrow.
- No 3D avatar or lip-sync.

Prompt rules:

- Oliviaa is the main character.
- The other person should currently be a man for pending empty image rows.
- Scenes can be drama, daily conversations, disputes, crime, intimacy, first date, relationship tension, or other real-life situations.
- Not only couple drama.

## TheFluentBuild

Style:

- Grandma Corrects You in Real Life.
- Elegant grandmother + male learner for now.
- Gentle correction flow.
- One bubble at a time.
- Bubble pointer should follow speaker side.
- Google TTS with separate grandma/learner voice if possible.

Content:

- Grammar, vocabulary, irregular verbs, real-life phrase corrections.
- Avoid repetition.
- Make scenes more vivid and polite/friendly.

## Cindy

Style:

- Podcast Listening Practice.
- 75 seconds target.
- Cindy + male guest.
- Modern podcast scene.
- Word/subtitle groups, waveform animation.
- CTA integrated naturally inside the script.

Content:

- Real conversations, not lessons.
- Mix of topics: daily life, work, relationships, travel, confidence, communication, small problems.
- Hooks alternate to avoid repetition.

## Current Safety Principles

- If video generation fails, do not upload to Drive.
- If Drive upload fails, do not update Notion.
- If public Drive link fails, do not set `A publier`.
- If one avatar fails, the other avatars should continue.
- Do not let HyperFrames block `publish_images.py`.
