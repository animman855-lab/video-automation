# Image Generation And Drive Upload System

## Purpose

This system prepares source images for HyperFrames videos.

The video workflow needs a public Google Drive image link in Notion:

- Column: `Image HyperFrames`

The image generation conversation should focus only on creating/uploading source images, not video rendering or publishing.

## Recommended Drive Structure

Use the same Google account and OAuth credentials as HyperFrames, but use a separate folder for images.

Recommended:

```text
Saloo English Automation
├── HyperFrames Videos
│   ├── TeacherRyan
│   ├── Oliviaa
│   ├── TheFluentBuild
│   └── Cindy
└── HyperFrames Images
    ├── TeacherRyan
    ├── Oliviaa
    ├── TheFluentBuild
    └── Cindy
```

Why:

- MP4 outputs and source images should not be mixed.
- Easier to audit.
- Easier to regenerate missing images.
- Same Google secrets can still be reused.

## Simple Workflow

1. Find a Notion row where `Image HyperFrames` is empty.
2. Read `Prompt 1`.
3. Generate the image.
4. Save image with clean name:
   - `TheFluentBuild - 2026-06-19 - 08-00.png`
5. Upload image to the correct Google Drive images folder.
6. Make it public/shareable.
7. Write the public link into Notion `Image HyperFrames`.
8. Do not touch `Lien Video`.
9. Do not touch `Statut`.
10. Do not publish.

## Safe First Implementation

Dry-run:

- find target rows
- confirm `Image HyperFrames` is empty
- show intended file name and Drive folder
- do not upload
- do not update Notion

Execute:

- upload image
- make public
- write `Image HyperFrames`

## Required Secrets

Do not paste secret values into chat.

Needed:

- `NOTION_TOKEN`
- `NOTION_DATABASE_ID`
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GOOGLE_REFRESH_TOKEN`
- image Drive folder ID, ideally one per root folder or per avatar

## Important Rules

- Only update `Image HyperFrames`.
- Never update `Lien Video`.
- Never set `Statut = A publier`.
- Never trigger Upload-Post.
- Never launch video workflows from the image generation conversation.

## Avatar Image Notes

TeacherRyan:

- exactly 10 equal rectangular cells
- 2 columns and 5 rows
- visible borders
- one item centered in each cell
- readable English label
- small/clean TeacherRyan brand mark, currently preferred at bottom/title-size only if requested

Oliviaa:

- one scene image
- Oliviaa visible
- second person visible, currently usually male for planned rows
- dramatic or real-life social situation
- space for dialogue bubbles
- no text inside image

TheFluentBuild:

- elegant grandma + male learner
- real-life scene
- warm/classy educational mood
- no text inside image
- space for bubbles

Cindy:

- modern podcast scene
- Cindy + male guest
- microphones visible
- no text inside image
- clean space for subtitles/waveform
