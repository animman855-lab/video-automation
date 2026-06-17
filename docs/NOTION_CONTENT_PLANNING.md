# Notion Content Planning

## Purpose

Notion controls what content should be generated and published.

Avoid duplications and avoid modifying unrelated avatars.

## Important Rules

- Do not rename Notion columns.
- Reuse existing columns whenever possible.
- Do not create new columns unless Yahya explicitly approves.
- Do not change old published rows unless asked.
- Archive old/unneeded `00:00` rows only when Yahya explicitly asks.

## Main Video Fields

- `Titre` - content title.
- `Avatar` - account/avatar.
- `Date Publication` - target date.
- `Slot` - target time.
- `Video Type` - content type.
- `Statut` - workflow status.
- `Script` - script/content used by automation.
- `Prompt 1` - image generation prompt.
- `Image HyperFrames` - public image or source video link for HyperFrames/Kayla.
- `Lien Video` - final MP4 link.
- `Plateforme` - publishing platforms.

## HyperFrames Planning

For HyperFrames rows:

- `Video Type = HyperFrames`
- `Statut = En cours`
- `Image HyperFrames = empty` until image is generated/uploaded
- `Lien Video = empty` until MP4 is generated/uploaded

After HyperFrames success:

- `Lien Video` filled
- `Statut = A publier`

## TeacherRyan Planning

Style:

- visual vocabulary grid
- 10 items for now
- clear labels
- 2 columns x 5 rows

Script format should start with the comma-separated items:

```text
computer, keyboard, mouse, printer, phone, calendar, folder, notepad, stapler, envelope.
Green arrow points to each item one by one.
Voice says each word clearly.
CTA: Follow TeacherRyan for more English vocabulary.
```

Prompt should strongly enforce:

- exactly 10 equal rectangular cells
- 2 columns and 5 rows
- visible borders
- one item per cell
- centered item
- readable label
- no extra objects
- no random layout

## Oliviaa Planning

Style:

- drama dialogue / real-life dialogue
- 2 characters
- one image scene
- 8-ish short lines
- CTA final

Important:

- English only
- no names in bubbles
- no French
- no explanation after dialogue
- vary scenes beyond couple drama

## Cindy Planning

Style:

- podcast listening practice
- 75 seconds target
- Cindy + male guest
- natural conversation
- CTA integrated naturally inside script

Avoid:

- robotic lessons
- repeated topics
- same hook every video

## TheFluentBuild Planning

Style:

- grandma corrects gently
- male learner for now
- real-life setting
- mistake -> correction -> why -> repetition -> validation

Avoid:

- repeated corrections
- dry grammar-only scripts
- scenes that feel static

## Kayla Planning

Kayla ads:

- manually generated Flow videos
- app promo / UGC style
- Notion `Image HyperFrames` holds the source MP4 link
- `Lien Video` holds the final processed MP4 link

Kayla scripts should include explicit `Card N:` lines for post-processing.
