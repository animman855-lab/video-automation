# Saloo English Automation - System Overview

## Goal

Saloo English is an automated content system for teaching English and promoting the Saloo English app.

The system uses:

- Notion as the central content database.
- GitHub Actions to run automation.
- Make.com to trigger GitHub workflows.
- Google Drive to store generated videos and source images.
- Upload-Post to publish to social platforms.

## Main Platforms

Publishing targets can include:

- YouTube
- Facebook
- Instagram
- TikTok
- Pinterest

Some tests may use only one platform, but Notion should usually control platform selection.

## Main Notion Database

Database: `Video Publishing`

Important columns:

- `Titre`
- `Avatar`
- `Date Publication`
- `Slot`
- `Video Type`
- `Statut`
- `Script`
- `Prompt 1`
- `Image HyperFrames`
- `Lien Video`
- `Plateforme`

Important statuses:

- `En cours` - content is being prepared/generated.
- `A publier` - ready for publishing.
- `Publie` - published successfully.

## Main Avatars / Brands

- `teacherryan` - Visual Vocabulary Grid.
- `oliviaa` / `oliviaaa` in some planning contexts - Drama Dialogue English.
- `thefluentbuild` - Grandma Corrects You in Real Life.
- `cindy` - Podcast Listening Practice.
- `kayla` - manual UGC ads for Saloo English app.

## Current Slot Logic

Common slots:

- `08:00`
- `16:00`
- `00:00`

Kayla ads also use:

- `10:00`
- `12:00`
- `14:00`
- `18:00`
- `20:00`
- `22:00`
- `02:00`

The code checks whether a slot is due before processing.

## Important Separation

Normal publishing:

- `publish.py` handles video publishing.
- `publish_images.py` handles image quiz publishing.

HyperFrames:

- Lives in `hyperframes/`.
- Should not be mixed with root publishing scripts.

Kayla ads:

- Use a separate workflow and script.
- Should not block normal publications.

## Current Strategy

Keep the system modular:

- HyperFrames generates videos and fills `Lien Video`.
- Publishing scripts publish rows that are already ready.
- Image generation can be handled in a separate image-focused conversation/workflow.
- Kayla ads use a separate workflow because they can run many times per day.
