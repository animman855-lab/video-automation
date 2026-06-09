from __future__ import annotations

import re
from dataclasses import dataclass


class PodcastParseError(RuntimeError):
    pass


@dataclass(frozen=True)
class PodcastLine:
    speaker: str
    text: str


@dataclass(frozen=True)
class PodcastScript:
    lines: list[PodcastLine]


def parse_podcast_script(script: str) -> PodcastScript:
    lines: list[PodcastLine] = []

    for raw_line in script.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        match = re.match(r"^(Cindy|Guest|Host|Speaker\s*[AB]):\s*(.+)$", line, flags=re.IGNORECASE)
        if match:
            speaker = match.group(1).strip().lower().replace(" ", "")
            text = match.group(2).strip().strip('"')
            if text:
                lines.append(PodcastLine(speaker=speaker, text=text))
            continue

        numbered = re.match(r"^Line\s+\d+\s*\((Cindy|Guest|Host|Speaker\s*[AB])\):\s*(.+)$", line, flags=re.IGNORECASE)
        if numbered:
            speaker = numbered.group(1).strip().lower().replace(" ", "")
            text = numbered.group(2).strip().strip('"')
            if text:
                lines.append(PodcastLine(speaker=speaker, text=text))

    if len(lines) < 10:
        raise PodcastParseError(f"Expected at least 10 Cindy podcast lines, found {len(lines)}.")
    if len(lines) > 28:
        raise PodcastParseError(f"Expected at most 28 Cindy podcast lines, found {len(lines)}.")

    return PodcastScript(lines=lines)
