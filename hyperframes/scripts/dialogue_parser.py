from __future__ import annotations

import re
from dataclasses import dataclass


class DialogueParseError(RuntimeError):
    pass


@dataclass(frozen=True)
class DialogueScript:
    lines: list[str]
    cta: str


def parse_dialogue_script(script: str) -> DialogueScript:
    lines: list[str] = []
    cta = ""

    for raw_line in script.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        cta_match = re.match(r"^CTA:\s*(.+)$", line, flags=re.IGNORECASE)
        if cta_match:
            cta = cta_match.group(1).strip().strip('"')
            continue

        line_match = re.match(r"^Line\s+\d+(?:\s*\([^)]+\))?:\s*(.+)$", line, flags=re.IGNORECASE)
        if line_match:
            text = line_match.group(1).strip().strip('"')
            if text:
                lines.append(text)
            continue

        speaker_match = re.match(r"^(Person|Learner|Student|Grandma|Guest|Cindy|Oliviaa?):\s*(.+)$", line, flags=re.IGNORECASE)
        if speaker_match:
            text = speaker_match.group(2).strip().strip('"')
            if text:
                lines.append(text)

    if len(lines) < 4:
        raise DialogueParseError(f"Expected at least 4 dialogue lines, found {len(lines)}.")
    if len(lines) > 12:
        raise DialogueParseError(f"Expected at most 12 dialogue lines, found {len(lines)}.")
    if not cta:
        raise DialogueParseError("CTA line is required for dialogue rendering.")

    return DialogueScript(lines=lines, cta=cta)
