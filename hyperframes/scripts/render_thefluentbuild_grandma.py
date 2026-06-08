from __future__ import annotations

import math
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from dialogue_parser import DialogueScript


WIDTH = 1080
HEIGHT = 1920
FPS = 30
MIN_LINE_SECONDS = 2.35
LINE_SILENCE_SECONDS = 0.28
CTA_HOLD_SECONDS = 1.2


@dataclass(frozen=True)
class GrandmaSegment:
    text: str
    start: float
    end: float
    index: int
    speaker: str


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


def _bubble_box(text: str, font: ImageFont.ImageFont) -> tuple[list[str], int, int]:
    max_text_width = 700
    lines = _wrap_text(text, font, max_text_width)
    line_height = 56
    padding_x = 36
    padding_y = 28
    text_w = 0
    for line in lines:
        bbox = ImageDraw.Draw(Image.new("RGB", (1, 1))).textbbox((0, 0), line, font=font)
        text_w = max(text_w, bbox[2] - bbox[0])
    box_w = min(max_text_width + padding_x * 2, max(430, text_w + padding_x * 2))
    box_h = len(lines) * line_height + padding_y * 2
    return lines, box_w, box_h


def _bubble_position(speaker: str, index: int, box_w: int, box_h: int) -> tuple[int, int]:
    grandma_y = [260, 500, 700]
    learner_y = [340, 570, 740]
    if speaker == "grandma":
        x = 70
        y = grandma_y[(index // 2) % len(grandma_y)]
    else:
        x = WIDTH - box_w - 70
        y = learner_y[(index // 2) % len(learner_y)]
    return x, min(y, 820 - box_h)


def _draw_bubble(draw: ImageDraw.ImageDraw, segment: GrandmaSegment, pulse: float) -> None:
    font = _font(46)
    lines, box_w, box_h = _bubble_box(segment.text, font)
    x, y = _bubble_position(segment.speaker, segment.index, box_w, box_h)
    shadow = 7
    padding_x = 36
    padding_y = 28
    line_height = 56
    border = (214, 191, 157) if segment.speaker == "grandma" else (210, 214, 220)

    scale = 1.0 + math.sin(pulse * math.pi) * 0.01
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
        radius=30,
        fill=(0, 0, 0, 65),
    )
    draw.rounded_rectangle(
        (x1, y1, x2, y2),
        radius=30,
        fill=(255, 255, 255),
        outline=border,
        width=4,
    )

    tail_y = y2 - 28
    if segment.speaker == "grandma":
        tail = [(x1 + 74, tail_y), (x1 + 126, tail_y), (x1 + 66, tail_y + 58)]
    else:
        tail = [(x2 - 74, tail_y), (x2 - 126, tail_y), (x2 - 66, tail_y + 58)]
    draw.polygon(tail, fill=(255, 255, 255), outline=border)

    text_y = y1 + padding_y - 5
    for line in lines:
        draw.text((x1 + padding_x, text_y), line, fill=(18, 18, 18), font=font)
        text_y += line_height


def _draw_cta(draw: ImageDraw.ImageDraw, cta: str) -> None:
    font = _font(50)
    lines = _wrap_text(cta, font, 760)
    line_height = 62
    box_w = 850
    box_h = len(lines) * line_height + 72
    x1 = (WIDTH - box_w) // 2
    y1 = 1290
    x2 = x1 + box_w
    y2 = y1 + box_h
    draw.rounded_rectangle(
        (x1, y1, x2, y2),
        radius=32,
        fill=(255, 255, 255),
        outline=(214, 191, 157),
        width=4,
    )
    y = y1 + 34
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        draw.text(((WIDTH - (bbox[2] - bbox[0])) // 2, y), line, fill=(18, 18, 18), font=font)
        y += line_height


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
    cta_audio_path: Path,
    dialogue: DialogueScript,
    output_audio_path: Path,
    audio_work_dir: Path,
) -> tuple[Path, list[GrandmaSegment], float, float]:
    if len(line_audio_paths) != len(dialogue.lines):
        raise ValueError(
            f"Dialogue has {len(dialogue.lines)} lines but {len(line_audio_paths)} audio files were provided."
        )
    if not cta_audio_path.exists() or cta_audio_path.stat().st_size <= 0:
        raise ValueError("CTA audio is missing or empty.")

    audio_work_dir.mkdir(parents=True, exist_ok=True)
    normalized_lines = []
    for index, source in enumerate(line_audio_paths, start=1):
        if not source.exists() or source.stat().st_size <= 0:
            raise ValueError(f"Dialogue audio is missing or empty for line {index}.")
        normalized_lines.append(_convert_to_wav(ffmpeg, source, audio_work_dir / f"line_{index:02d}.wav"))
    normalized_cta = _convert_to_wav(ffmpeg, cta_audio_path, audio_work_dir / "cta.wav")

    current = 0.0
    segments: list[GrandmaSegment] = []
    expected_params = None

    with wave.open(str(output_audio_path), "wb") as writer:
        for index, wav_path in enumerate(normalized_lines, start=1):
            with wave.open(str(wav_path), "rb") as reader:
                params = reader.getparams()
                if expected_params is None:
                    expected_params = params
                    writer.setparams(params)
                elif params[:3] != expected_params[:3]:
                    raise ValueError(f"Inconsistent audio format for line {index}.")

                frames_count = reader.getnframes()
                duration = frames_count / reader.getframerate()
                if duration <= 0:
                    raise ValueError(f"Dialogue audio has invalid duration for line {index}.")

                segment_duration = max(duration + LINE_SILENCE_SECONDS, MIN_LINE_SECONDS)
                start = current
                end = current + segment_duration
                writer.writeframes(reader.readframes(frames_count))
                _write_silence(writer, segment_duration - duration)
                speaker = "grandma" if index % 2 == 0 else "learner"
                segments.append(
                    GrandmaSegment(
                        text=dialogue.lines[index - 1],
                        start=start,
                        end=end,
                        index=index,
                        speaker=speaker,
                    )
                )
                current = end

        cta_start = current
        with wave.open(str(normalized_cta), "rb") as reader:
            params = reader.getparams()
            if expected_params is None:
                writer.setparams(params)
            elif params[:3] != expected_params[:3]:
                raise ValueError("Inconsistent audio format for CTA.")

            frames_count = reader.getnframes()
            cta_duration = reader.getnframes() / reader.getframerate()
            if cta_duration <= 0:
                raise ValueError("CTA audio has invalid duration.")

            writer.writeframes(reader.readframes(frames_count))
            _write_silence(writer, CTA_HOLD_SECONDS)
            current += cta_duration + CTA_HOLD_SECONDS

    if not segments:
        raise ValueError("No TheFluentBuild dialogue segments were created.")

    return output_audio_path, segments, cta_start, current


def _active_segment(segments: list[GrandmaSegment], seconds: float) -> GrandmaSegment | None:
    for segment in segments:
        if segment.start <= seconds < segment.end:
            return segment
    return None


def render_thefluentbuild_grandma_video(
    image_path: Path,
    output_path: Path,
    frames_dir: Path,
    dialogue: DialogueScript,
    line_audio_paths: list[Path],
    cta_audio_path: Path,
) -> Path:
    import imageio_ffmpeg

    if not dialogue.lines:
        raise ValueError("Cannot render TheFluentBuild without dialogue lines.")

    frames_dir.mkdir(parents=True, exist_ok=True)
    audio_work_dir = frames_dir.parent / "audio_normalized"
    base = _cover_image(image_path)
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    synced_audio_path, segments, cta_start, duration = _build_synced_audio(
        ffmpeg=ffmpeg,
        line_audio_paths=line_audio_paths,
        cta_audio_path=cta_audio_path,
        dialogue=dialogue,
        output_audio_path=frames_dir.parent / "thefluentbuild-synced-audio.wav",
        audio_work_dir=audio_work_dir,
    )
    total_frames = int(FPS * duration)

    for frame_index in range(total_frames):
        seconds = frame_index / FPS
        frame = base.copy()
        draw = ImageDraw.Draw(frame, "RGBA")
        segment = _active_segment(segments, seconds)

        if segment:
            local = (seconds - segment.start) / max(0.01, segment.end - segment.start)
            _draw_bubble(draw, segment, local)
        elif seconds >= cta_start:
            _draw_cta(draw, dialogue.cta)

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
