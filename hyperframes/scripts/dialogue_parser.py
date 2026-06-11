from __future__ import annotations

import re
from dataclasses import dataclass, field


class DialogueParseError(RuntimeError):
    pass


@dataclass(frozen=True)
class DialogueScript:
    lines: list[str]
    cta: str
    speakers: list[str] = field(default_factory=list)


def parse_dialogue_script(script: str) -> DialogueScript:
    lines: list[str] = []
    speakers: list[str] = []
    cta = ""

    for raw_line in script.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        cta_match = re.match(r"^CTA:\s*(.+)$", line, flags=re.IGNORECASE)
        if cta_match:
            cta = cta_match.group(1).strip().strip('"')
            continue

        line_match = re.match(r"^Line\s+\d+(?:\s*\(([^)]+)\))?:\s*(.+)$", line, flags=re.IGNORECASE)
        if line_match:
            speaker = (line_match.group(1) or "").strip().lower()
            text = line_match.group(2).strip().strip('"')
            if text:
                lines.append(text)
                speakers.append(_normalize_speaker(speaker, len(lines)))
            continue

        speaker_match = re.match(
            r"^(Person|Male|Man|Boy|Learner|Student|Grandma|Guest|Cindy|Oliviaa?):\s*(.+)$",
            line,
            flags=re.IGNORECASE,
        )
        if speaker_match:
            speaker = speaker_match.group(1).strip().lower()
            text = speaker_match.group(2).strip().strip('"')
            if text:
                lines.append(text)
                speakers.append(_normalize_speaker(speaker, len(lines)))

    if len(lines) < 4:
        raise DialogueParseError(f"Expected at least 4 dialogue lines, found {len(lines)}.")
    if len(lines) > 12:
        raise DialogueParseError(f"Expected at most 12 dialogue lines, found {len(lines)}.")
    if not cta:
        raise DialogueParseError("CTA line is required for dialogue rendering.")

    return DialogueScript(lines=lines, cta=cta, speakers=speakers)


def _normalize_speaker(speaker: str, line_number: int) -> str:
    if speaker in {"olivia", "oliviaa", "oliviaaa", "cindy", "grandma"}:
        return speaker
    if speaker in {"male", "man", "boy", "guest", "person", "learner", "student"}:
        return "male"
    return "oliviaa" if line_number % 2 == 1 else "male"
