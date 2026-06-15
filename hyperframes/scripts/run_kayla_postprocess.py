from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
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


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


OUTRO_ASSET = repo_root() / "hyperframes" / "assets" / "kayla" / "saloo-outro.mp4"
CANVAS_SIZE = (1080, 1920)
MAX_CARDS = 7


@dataclass(frozen=True)
class KaylaCard:
    emoji: str
    title: str
    lines: tuple[str, ...]
    tone: str = "default"


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


def _clean_text(value: str, max_chars: int = 92) -> str:
    text = re.sub(r"\s+", " ", value).strip(" .:-")
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rsplit(" ", 1)[0] + "..."


def _short_title(text: str, fallback: str, max_chars: int = 34) -> str:
    cleaned = _clean_text(text, max_chars + 12)
    if not cleaned:
        return fallback
    if len(cleaned) <= max_chars:
        return cleaned
    return fallback


def _extract_field(script: str, label: str) -> str:
    labels = ["Hook", "Main message", "Problem", "Solution", "Ad message", "Visual direction", "CTA"]
    other_labels = [item for item in labels if item.lower() != label.lower()]
    boundary = "|".join(re.escape(item) + r":" for item in other_labels)
    match = re.search(rf"{re.escape(label)}:\s*(.*?)(?=\s+(?:{boundary})|$)", script, re.IGNORECASE | re.DOTALL)
    return _clean_text(match.group(1), 130) if match else ""


def _classify_kayla_format(script: str, prompt: str, title: str) -> str:
    combined = f"{title} {script} {prompt}".lower()
    if re.search(r"\b(myth|truth)\b", combined):
        return "myth_buster"
    if "mini english lesson" in combined or re.search(r"(don't|dont|do not)\s+say", combined) or "not:" in combined:
        return "mini_lesson"
    if "voice-call" in combined or "voice call" in combined or ("phone" in combined and "correct" in combined):
        return "saloo_demo"
    if any(term in combined for term in ["job interview", "airport", "hotel", "dating", "meeting", "campus", "library", "phone call", "small talk"]):
        return "specific_situation"
    if any(term in combined for term in ["scared", "freeze", "nervous", "confidence", "confession", "overthinking"]):
        return "confession"
    return "face_camera_hook"


def _extract_correction_cards(text: str) -> list[KaylaCard]:
    cards: list[KaylaCard] = []
    patterns = [
        r"(?:don't|dont|do not|not)\s+say:?\s*[\"“]?(.{2,80}?)[\"”]?(?:\.|\n|;)\s*(?:say|say this|instead|natural):?\s*[\"“]?(.{2,80}?)[\"”]?(?:\.|\n|;|$)",
        r"not:?\s*[\"“]?(.{2,80}?)[\"”]?(?:\.|\n|;)\s*say:?\s*[\"“]?(.{2,80}?)[\"”]?(?:\.|\n|;|$)",
    ]
    for pattern in patterns:
        for wrong, right in re.findall(pattern, text, flags=re.IGNORECASE | re.DOTALL):
            wrong = _clean_text(wrong, 58)
            right = _clean_text(right, 58)
            if wrong and right and wrong.lower() != right.lower():
                cards.append(KaylaCard("✅", "Say it naturally", (f"❌ {wrong}", f"✅ {right}"), "correction"))
            if len(cards) >= MAX_CARDS:
                return cards
    return cards


def _topic_emoji(text: str) -> str:
    lower = text.lower()
    if any(term in lower for term in ["phone", "app", "saloo", "screen"]):
        return "📱"
    if any(term in lower for term in ["listen", "ear", "voice", "pronunciation"]):
        return "🎧"
    if any(term in lower for term in ["speak", "speaking", "conversation", "reply"]):
        return "🗣️"
    if any(term in lower for term in ["job", "work", "interview", "meeting"]):
        return "💼"
    if any(term in lower for term in ["travel", "airport", "hotel"]):
        return "✈️"
    if any(term in lower for term in ["cafe", "coffee", "small talk"]):
        return "☕"
    if any(term in lower for term in ["freeze", "nervous", "shy", "scared"]):
        return "😬"
    return "✨"


