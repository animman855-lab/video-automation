from __future__ import annotations

import math
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from podcast_parser import PodcastScript


WIDTH = 1080
HEIGHT = 1920
FPS = 30
LINE_SILENCE_SECONDS = 0.08
MIN_LINE_SECONDS = 2.35
WAVEFORM_Y = 1175
SUBTITLE_Y = 1285


@dataclass(frozen=True)
class PodcastSegment:
    text: str
    start: float
    end: float
    index: int


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
    line_audio_paths: list[Path],
    podcast: PodcastScript,
    output_audio_path: Path,
    audio_work_dir: Path,
) -> tuple[Path, list[PodcastSegment], float]:
    if len(line_audio_paths) != len(podcast.lines):
        raise ValueError(f"Podcast has {len(podcast.lines)} lines but {len(line_audio_paths)} audio files were provided.")

    audio_work_dir.mkdir(parents=True, exist_ok=True)
    normalized_lines = []
    for index, source in enumerate(line_audio_paths, start=1):
        if not source.exists() or source.stat().st_size <= 0:
            raise ValueError(f"Podcast audio is missing or empty for line {index}.")
        normalized_lines.append(_convert_to_wav(ffmpeg, source, audio_work_dir / f"line_{index:02d}.wav"))

    current = 0.0
    segments: list[PodcastSegment] = []
    expected_params = None

    with wave.open(str(output_audio_path), "wb") as writer:
        for index, wav_path in enumerate(normalized_lines, start=1):
            with wave.open(str(wav_path), "rb") as reader:
                params = reader.getparams()
                if expected_params is None:
                    expected_params = params
                    writer.setparams(params)
                elif params[:3] != expected_params[:3]:
                    raise ValueError(f"Inconsistent audio format for podcast line {index}.")

                frames_count = reader.getnframes()
                duration = frames_count / reader.getframerate()
                if duration <= 0:
                    raise ValueError(f"Podcast audio has invalid duration for line {index}.")

                segment_duration = max(duration + LINE_SILENCE_SECONDS, MIN_LINE_SECONDS)
                start = current
                end = current + segment_duration
                writer.writeframes(reader.readframes(frames_count))
                _write_silence(writer, segment_duration - duration)
                segments.append(
                    PodcastSegment(
                        text=podcast.lines[index - 1].text,
                        start=start,
                        end=end,
                        index=index,
                    )
                )
                current = end

    if not segments:
        raise ValueError("No Cindy podcast segments were created.")
    return output_audio_path, segments, current


def _active_segment(segments: list[PodcastSegment], seconds: float) -> PodcastSegment | None:
    for segment in segments:
        if segment.start <= seconds < segment.end:
            return segment
    return None


def _subtitle_group(segment: PodcastSegment, seconds: float) -> str:
    words = segment.text.split()
    if not words:
        return ""

    group_size = 4 if len(words) > 9 else 3
    groups = [" ".join(words[index : index + group_size]) for index in range(0, len(words), group_size)]
    progress = (seconds - segment.start) / max(0.01, segment.end - segment.start)
    group_index = min(len(groups) - 1, max(0, int(progress * len(groups))))
    return groups[group_index]


def _draw_safe_gradient(draw: ImageDraw.ImageDraw) -> None:
    for y in range(1020, HEIGHT):
        alpha = int(min(150, max(0, (y - 1020) / 760 * 150)))
        draw.line((0, y, WIDTH, y), fill=(0, 0, 0, alpha))


def _draw_waveform(draw: ImageDraw.ImageDraw, seconds: float, active: bool) -> None:
    color = (255, 255, 255, 220)
    accent = (84, 190, 255, 235)
    width = 720
    left = (WIDTH - width) // 2
    bars = 42
    spacing = width / bars
    base_amp = 36 if active else 14

    for index in range(bars):
        phase = seconds * 5.5 + index * 0.65
        amp = base_amp * (0.35 + 0.65 * abs(math.sin(phase)))
        x = int(left + index * spacing)
        y1 = int(WAVEFORM_Y - amp)
        y2 = int(WAVEFORM_Y + amp)
        draw.rounded_rectangle((x, y1, x + 7, y2), radius=4, fill=accent if index % 5 == 0 else color)


def _draw_subtitle(draw: ImageDraw.ImageDraw, text: str) -> None:
    if not text:
        return

    font = _font(56)
    words = text.split()
    line = " ".join(words)
    bbox = draw.textbbox((0, 0), line, font=font)
    max_width = 850
    if bbox[2] - bbox[0] > max_width and len(words) > 2:
        midpoint = math.ceil(len(words) / 2)
        lines = [" ".join(words[:midpoint]), " ".join(words[midpoint:])]
    else:
        lines = [line]

    line_height = 66
    padding_x = 34
    padding_y = 24
    text_w = 0
    for item in lines:
        item_bbox = draw.textbbox((0, 0), item, font=font)
        text_w = max(text_w, item_bbox[2] - item_bbox[0])
    box_w = min(920, text_w + padding_x * 2)
    box_h = len(lines) * line_height + padding_y * 2
    x1 = (WIDTH - box_w) // 2
    y1 = SUBTITLE_Y
    x2 = x1 + box_w
    y2 = y1 + box_h

    draw.rounded_rectangle((x1 + 6, y1 + 6, x2 + 6, y2 + 6), radius=26, fill=(0, 0, 0, 90))
    draw.rounded_rectangle((x1, y1, x2, y2), radius=26, fill=(255, 255, 255, 238))

    y = y1 + padding_y - 4
    for item in lines:
        item_bbox = draw.textbbox((0, 0), item, font=font)
        draw.text(((WIDTH - (item_bbox[2] - item_bbox[0])) // 2, y), item, fill=(12, 12, 12), font=font)
        y += line_height


def render_cindy_podcast_video(
    image_path: Path,
    output_path: Path,
    frames_dir: Path,
    podcast: PodcastScript,
    line_audio_paths: list[Path],
) -> Path:
    import imageio_ffmpeg

    if not podcast.lines:
        raise ValueError("Cannot render Cindy podcast without lines.")

    frames_dir.mkdir(parents=True, exist_ok=True)
    audio_work_dir = frames_dir.parent / "audio_normalized"
    base = _cover_image(image_path)
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    synced_audio_path, segments, duration = _build_synced_audio(
        ffmpeg=ffmpeg,
        line_audio_paths=line_audio_paths,
        podcast=podcast,
        output_audio_path=frames_dir.parent / "cindy-synced-audio.wav",
        audio_work_dir=audio_work_dir,
    )
    total_frames = int(FPS * duration)

    for frame_index in range(total_frames):
        seconds = frame_index / FPS
        frame = base.copy()
        draw = ImageDraw.Draw(frame, "RGBA")
        segment = _active_segment(segments, seconds)
        _draw_safe_gradient(draw)
        _draw_waveform(draw, seconds, active=segment is not None)
        if segment:
            _draw_subtitle(draw, _subtitle_group(segment, seconds))
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
