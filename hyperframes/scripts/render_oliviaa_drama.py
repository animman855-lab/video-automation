from __future__ import annotations

import math
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from dialogue_parser import DialogueScript


WIDTH = 1080
HEIGHT = 1920
FPS = 30
DURATION_SECONDS = 25.0
CTA_SECONDS = 3.5


def _cover_image(image_path: Path) -> Image.Image:
    source = Image.open(image_path).convert("RGB")
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


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/Arial.ttf",
    ]:
        path = Path(candidate)
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _wrap_text(text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    probe = Image.new("RGB", (1, 1))
    draw = ImageDraw.Draw(probe)

    for word in words:
        candidate = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if bbox[2] - bbox[0] <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _bubble_position(index: int, box_w: int, box_h: int) -> tuple[int, int, str]:
    positions = [
        (72, 235, "left"),
        (WIDTH - box_w - 72, 360, "right"),
        (110, 500, "left"),
        (WIDTH - box_w - 110, 630, "right"),
    ]
    x, y, side = positions[index % len(positions)]
    y = min(y, 760 - box_h)
    return x, max(220, y), side


def _draw_bubble(draw: ImageDraw.ImageDraw, text: str, index: int, pulse: float) -> None:
    font = _font(48)
    max_text_width = 720
    lines = _wrap_text(text, font, max_text_width)
    line_height = 58
    padding_x = 38
    padding_y = 30
    text_w = 0
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        text_w = max(text_w, bbox[2] - bbox[0])
    box_w = min(max_text_width + padding_x * 2, max(420, text_w + padding_x * 2))
    box_h = len(lines) * line_height + padding_y * 2
    x, y, side = _bubble_position(index, box_w, box_h)
    shadow = 8

    scale = 1.0 + math.sin(pulse * math.pi) * 0.012
    center_x = x + box_w / 2
    center_y = y + box_h / 2
    scaled_w = box_w * scale
    scaled_h = box_h * scale
    x1 = int(center_x - scaled_w / 2)
    y1 = int(center_y - scaled_h / 2)
    x2 = int(center_x + scaled_w / 2)
    y2 = int(center_y + scaled_h / 2)

    draw.rounded_rectangle(
        (x1 + shadow, y1 + shadow, x2 + shadow, y2 + shadow),
        radius=32,
        fill=(0, 0, 0, 80),
    )
    draw.rounded_rectangle(
        (x1, y1, x2, y2),
        radius=32,
        fill=(255, 255, 255),
        outline=(235, 235, 235),
        width=3,
    )

    tail_y = y2 - 26
    if side == "left":
        tail = [(x1 + 72, tail_y), (x1 + 126, tail_y), (x1 + 72, tail_y + 58)]
    else:
        tail = [(x2 - 72, tail_y), (x2 - 126, tail_y), (x2 - 72, tail_y + 58)]
    draw.polygon(tail, fill=(255, 255, 255), outline=(235, 235, 235))

    text_y = y1 + padding_y - 4
    for line in lines:
        draw.text((x1 + padding_x, text_y), line, fill=(12, 12, 12), font=font)
        text_y += line_height


def _draw_cta(draw: ImageDraw.ImageDraw, cta: str) -> None:
    font = _font(52)
    lines = _wrap_text(cta, font, 760)
    line_height = 64
    box_w = 860
    box_h = len(lines) * line_height + 72
    x1 = (WIDTH - box_w) // 2
    y1 = 1290
    x2 = x1 + box_w
    y2 = y1 + box_h
    draw.rounded_rectangle((x1, y1, x2, y2), radius=34, fill=(255, 255, 255), outline=(28, 28, 28), width=3)
    y = y1 + 34
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        draw.text(((WIDTH - (bbox[2] - bbox[0])) // 2, y), line, fill=(10, 10, 10), font=font)
        y += line_height


def render_oliviaa_drama_video(
    image_path: Path,
    output_path: Path,
    frames_dir: Path,
    dialogue: DialogueScript,
) -> Path:
    import imageio_ffmpeg

    if not dialogue.lines:
        raise ValueError("Cannot render Oliviaa drama without dialogue lines.")

    frames_dir.mkdir(parents=True, exist_ok=True)
    base = _cover_image(image_path)
    dialogue_duration = DURATION_SECONDS - CTA_SECONDS
    line_duration = dialogue_duration / len(dialogue.lines)
    total_frames = int(FPS * DURATION_SECONDS)

    for frame_index in range(total_frames):
        seconds = frame_index / FPS
        frame = base.copy()
        draw = ImageDraw.Draw(frame, "RGBA")

        if seconds < dialogue_duration:
            line_index = min(int(seconds // line_duration), len(dialogue.lines) - 1)
            local = (seconds - (line_index * line_duration)) / line_duration
            _draw_bubble(draw, dialogue.lines[line_index], line_index, local)
        else:
            _draw_cta(draw, dialogue.cta)

        frame.save(frames_dir / f"frame_{frame_index:04d}.jpg", "JPEG", quality=92)

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        ffmpeg,
        "-y",
        "-framerate",
        str(FPS),
        "-i",
        str(frames_dir / "frame_%04d.jpg"),
        "-t",
        f"{DURATION_SECONDS:.2f}",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(output_path),
    ]
    subprocess.run(cmd, check=True)
    return output_path
