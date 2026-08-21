from __future__ import annotations

import re


class ScriptParseError(RuntimeError):
    pass


IGNORE_PREFIXES = (
    "green arrow",
    "voice says",
    "cta",
    "follow ",
)

SOURCE_LABEL_RE = re.compile(r"^(?:items?|phrases?)\s*:\s*", flags=re.IGNORECASE)


def parse_vocabulary_items(script: str) -> list[str]:
    """Extract spoken vocabulary items from the Notion Script field.

    The first comma-separated vocabulary line is treated as the source of truth.
    Instruction lines such as CTA or arrow notes are ignored.
    """

    candidates = []
    for raw_line in script.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lower = line.lower()
        if lower.startswith(IGNORE_PREFIXES):
            continue
        if "," in line:
            # Keep Notion readable while preventing metadata labels from becoming spoken text.
            candidates.append(SOURCE_LABEL_RE.sub("", line))

    if not candidates:
        raise ScriptParseError("No comma-separated vocabulary line found in Script.")

    source = candidates[0].strip().rstrip(".")
    items = []
    for part in source.split(","):
        item = re.sub(r"[^A-Za-z -]+", "", part).strip().lower()
        item = re.sub(r"\s+", " ", item)
        if item:
            items.append(item)

    if not items:
        raise ScriptParseError("Script vocabulary line did not contain usable items.")

    if len(set(items)) != len(items):
        raise ScriptParseError(f"Duplicate vocabulary items found: {items}")

    return items


def parse_vocabulary_cta(script: str, fallback: str) -> str:
    for raw_line in script.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = re.match(r"^CTA:\s*(.+)$", line, flags=re.IGNORECASE)
        if match:
            cta = match.group(1).strip().strip('"')
            if cta:
                return cta

    return fallback
