from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from PIL import Image, ImageDraw, ImageFont

from drive_client import check_drive_secrets, upload_video_make_public
from notion_client import (
    load_local_env,
    prop_text,
    query_ready_kayla_flow_rows,
    set_video_link,
)
from tts_google import check_tts_secrets, synthesize_kayla_cta_audio


SLOT_HOURS = {
    "00:00": 0,
    "02:00": 2 * 60,
    "08:00": 8 * 60,
    "10:00": 10 * 60,
    "12:00": 12 * 60,
    "14:00": 14 * 60,
    "16:00": 16 * 60,
    "18:00": 18 * 60,
    "20:00": 20 * 60,
    "22:00": 22 * 60,
}
SLOT_WINDOW_MINUTES = 90
CTA_TEXT = "Download Saloo English. Link in bio."


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def toronto_now() -> datetime:
    try:
        return datetime.now(ZoneInfo("America/Toronto"))
    except Exception:
        return datetime.now(timezone(timedelta(hours=-4)))


def slot_is_due(slot_name: str, now: datetime | None = None) -> bool:
    current = now or toronto_now()
    current_minutes = current.hour * 60 + current.minute
    slot_minutes = SLOT_HOURS.get(slot_name)
    if slot_minutes is None:
        return False
    if slot_minutes == 0:
        return current_minutes <= 180
    diff = current_minutes - slot_minutes
    return 0 <= diff <= SLOT_WINDOW_MINUTES


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Post-process one due Kayla Flow video before publication.")
    parser.add_argument("--execute", action="store_true", help="Create final video, upload it, and fill Lien Video.")
    parser.add_argument("--date", help="YYYY-MM-DD. Defaults to today in Montreal.")
    return parser.parse_args()


def _drive_download_url(url: str) -> str:
    match = re.search(r"/d/([a-zA-Z0-9_-]+)", url)
    if match:
        return f"https://drive.usercontent.google.com/download?id={match.group(1)}&export=download&confirm=t"
    match = re.search(r"id=([a-zA-Z0-9_-]+)", url)
    if match:
        return f"https://drive.usercontent.google.com/download?id={match.group(1)}&export=download&confirm=t"
    return url


def download_source_video(source_url: str, output_path: Path) -> Path:
    response = requests.get(_drive_download_url(source_url), stream=True, timeout=180)
    print(f"Source video download status: {response.status_code}")
    print(f"Source video content-type: {response.headers.get('Content-Type', 'unknown')}")
    response.raise_for_status()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as handle:
        for chunk in response.iter_content(chunk_size=65536):
            if chunk:
                handle.write(chunk)
    if output_path.stat().st_size < 1024:
        raise RuntimeError("Downloaded source video is too small.")
    return output_path


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def _center_text(draw: ImageDraw.ImageDraw, y: int, text: str, font: ImageFont.ImageFont, fill: tuple[int, int, int]) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    x = (1080 - (bbox[2] - bbox[0])) // 2
    draw.text((x, y), text, font=font, fill=fill)


def create_cta_card(output_path: Path) -> Path:
    image = Image.new("RGB", (1080, 1920), (7, 20, 28))
    draw = ImageDraw.Draw(image)

    # Soft vertical gradient.
    for y in range(1920):
        green = int(20 + (y / 1920) * 32)
        blue = int(28 + (y / 1920) * 24)
        draw.line([(0, y), (1080, y)], fill=(7, green, blue))

    title_font = _font(98, bold=True)
    sub_font = _font(50)
    small_font = _font(36)
    button_font = _font(48, bold=True)

    _center_text(draw, 520, "Saloo English", title_font, (255, 255, 255))
    _center_text(draw, 650, "Practice real English", sub_font, (214, 241, 228))
    _center_text(draw, 730, "without freezing.", sub_font, (214, 241, 228))

    button_box = (210, 960, 870, 1080)
    draw.rounded_rectangle(button_box, radius=46, fill=(42, 207, 125))
    _center_text(draw, 994, "Download the app", button_font, (5, 22, 18))

    _center_text(draw, 1180, "Link in bio", sub_font, (255, 255, 255))
    _center_text(draw, 1540, "@salooenglish", small_font, (168, 214, 194))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, quality=95)
    return output_path


def _run_ffmpeg(cmd: list[str]) -> None:
    completed = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if completed.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {completed.stderr[-2000:]}")