def build_kayla_cards(title: str, script: str, prompt: str) -> list[KaylaCard]:
    combined = f"{title}\n{script}\n{prompt}"
    video_format = _classify_kayla_format(script, prompt, title)
    hook = _extract_field(script, "Hook")
    problem = _extract_field(script, "Problem")
    solution = _extract_field(script, "Solution")
    main_message = _extract_field(script, "Main message")

    cards: list[KaylaCard] = []
    corrections = _extract_correction_cards(combined)

    if video_format == "mini_lesson":
        cards.append(KaylaCard("⚠️", _short_title(hook, "Quick English fix"), (hook or "Stop saying it the hard way",), "hook"))
        cards.extend(corrections[:5])
        if not corrections:
            cards.append(KaylaCard("❌", "Common mistake", ("This sounds translated",), "correction"))
            cards.append(KaylaCard("✅", "Make it natural", ("Practice the better phrase out loud",), "correction"))
        cards.append(KaylaCard("🔁", "Practice tip", ("Say the natural version twice",), "takeaway"))
    elif video_format == "saloo_demo":
        cards.append(KaylaCard("📱", _short_title(hook, "App speaking practice"), (hook or "Let the app correct you",), "hook"))
        cards.append(KaylaCard("🎧", "Saloo feedback", ("Listen, repeat, improve",), "phone"))
        if main_message:
            cards.append(KaylaCard("🗣️", "Speaking practice", (_clean_text(main_message, 72),), "takeaway"))
        cards.append(KaylaCard("✨", "Small correction", ("More confidence next time",), "takeaway"))
    elif video_format == "myth_buster":
        myth = hook if hook else "Watching English is enough"
        truth = main_message or solution or "You need to answer out loud"
        cards.append(KaylaCard("🚫", "Myth", (_clean_text(myth, 70),), "myth"))
        cards.append(KaylaCard("✅", "Truth", (_clean_text(truth, 78),), "truth"))
        cards.append(KaylaCard("🔁", "Real practice", ("Reply out loud before real life",), "takeaway"))
    elif video_format == "specific_situation":
        cards.append(KaylaCard(_topic_emoji(combined), _short_title(hook, "Real-life practice"), (hook or "Practice before real life",), "hook"))
        if main_message:
            cards.append(KaylaCard("💬", "Real-life English", (_clean_text(main_message, 78),), "takeaway"))
        if solution:
            cards.append(KaylaCard("📱", "Practice inside Saloo", (_clean_text(solution, 78),), "phone"))
    elif video_format == "confession":
        cards.append(KaylaCard("😬", _short_title(hook, "English feels stuck?"), (hook or "You understand it... then freeze",), "hook"))
        if problem:
            cards.append(KaylaCard("💬", "Real learner problem", (_clean_text(problem, 78),), "takeaway"))
        cards.append(KaylaCard("✨", "Practice helps", ("Confidence grows after repetition",), "takeaway"))
    else:
        cards.append(KaylaCard(_topic_emoji(combined), _short_title(hook, "Practice useful English"), (hook or "Small daily practice works",), "hook"))
        if main_message:
            cards.append(KaylaCard("💬", "Real English", (_clean_text(main_message, 78),), "takeaway"))
        cards.append(KaylaCard("📱", "Saloo English", ("Practice before you need it",), "phone"))

    deduped: list[KaylaCard] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for card in cards:
        key = (card.title.lower(), tuple(line.lower() for line in card.lines))
        if key not in seen:
            deduped.append(card)
            seen.add(key)
        if len(deduped) >= MAX_CARDS:
            break
    return deduped


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        r"C:\Windows\Fonts\seguiemj.ttf",
        r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def _wrap_line(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        width = draw.textbbox((0, 0), candidate, font=font)[2]
        if width <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def render_card_png(card: KaylaCard, output_path: Path) -> Path:
    image = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    title_font = _font(54, bold=True)
    body_font = _font(48, bold=True)
    small_font = _font(38)

    max_width = 900
    wrapped_lines: list[tuple[str, ImageFont.ImageFont]] = []
    for wrapped in _wrap_line(draw, f"{card.emoji} {card.title}", title_font, max_width - 90):
        wrapped_lines.append((wrapped, title_font))
    for line in card.lines:
        font = body_font if any(mark in line for mark in ["❌", "✅"]) else small_font
        for wrapped in _wrap_line(draw, line, font, max_width - 90):
            wrapped_lines.append((wrapped, font))

    line_heights = [draw.textbbox((0, 0), text, font=font)[3] + 18 for text, font in wrapped_lines]
    box_height = min(430, max(180, sum(line_heights) + 70))
    box_width = max_width
    x0 = (1080 - box_width) // 2
    y0 = 1070 if card.tone not in {"hook", "myth"} else 900
    x1 = x0 + box_width
    y1 = y0 + box_height

    border = {
        "correction": (54, 218, 125, 230),
        "phone": (83, 177, 255, 230),
        "myth": (255, 91, 91, 230),
        "truth": (54, 218, 125, 230),
        "hook": (255, 255, 255, 220),
    }.get(card.tone, (255, 255, 255, 210))

    shadow = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle((x0 + 8, y0 + 10, x1 + 8, y1 + 10), radius=34, fill=(0, 0, 0, 110))
    image.alpha_composite(shadow)

    draw.rounded_rectangle((x0, y0, x1, y1), radius=34, fill=(14, 20, 24, 218), outline=border, width=4)

    y = y0 + 36
    for text, font in wrapped_lines:
        bbox = draw.textbbox((0, 0), text, font=font)
        draw.text(((1080 - (bbox[2] - bbox[0])) // 2, y), text, font=font, fill=(255, 255, 255, 255))
        y += bbox[3] - bbox[1] + 18

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)
    return output_path


def _video_duration_seconds(ffmpeg: str, video_path: Path) -> float:
    completed = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(video_path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", completed.stderr)
    if not match:
        return 12.0
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _card_windows(card_count: int, duration: float) -> list[tuple[float, float]]:
    if card_count <= 0:
        return []
    start = 0.45
    end_limit = max(start + 1.0, duration - 0.55)
    span = max(1.0, end_limit - start)
    slot = span / card_count
    visible = min(2.6, max(1.35, slot * 0.82))
    return [(start + index * slot, min(start + index * slot + visible, end_limit)) for index in range(card_count)]


def overlay_cards(ffmpeg: str, source_video: Path, output_video: Path, cards: list[KaylaCard], work_dir: Path) -> Path:
    if not cards:
        shutil.copyfile(source_video, output_video)
        return output_video

    duration = _video_duration_seconds(ffmpeg, source_video)
    windows = _card_windows(len(cards), duration)
    card_paths = [render_card_png(card, work_dir / f"card_{index:02d}.png") for index, card in enumerate(cards, start=1)]

    cmd = [ffmpeg, "-y", "-i", str(source_video)]
    for path in card_paths:
        cmd.extend(["-loop", "1", "-i", str(path)])

    filters: list[str] = []
    current = "[0:v]"
    for index, ((start, end), _) in enumerate(zip(windows, card_paths), start=1):
        next_label = f"[v{index}]"
        filters.append(
            f"{current}[{index}:v]overlay=0:0:enable='between(t,{start:.2f},{end:.2f})'{next_label}"
        )
        current = next_label

    cmd.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            current,
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "copy",
            "-shortest",
            str(output_video),
        ]
    )
    _run_ffmpeg(cmd)
    if not output_video.exists() or output_video.stat().st_size < 1024:
        raise RuntimeError("Kayla card overlay video was not created correctly.")
    return output_video


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


def render_final_video(source_video: Path, output_video: Path, work_dir: Path, cards: list[KaylaCard]) -> Path:
    import imageio_ffmpeg

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    normalized_source = work_dir / "source_normalized.mp4"
    source_with_cards = work_dir / "source_with_cards.mp4"
    normalized_outro = work_dir / "outro_normalized.mp4"
    concat_file = work_dir / "concat.txt"

    if not OUTRO_ASSET.exists() or OUTRO_ASSET.stat().st_size < 1024:
        raise RuntimeError(f"Missing Kayla outro asset: {OUTRO_ASSET}")

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
    overlay_cards(ffmpeg, normalized_source, source_with_cards, cards, work_dir / "cards")

    if _video_has_audio(ffmpeg, OUTRO_ASSET):
        outro_cmd = [
            ffmpeg,
            "-y",
            "-i",
            str(OUTRO_ASSET),
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
            str(normalized_outro),
        ]
    else:
        outro_cmd = [
            ffmpeg,
            "-y",
            "-i",
            str(OUTRO_ASSET),
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
            str(normalized_outro),
        ]
    _run_ffmpeg(outro_cmd)

    concat_file.write_text(
        f"file '{source_with_cards.as_posix()}'\nfile '{normalized_outro.as_posix()}'\n",
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
    print(f"Outro asset present: {OUTRO_ASSET.exists()}")
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
        cards = build_kayla_cards(
            title=prop_text(props, "Titre"),
            script=prop_text(props, "Script"),
            prompt=prop_text(props, "Prompt 1"),
        )
        print(f"Kayla smart cards: {len(cards)}")
        for index, card in enumerate(cards, start=1):
            print(f"  card_{index}: {card.emoji} {card.title} | {' / '.join(card.lines)}")
        final_path = render_final_video(source_path, work_dir / _output_name(selected), work_dir, cards)
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
