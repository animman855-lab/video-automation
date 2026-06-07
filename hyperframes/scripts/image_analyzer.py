from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image


class ImageAnalysisError(RuntimeError):
    pass


@dataclass(frozen=True)
class GridAnalysis:
    targets: dict[str, tuple[int, int]]
    vertical_lines: list[int]
    horizontal_lines: list[int]
    cells_found: int


WIDTH = 1080
HEIGHT = 1920


def _cover_image(image: Image.Image) -> Image.Image:
    source = image.convert("RGB")
    src_ratio = source.width / source.height
    target_ratio = WIDTH / HEIGHT
    if src_ratio > target_ratio:
        new_h = HEIGHT
        new_w = int(new_h * src_ratio)
    else:
        new_w = WIDTH
        new_h = int(new_w / src_ratio)
    resized = source.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - WIDTH) // 2
    top = (new_h - HEIGHT) // 2
    return resized.crop((left, top, left + WIDTH, top + HEIGHT))


def _gray_line_score(pixel: tuple[int, int, int]) -> bool:
    r, g, b = pixel
    return 125 <= r <= 240 and 125 <= g <= 240 and 125 <= b <= 240 and max(pixel) - min(pixel) <= 32


def _group_peaks(indices: list[int], min_gap: int = 12) -> list[int]:
    if not indices:
        return []

    groups: list[list[int]] = [[indices[0]]]
    for index in indices[1:]:
        if index - groups[-1][-1] <= min_gap:
            groups[-1].append(index)
        else:
            groups.append([index])

    return [sum(group) // len(group) for group in groups]


def _find_vertical_lines(image: Image.Image) -> list[int]:
    width, height = image.size
    y_start = int(height * 0.05)
    y_end = int(height * 0.97)
    scores = []

    for x in range(width):
        hits = 0
        total = 0
        for y in range(y_start, y_end, 4):
            total += 1
            if _gray_line_score(image.getpixel((x, y))):
                hits += 1
        if total and hits / total > 0.38:
            scores.append(x)

    return [x for x in _group_peaks(scores) if width * 0.04 < x < width * 0.96]


def _find_horizontal_lines(image: Image.Image) -> list[int]:
    width, height = image.size
    x_start = int(width * 0.06)
    x_end = int(width * 0.94)
    scores = []

    for y in range(height):
        hits = 0
        total = 0
        for x in range(x_start, x_end, 4):
            total += 1
            if _gray_line_score(image.getpixel((x, y))):
                hits += 1
        if total and hits / total > 0.32:
            scores.append(y)

    return [y for y in _group_peaks(scores) if height * 0.03 < y < height * 0.99]


def _valid_gaps(lines: list[int], min_gap: int) -> bool:
    return len(lines) >= 2 and all(lines[i + 1] - lines[i] >= min_gap for i in range(len(lines) - 1))


def _trim_to_plausible_grid(lines: list[int], min_gap: int, min_lines: int) -> list[int]:
    if len(lines) < min_lines:
        raise ImageAnalysisError(f"Not enough grid lines found. Required {min_lines}, found {lines}")

    best = None
    best_score = float("-inf")
    for start in range(len(lines)):
        for end in range(start + min_lines, len(lines) + 1):
            candidate = lines[start:end]
            if not _valid_gaps(candidate, min_gap):
                continue
            gaps = [candidate[i + 1] - candidate[i] for i in range(len(candidate) - 1)]
            median_gap = sorted(gaps)[len(gaps) // 2]
            regularity = -sum(abs(gap - median_gap) for gap in gaps)
            span = candidate[-1] - candidate[0]
            score = regularity + span * 0.08 + len(candidate) * 30
            if score > best_score:
                best = candidate
                best_score = score

    if not best:
        raise ImageAnalysisError(f"Could not select plausible grid from lines: {lines}")
    return best


def analyze_vocabulary_grid(image_path: Path, items: list[str]) -> GridAnalysis:
    if not items:
        raise ImageAnalysisError("No vocabulary items were provided for image analysis.")

    image = _cover_image(Image.open(image_path))
    raw_vertical = _find_vertical_lines(image)
    raw_horizontal = _find_horizontal_lines(image)

    vertical = _trim_to_plausible_grid(raw_vertical, min_gap=120, min_lines=2)
    horizontal = _trim_to_plausible_grid(raw_horizontal, min_gap=120, min_lines=2)

    cols = len(vertical) - 1
    rows = len(horizontal) - 1
    cells_found = cols * rows
    if cells_found < len(items):
        raise ImageAnalysisError(
            f"Image grid has {cells_found} cells, but Script contains {len(items)} items."
        )

    targets: dict[str, tuple[int, int]] = {}
    for index, item in enumerate(items):
        row = index // cols
        col = index % cols
        x1, x2 = vertical[col], vertical[col + 1]
        y1, y2 = horizontal[row], horizontal[row + 1]
        cell_w = x2 - x1
        cell_h = y2 - y1
        # Point to the illustration area, above the bold label when present.
        targets[item] = (int(x1 + cell_w * 0.50), int(y1 + cell_h * 0.38))

    return GridAnalysis(
        targets=targets,
        vertical_lines=vertical,
        horizontal_lines=horizontal,
        cells_found=cells_found,
    )
