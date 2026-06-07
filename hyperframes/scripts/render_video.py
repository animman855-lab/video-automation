from __future__ import annotations

import math
import re
import subprocess
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont


WIDTH = 1080
HEIGHT = 1920
FPS = 30
SECONDS_PER_ANIMAL = 2.0
CTA_SECONDS = 4.0
MAX_DURATION = 30.0


def _drive_download_url(url: str) -> str:
    for pattern in [r"/file/d/([^/]+)", r"[?&]id=([^&]+)"]:
        match = re.search(pattern, url)
        if match:
            return f"https://drive.google.com/uc?export=download&id={match.group(1)}"
    return url


def download_image(image_url: str, output_path: Path) -> Path:
    response = requests.get(_drive_download_url(image_url), timeout=90)
    response.raise_for_status()
    output_path.write_bytes(response.content)
    return output_path


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


def _draw_current_word(draw: ImageDraw.ImageDraw, word: str, pulse: float) -> None:
    font = _font(72)
    label = word.upper()
    bbox = draw.textbbox((0, 0), label, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    padding_x = 44
    padding_y = 24
    box_w = text_w + padding_x * 2
    box_h = text_h + padding_y * 2
    x1 = (WIDTH - box_w) // 2
    y1 = 54
    x2 = x1 + box_w
    y2 = y1 + box_h
    outline_width = 6 + int(math.sin(pulse * math.pi * 2) * 2)

    draw.rounded_rectangle(
        (x1, y1, x2, y2),
        radius=28,
        fill=(255, 255, 255),
        outline=(28, 180, 85),
        width=outline_width,
    )
    draw.text((x1 + padding_x, y1 + padding_y - 7), label, fill=(10, 10, 10), font=font)


def _draw_arrow(draw: ImageDraw.ImageDraw, target: tuple[int, int], pulse: float) -> None:
    tx, ty = target
    offset = int(math.sin(pulse * math.pi * 2) * 7)
    if tx < WIDTH // 2:
        start = (tx - 180 + offset, ty - 115)
    else:
        start = (tx + 180 - offset, ty - 115)
    end = (tx, ty)
    color = (22, 196, 79)
    width = 18

    draw.line([start, end], fill=color, width=width)
    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    head_len = 48
    left = (
        end[0] - head_len * math.cos(angle - math.pi / 6),
        end[1] - head_len * math.sin(angle - math.pi / 6),
    )
    right = (
        end[0] - head_len * math.cos(angle + math.pi / 6),
        end[1] - head_len * math.sin(angle + math.pi / 6),
    )
    draw.polygon([end, left, right], fill=color)


def _draw_cta(draw: ImageDraw.ImageDraw) -> None:
    text = "Follow TeacherRyan for more English vocabulary."
    font = _font(52)
    box = (90, 1420, 990, 1625)
    draw.rounded_rectangle(box, radius=30, fill=(255, 255, 255), outline=(30, 30, 30), width=3)
    lines = ["Follow TeacherRyan", "for more English vocabulary."]
    y = 1460
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        draw.text(((WIDTH - (bbox[2] - bbox[0])) // 2, y), line, fill=(10, 10, 10), font=font)
        y += 62


def render_teacher_ryan_video(
    image_path: Path,
    audio_path: Path,
    output_path: Path,
    frames_dir: Path,
    items: list[str],
    item_targets: dict[str, tuple[int, int]],
) -> Path:
    import imageio_ffmpeg

    if not items:
        raise ValueError("Cannot render without vocabulary items.")

    frames_dir.mkdir(parents=True, exist_ok=True)
    base = _cover_image(image_path)
    item_duration = min(SECONDS_PER_ANIMAL * len(items), MAX_DURATION - CTA_SECONDS)
    duration = item_duration + CTA_SECONDS
    total_frames = int(FPS * duration)

    for frame_index in range(total_frames):
        seconds = frame_index / FPS
        frame = base.copy()
        draw = ImageDraw.Draw(frame)

        if seconds < item_duration:
            animal_index = min(int(seconds // SECONDS_PER_ANIMAL), len(items) - 1)
            animal = items[animal_index]
            pulse = (frame_index % FPS) / FPS
            _draw_arrow(draw, item_targets[animal], pulse)
            _draw_current_word(draw, animal, pulse)
        else:
            _draw_cta(draw)

        frame.save(frames_dir / f"frame_{frame_index:04d}.jpg", "JPEG", quality=92)

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        ffmpeg,
        "-y",
        "-framerate",
        str(FPS),
        "-i",
        str(frames_dir / "frame_%04d.jpg"),
        "-i",
        str(audio_path),
        "-t",
        f"{duration:.2f}",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        str(output_path),
    ]
    subprocess.run(cmd, check=True)
    return output_path
