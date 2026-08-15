# Content Registry

This folder stores the local anti-repetition registry for Saloo English content.

## Purpose

- Keep every used scenario in history, including published and imperfect rows.
- Detect exact or near repetitions before a new Notion batch is created.
- Separate `used` history from quality:
  - `keep`: usable reference.
  - `needs_improvement`: usable idea, but script or prompt needs work.
  - `repetition_risk`: keep in history, do not reuse closely.
- Keep the same format while changing the complete scenario, not just a few words.

## Local files

The generated `*.json` files are ignored by Git because they can contain business
content metadata. They remain available locally for audits. If GitHub automation
needs the registry later, store it in a private repository or move only the
non-sensitive fingerprints into a protected GitHub artifact/secret.

## Generate or refresh a registry

From the repository root:

```text
python hyperframes/scripts/content_registry.py --avatar oliviaa --from 2026-06-01 --to 2026-07-30 --write
```

The command is read-only against Notion. `--write` only creates or replaces the
local registry JSON; it never updates Notion, starts a workflow, or publishes.

## Content families

The registry distinguishes the content family from the repetition key. A family
may repeat; the complete scenario must still be new.

- Oliviaa: daily conversation, boundaries/safety, work/social, texting/phone,
  dating, public services, misunderstandings, light conflict, app-indirect,
  short hooks.
- Cindy: podcast story, work/social, travel/public, awkward misunderstandings,
  opinion/debate, listening practice.
- TeacherRyan: visual items, actions/commands, state affirmations,
  grammar contrasts.
- TheFluentBuild: Grandma corrections, real-life grammar, vocabulary usage.

## Preflight before Notion

Create a private candidate JSON and check it before writing anything to Notion:

```text
python hyperframes/scripts/content_registry.py --check-candidate path/to/candidates.json --registry content_registry/oliviaa.json
```

The result is `PASS` or `BLOCK` for every candidate. It blocks exact content,
reused scenario + setting, reused hooks, identical prompts, and scripts whose
stored word fingerprints are too similar. This check does not modify Notion.

It also checks the fixed composition rules and the script quality. A candidate
can be `WARN` when it is usable but needs human review, for example when a CTA
is not detected or a Cindy podcast looks too short. It is `BLOCK` when a fixed
visual rule is missing, a template label would be spoken, or a dialogue line is
duplicated.

Refresh format labels in an existing local registry:

```text
python hyperframes/scripts/content_registry.py --reclassify-local content_registry/oliviaa.json
```

## Decision rule for future batches

1. Load all historical rows for the avatar.
2. Block an exact fingerprint.
3. Flag a repeated scenario + setting, even when the dialogue was rewritten.
4. Flag a very similar hook or visual prompt.
5. Allow the same educational format when the complete situation is genuinely new.
6. Keep flagged rows in history; regenerate only rows that have not been produced
   yet and are explicitly approved for replacement.