def _video_has_audio(ffmpeg: str, video_path: Path) -> bool:
    completed = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(video_path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return "Audio:" in completed.stderr


def render_final_video(source_video: Path, output_video: Path, work_dir: Path) -> Path:
    import imageio_ffmpeg

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    normalized_source = work_dir / "source_normalized.mp4"
    cta_image = create_cta_card(work_dir / "cta_card.jpg")
    cta_audio = synthesize_kayla_cta_audio(work_dir / "cta.mp3", CTA_TEXT)
    cta_video = work_dir / "cta_video.mp4"
    concat_file = work_dir / "concat.txt"

    source_has_audio = _video_has_audio(ffmpeg, source_video)
    if source_has_audio:
        normalize_cmd = [
            ffmpeg,
            "-y",
            "-i",
            str(source_video),
            "-vf",
            "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1",
            "-r",
            "30",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ar",
            "44100",
            "-ac",
            "2",
            "-shortest",
            str(normalized_source),
        ]
    else:
        normalize_cmd = [
            ffmpeg,
            "-y",
            "-i",
            str(source_video),
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-vf",
            "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1",
            "-r",
            "30",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ar",
            "44100",
            "-ac",
            "2",
            "-shortest",
            str(normalized_source),
        ]
    _run_ffmpeg(normalize_cmd)

    _run_ffmpeg(
        [
            ffmpeg,
            "-y",
            "-loop",
            "1",
            "-i",
            str(cta_image),
            "-i",
            str(cta_audio),
            "-t",
            "4.5",
            "-vf",
            "scale=1080:1920,setsar=1",
            "-r",
            "30",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ar",
            "44100",
            "-ac",
            "2",
            "-shortest",
            str(cta_video),
        ]
    )

    concat_file.write_text(
        f"file '{normalized_source.as_posix()}'\nfile '{cta_video.as_posix()}'\n",
        encoding="utf-8",
    )
    _run_ffmpeg(
        [
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-c",
            "copy",
            str(output_video),
        ]
    )
    if not output_video.exists() or output_video.stat().st_size < 1024:
        raise RuntimeError("Final Kayla video was not created correctly.")
    return output_video


def _output_name(row: dict) -> str:
    props = row.get("properties", {})
    date = prop_text(props, "Date Publication")
    slot = prop_text(props, "Slot").replace(":", "")
    page_id = row.get("id", "")[:8]
    return f"kayla-flow-final-{date}-{slot}-{page_id}.mp4"


def _select_due_row(rows: list[dict], now: datetime) -> dict | None:
    for row in rows:
        props = row.get("properties", {})
        slot = prop_text(props, "Slot")
        title = prop_text(props, "Titre") or "Kayla Flow"
        if not slot_is_due(slot, now):
            print(f"Skipping {title}: slot {slot} not due for Kayla post-process.")
            continue
        return row
    return None


def _print_row(row: dict) -> None:
    props = row.get("properties", {})
    print("Matched Kayla Flow row:")
    print(f"- page_id: {row.get('id')}")
    print(f"- title: {prop_text(props, 'Titre')}")
    print(f"- date: {prop_text(props, 'Date Publication')}")
    print(f"- slot: {prop_text(props, 'Slot')}")
    print(f"- source_video_present_in_Image_HyperFrames: {bool(prop_text(props, 'Image HyperFrames'))}")
    print(f"- lien_video_empty: {not bool(prop_text(props, 'Lien Video'))}")


def dry_run(target_date: str) -> int:
    now = toronto_now()
    rows = query_ready_kayla_flow_rows(target_date)
    selected = _select_due_row(rows, now)
    print("KAYLA POST-PROCESS DRY-RUN")
    print("No video render, Drive upload, TTS, Notion update, or publication will run.")
    print(f"Current time (Montreal): {now.strftime('%Y-%m-%d %H:%M')}")
    print(f"Target date: {target_date}")
    print(f"Ready source row(s): {len(rows)}")
    print(f"Drive missing: {', '.join(check_drive_secrets()) or 'none'}")
    print(f"TTS missing: {', '.join(check_tts_secrets()) or 'none'}")
    if selected:
        _print_row(selected)
    else:
        print("No due Kayla Flow row found.")
    return 0


def execute(target_date: str) -> int:
    now = toronto_now()
    rows = query_ready_kayla_flow_rows(target_date)
    selected = _select_due_row(rows, now)
    print(f"Current time (Montreal): {now.strftime('%Y-%m-%d %H:%M')}")
    print(f"Kayla post-process target date: {target_date}")
    print(f"Ready Kayla Flow source row(s): {len(rows)}")
    if not selected:
        print("No due Kayla Flow row found. Nothing to prepare.")
        return 0

    _print_row(selected)
    props = selected.get("properties", {})
    source_url = prop_text(props, "Image HyperFrames")
    if not source_url:
        print("Image HyperFrames is empty. Nothing to prepare.")
        return 0

    work_dir = Path(tempfile.mkdtemp(prefix="kayla_postprocess_"))
    try:
        source_path = download_source_video(source_url, work_dir / "source_flow.mp4")
        final_path = render_final_video(source_path, work_dir / _output_name(selected), work_dir)
        drive_url = upload_video_make_public(final_path, final_path.name)
        if not drive_url:
            raise RuntimeError("Google Drive upload returned an empty URL.")
        set_video_link(selected["id"], drive_url)
        print("Kayla final video ready. Notion Lien Video filled. Statut remains A publier.")
        print(f"Drive URL: {drive_url}")
        return 0
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def main() -> int:
    args = parse_args()
    load_local_env(repo_root())
    target_date = args.date or toronto_now().strftime("%Y-%m-%d")
    if args.execute:
        return execute(target_date)
    return dry_run(target_date)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"KAYLA_POSTPROCESS_FAILED: {exc}", file=sys.stderr)
        raise SystemExit(2)
