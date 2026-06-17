# Troubleshooting

## HyperFrames Says Slot Not Due Yet

Cause:

The row exists, but current Toronto/Montreal time has not reached that slot window.

Example:

```text
Slot 16:00 not due yet - skipping.
```

Fix:

- wait until the slot is due
- or temporarily adjust Notion slot only if testing manually

## TeacherRyan Arrow Wrong

Current system:

- OCR reads the visible labels.
- If OCR misses 1 or 2 labels, fixed 2x5 fallback is used for those labels.
- If OCR misses too many labels, TeacherRyan skips.

Check logs for:

```text
TeacherRyan OCR hybrid arrow targets enabled
```

If OCR misses a visible word:

- make labels larger
- use high contrast black text
- avoid stylized fonts
- keep exact label spelling
- keep label inside its cell

## TeacherRyan Skipped

Possible cause:

```text
OCR could not find all Script labels
```

If missing 1 or 2 labels, fallback should now handle it.

If more than 2 labels are missing:

- image is probably too hard to read
- prompt should enforce clearer grid and labels

## Image Quiz Did Not Publish

Check:

- Did `publish_images.py` run?
- Were the rows due?
- Are image quiz rows in the correct image database?
- Are slots `00:00` allowed only between `00:00` and `03:00`?

Important:

`publish_images.py` should run before HyperFrames in `publish.yml`.

## Kayla Outro Missing

Current rule:

- Kayla post-processing always appends `hyperframes/assets/kayla/saloo-outro.mp4`.

If outro is missing:

- check that the latest commit is deployed
- check that source row went through `run_kayla_postprocess.py`
- check logs for `Appending Kayla outro asset.`

## Kayla Cards Too Generic

Current rule:

- Script should contain explicit `Card N:` lines.
- The post-processing script should use those lines.

Fix Notion `Script`, not `Prompt 1`, unless the visual generation itself is wrong.

## Make.com 401 Unauthorized

Cause:

GitHub token is invalid, expired, lacks permission, or header is wrong.

Check:

- `Authorization` header must be `Bearer <token>`
- token must have workflow/repo permissions
- workflow URL must match the file name

## Google Drive Refresh Token Problems

If Drive upload fails after credentials previously worked:

- refresh token may have been revoked
- OAuth client secret may have changed
- the wrong OAuth client may be used
- Drive folder ID may be wrong

Never paste full secret values into chat.
