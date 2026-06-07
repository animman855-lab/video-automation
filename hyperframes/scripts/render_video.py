from __future__ import annotations

import math
import re
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont


WIDTH = 1080
HEIGHT = 1920
FPS = 30
MIN_SECONDS_PER_ITEM = 2.0
ITEM_SILENCE_SECONDS = 0.8
CTA_SECONDS = 4.0
MAX_ITEMS = 20
ITEMS_PER_PAGE = 10


@dataclass(frozen=True)
class ItemSegment:
    item: str
    start: float
    end: float
    page: int


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


def _draw_page_marker(draw: ImageDraw.ImageDraw, page: int, page_count: int) -> None:
    if page_count <= 1:
        return

    font = _font(30)
    label = f"{page}/{page_count}"
    bbox = draw.textbbox((0, 0), label, font=font)
    padding_x = 22
    padding_y = 12
    x2 = WIDTH - 54
    y1 = 68
    x1 = x2 - (bbox[2] - bbox[0]) - padding_x * 2
    y2 = y1 + (bbox[3] - bbox[1]) + padding_y * 2
    draw.rounded_rectangle(
        (x1, y1, x2, y2),
        radius=18,
        fill=(255, 255, 255),
        outline=(28, 180, 85),
        width=3,
    )
    draw.text((x1 + padding_x, y1 + padding_y - 3), label, fill=(10, 10, 10), font=font)


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


def _convert_to_wav(ffmpeg: str, source: Path, output_path: Path) -> Path:
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(source),
        "-ac",
        "1",
        "-ar",
        "24000",
        "-sample_fmt",
        "s16",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return output_path


def _write_silence(writer: wave.Wave_write, seconds: float) -> None:
    frames = max(0, int(writer.getframerate() * seconds))
    writer.writeframes(b"\x00" * frames * writer.getnchannels() * writer.getsampwidth())


def _build_synced_audio(
    ffmpeg: str,
    item_audio_paths: dict[str, Path],
    items: list[str],
    output_audio_path: Path,
    audio_work_dir: Path,
) -> tuple[Path, list[ItemSegment], float]:
    if len(items) > MAX_ITEMS:
        raise ValueError(f"TeacherRyan HyperFrames supports at most {MAX_ITEMS} items, got {len(items)}.")

    missing = [item for item in items if item not in item_audio_paths]
    if missing:
        raise ValueError(f"Missing item audio files for: {', '.join(missing)}")

    audio_work_dir.mkdir(parents=True, exist_ok=True)
    normalized: list[tuple[str, Path]] = []
    for index, item in enumerate(items, start=1):
        source = item_audio_paths[item]
        if not source.exists() or source.stat().st_size <= 0:
            raise ValueError(f"Item audio is missing or empty for '{item}'.")
        wav_path = audio_work_dir / f"{index:02d}_{re.sub(r'[^a-z0-9]+', '-', item.lower()).strip('-')}.wav"
        normalized.append((item, _convert_to_wav(ffmpeg, source, wav_path)))

    current = 0.0
    segments: list[ItemSegment] = []
    expected_params = None

    with wave.open(str(output_audio_path), "wb") as writer:
        for index, (item, wav_path) in enumerate(normalized):
            with wave.open(str(wav_path), "rb") as reader:
                params = reader.getparams()
                if expected_params is None:
                    expected_params = params
                    writer.setparams(params)
                elif params[:3] != expected_params[:3]:
                    raise ValueError(f"Inconsistent audio format for '{item}'.")

                frames_count = reader.getnframes()
                duration = frames_count / reader.getframerate()
                if duration <= 0:
                    raise ValueError(f"Item audio has invalid duration for '{item}'.")

                segment_duration = max(duration + ITEM_SILENCE_SECONDS, MIN_SECONDS_PER_ITEM)
                start = current
                end = current + segment_duration
                page = (index // ITEMS_PER_PAGE) + 1

                writer.writeframes(reader.readframes(frames_count))
                _write_silence(writer, segment_duration - duration)

                segments.append(ItemSegment(item=item, start=start, end=end, page=page))
                current = end

        _write_silence(writer, CTA_SECONDS)

    if not segments:
        raise ValueError("No audio segments were created.")

    return output_audio_path, segments, current + CTA_SECONDS


def _active_segment(segments: list[ItemSegment], seconds: float) -> ItemSegment | None:
    for segment in segments:
        if segment.start <= seconds < segment.end:
            return segment
    return None


def render_teacher_ryan_video(
    image_path: Path,
    item_audio_paths: dict[str, Path],
    output_path: Path,
    frames_dir: Path,
    items: list[str],
    item_targets: dict[str, tuple[int, int]],
) -> Path:
    import imageio_ffmpeg

    if not items:
        raise ValueError("Cannot render without vocabulary items.")

    if len(items) > MAX_ITEMS:
        raise ValueError(f"Cannot render more than {MAX_ITEMS} vocabulary items.")

    frames_dir.mkdir(parents=True, exist_ok=True)
    audio_work_dir = frames_dir.parent / "audio_normalized"
    base = _cover_image(image_path)
    page_count = math.ceil(len(items) / ITEMS_PER_PAGE)
    if page_count > 2:
        raise ValueError("TeacherRyan visual vocabulary supports only one or two pages.")

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    synced_audio_path, segments, duration = _build_synced_audio(
        ffmpeg=ffmpeg,
        item_audio_paths=item_audio_paths,
        items=items,
        output_audio_path=frames_dir.parent / "teacherryan-synced-audio.wav",
        audio_work_dir=audio_work_dir,
    )
    total_frames = int(FPS * duration)

    for frame_index in range(total_frames):
        seconds = frame_index / FPS
        frame = base.copy()
        draw = ImageDraw.Draw(frame)
        segment = _active_segment(segments, seconds)

        if segment:
            animal = segment.item
            pulse = (frame_index % FPS) / FPS
            _draw_page_marker(draw, segment.page, page_count)
            _draw_arrow(draw, item_targets[animal], pulse)
            _draw_current_word(draw, animal, pulse)
        else:
            _draw_cta(draw)

        frame.save(frames_dir / f"frame_{frame_index:04d}.jpg", "JPEG", quality=92)

    cmd = [
        ffmpeg,
        "-y",
        "-framerate",
        str(FPS),
        "-i",
        str(frames_dir / "frame_%04d.jpg"),
        "-i",
        str(synced_audio_path),
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
