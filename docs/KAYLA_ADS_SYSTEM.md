# Kayla Ads System

## Purpose

Kayla is no longer treated like a normal avatar brand. Kayla is used for Saloo English app UGC-style ads.

Yahya generates Flow videos manually, then the system post-processes them:

- downloads the Flow MP4 from Notion `Image HyperFrames`
- overlays smart cards/subtitle-style cards
- appends the Saloo English outro
- uploads final MP4 to Google Drive
- fills Notion `Lien Video`
- leaves `Statut = A publier`
- publishes with a dedicated Kayla workflow

## Why Separate Workflow

Kayla can publish many ads per day. It should not block or interfere with normal daily content.

Workflow:

- `.github/workflows/publish_kayla_ads.yml`

Scripts:

- `hyperframes/scripts/run_kayla_postprocess.py`
- `publish_kayla_ads.py`

## Notion Criteria

Kayla rows use:

- `Avatar = kayla`
- `Video Type = Visual Vocabulary`
- `Statut = A publier`
- `Image HyperFrames` contains the source Flow MP4 link
- `Lien Video` empty before post-processing

After post-processing:

- `Lien Video` is filled with final Drive MP4 link
- `Statut` stays `A publier`

After publishing:

- `publish_kayla_ads.py` marks `Statut = Publie` if at least two platforms succeed.

## Kayla Slots

Target slots:

- `08:00`
- `10:00`
- `12:00`
- `14:00`
- `16:00`
- `18:00`
- `20:00`
- `22:00`
- `00:00`
- `02:00`

The idea is to publish many ads per day but not all at once.

## Content Positioning

Kayla ads promote Saloo English without sounding like generic AI ads.

Core promise:

Saloo English helps people who understand English but freeze when they need to speak.

Content angles:

- mini lesson
- demo Saloo correction
- face-camera hook
- confession/personal struggle
- myth buster
- specific situation

## Cards

Kayla scripts now include explicit card lines such as:

- `Card 1: ...`
- `Card 2: ...`
- `Card 3: ...`

The post-processing script should use these card lines instead of generic repeated cards.

Rules:

- cards should be short
- cards should match what the avatar says
- avoid internal notes like `Hook:` or `CTA:` appearing visually
- avoid too many cards
- cards should look like native TikTok/UGC overlays

## Outro

Outro asset:

- `hyperframes/assets/kayla/saloo-outro.mp4`

Current rule:

- always append the Kayla outro asset.

Reason:

The earlier duration-based skip caused some videos to miss the outro.

## Safety

- Kayla workflow is separate.
- If one platform fails, ignore it.
- If at least two platforms publish, mark as `Publie`.
- Do not let Kayla block normal publishing.
