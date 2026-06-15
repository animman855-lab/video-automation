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
CARD_MAX_SECONDS = 12.4
APPEND_OUTRO_MAX_SOURCE_SECONDS = 15.5


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
        if solution:
            cards.append(KaylaCard("✅", "Better answer", (_clean_text(solution, 72),), "truth"))
        cards.append(KaylaCard("✨", "Small correction", ("More confidence next time",), "takeaway"))
    elif video_format == "myth_buster":
        myth = hook if hook else "Watching English is enough"
        truth = main_message or solution or "You need to answer out loud"
        cards.append(KaylaCard("🚫", "Myth", (_clean_text(myth, 70),), "myth"))
        cards.append(KaylaCard("✅", "Truth", (_clean_text(truth, 78),), "truth"))
        cards.append(KaylaCard("🔁", "Real practice", ("Reply out loud before real life",), "takeaway"))
    elif video_format == "specific_situation":
        cards.append(KaylaCard(_topic_emoji(combined), _short_title(hook, "Real-life practice"), (hook or "Practice before real life",), "hook"))
        if problem:
            cards.append(KaylaCard("😬", "The problem", (_clean_text(problem, 72),), "myth"))
        if main_message:
            cards.append(KaylaCard("💬", "Real-life English", (_clean_text(main_message, 78),), "takeaway"))
        if solution:
            cards.append(KaylaCard("📱", "Practice inside Saloo", (_clean_text(solution, 78),), "phone"))
    elif video_format == "confession":
        cards.append(KaylaCard("😬", _short_title(hook, "English feels stuck?"), (hook or "You understand it... then freeze",), "hook"))
        if problem:
            cards.append(KaylaCard("💬", "Real learner problem", (_clean_text(problem, 78),), "takeaway"))
        if solution:
            cards.append(KaylaCard("📱", "Practice in Saloo", (_clean_text(solution, 78),), "phone"))
        elif main_message:
            cards.append(KaylaCard("🗣️", "Try this", (_clean_text(main_message, 78),), "takeaway"))
        cards.append(KaylaCard("✨", "Practice helps", ("Confidence grows after repetition",), "takeaway"))
    else:
        cards.append(KaylaCard(_topic_emoji(combined), _short_title(hook, "Practice useful English"), (hook or "Small daily practice works",), "hook"))
        if problem:
            cards.append(KaylaCard("😬", "The problem", (_clean_text(problem, 72),), "myth"))
        if main_message:
            cards.append(KaylaCard("💬", "Real English", (_clean_text(main_message, 78),), "takeaway"))
        if solution:
            cards.append(KaylaCard("✅", "The fix", (_clean_text(solution, 72),), "truth"))
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


def _emoji(codepoint: str) -> str:
    return "".join(chr(int(part, 16)) for part in codepoint.split())


E_WARN = _emoji("26A0 FE0F")
E_WRONG = _emoji("274C")
E_RIGHT = _emoji("2705")
E_PHONE = _emoji("1F4F1")
E_AUDIO = _emoji("1F3A7")
E_SPEAK = _emoji("1F5E3 FE0F")
E_REPEAT = _emoji("1F501")
E_SPARK = _emoji("2728")
E_MYTH = _emoji("1F6AB")
E_NERVOUS = _emoji("1F62C")
E_CHAT = _emoji("1F4AC")
E_WORK = _emoji("1F4BC")
E_TRAVEL = _emoji("2708 FE0F")
E_COFFEE = _emoji("2615")
E_BRAIN = _emoji("1F9E0")
E_MELT = _emoji("1FAE0")
E_BOOKS = _emoji("1F4DA")
E_CLOCK = _emoji("23F1 FE0F")
E_QUIET = _emoji("1F636")


def _contains(text: str, words: list[str]) -> bool:
    lower = text.lower()
    return any(word in lower for word in words)


def _card_problem(text: str) -> tuple[str, str]:
    if _contains(text, ["translate", "translating"]):
        return E_BRAIN, "You translate first..."
    if _contains(text, ["freeze", "stuck", "mouth"]):
        return E_NERVOUS, "You know the words..."
    if _contains(text, ["forget", "forgetting", "remember"]):
        return E_MELT, "You learn it... then forget it"
    if _contains(text, ["random", "videos", "notes"]):
        return E_BOOKS, "Random English is not enough"
    if _contains(text, ["perfect", "grammar"]):
        return E_WARN, "Perfect grammar can wait"
    if _contains(text, ["busy", "minutes", "habit", "daily"]):
        return E_CLOCK, "A few minutes can count"
    if _contains(text, ["shy", "nervous", "scared"]):
        return E_QUIET, "Private practice helps"
    if _contains(text, ["job", "interview", "work", "meeting"]):
        return E_WORK, "Practice before work English"
    if _contains(text, ["airport", "hotel", "travel"]):
        return E_TRAVEL, "Practice before travel"
    return E_NERVOUS, "You understand it..."


def _card_fix(text: str) -> tuple[str, str]:
    if _contains(text, ["reply", "answer", "conversation"]):
        return E_SPEAK, "Practice real replies"
    if _contains(text, ["repeat", "listen", "voice", "pronunciation"]):
        return E_AUDIO, "Listen. Repeat. Improve."
    if _contains(text, ["mistake", "correct", "correction"]):
        return E_RIGHT, "Fix small mistakes fast"
    if _contains(text, ["habit", "daily", "minutes", "routine"]):
        return E_REPEAT, "Tiny reps, every day"
    if _contains(text, ["phrase", "phrases", "natural"]):
        return E_CHAT, "Use phrases people say"
    if _contains(text, ["confidence", "confident"]):
        return E_SPARK, "Build speaking confidence"
    return E_PHONE, "Practice inside Saloo"


def _topic_icon_v2(text: str) -> str:
    if _contains(text, ["job", "work", "interview", "meeting"]):
        return E_WORK
    if _contains(text, ["airport", "hotel", "travel"]):
        return E_TRAVEL
    if _contains(text, ["cafe", "coffee", "small talk"]):
        return E_COFFEE
    if _contains(text, ["phone", "app", "saloo", "screen"]):
        return E_PHONE
    return E_CHAT


def _extract_correction_cards_v2(text: str) -> list[KaylaCard]:
    cards: list[KaylaCard] = []
    patterns = [
        r"(?:don't|dont|do not|not)\s+say:?\s*[\"“]?(.{2,70}?)[\"”]?(?:\.|\n|;)\s*(?:say|say this|instead|natural):?\s*[\"“]?(.{2,70}?)[\"”]?(?:\.|\n|;|$)",
        r"not:?\s*[\"“]?(.{2,70}?)[\"”]?(?:\.|\n|;)\s*say:?\s*[\"“]?(.{2,70}?)[\"”]?(?:\.|\n|;|$)",
    ]
    for pattern in patterns:
        for wrong, right in re.findall(pattern, text, flags=re.IGNORECASE | re.DOTALL):
            wrong = _clean_text(wrong, 44)
            right = _clean_text(right, 44)
            if wrong and right and wrong.lower() != right.lower():
                cards.append(KaylaCard(E_RIGHT, "Say it naturally", (f"{E_WRONG} {wrong}", f"{E_RIGHT} {right}"), "correction"))
            if len(cards) >= MAX_CARDS:
                return cards
    return cards


def build_kayla_cards_v2(title: str, script: str, prompt: str) -> list[KaylaCard]:
    combined = f"{title}\n{script}\n{prompt}"
    video_format = _classify_kayla_format(script, prompt, title)
    problem_icon, problem_line = _card_problem(combined)
    fix_icon, fix_line = _card_fix(combined)
    corrections = _extract_correction_cards_v2(combined)

    if video_format == "mini_lesson":
        cards = [KaylaCard(E_WARN, "Quick English fix", ("Stop sounding translated",), "hook")]
        cards.extend(corrections[:5])
        if not corrections:
            cards.extend(
                [
                    KaylaCard(E_WRONG, "Not natural", ("Textbook English",), "correction"),
                    KaylaCard(E_RIGHT, "Better", ("Real-life English",), "correction"),
                    KaylaCard(E_SPEAK, "Say it out loud", ("That is the part most learners skip",), "takeaway"),
                ]
            )
        cards.append(KaylaCard(E_REPEAT, "Practice tip", ("Repeat the better version twice",), "takeaway"))
    elif video_format == "saloo_demo":
        cards = [
            KaylaCard(E_PHONE, "Real app practice", ("Talk like it is a voice call",), "hook"),
            KaylaCard(E_AUDIO, "Saloo listens", ("Then corrects your sentence",), "phone"),
            KaylaCard(E_RIGHT, "Try again", ("Shorter. Clearer. Natural.",), "truth"),
            KaylaCard(E_SPEAK, "This is the point", ("Practice replying out loud",), "takeaway"),
        ]
    elif video_format == "myth_buster":
        cards = [
            KaylaCard(E_MYTH, "Myth", ("Watching English = speaking English",), "myth"),
            KaylaCard(E_RIGHT, "Truth", ("You need to answer out loud",), "truth"),
            KaylaCard(E_REPEAT, "Real practice", ("Rehearse before real conversations",), "takeaway"),
        ]
    elif video_format == "specific_situation":
        cards = [
            KaylaCard(_topic_icon_v2(combined), "Real-life moment", ("This is where English matters",), "hook"),
            KaylaCard(problem_icon, "The hard part", (problem_line,), "myth"),
            KaylaCard(fix_icon, "Practice this first", (fix_line,), "truth"),
            KaylaCard(E_PHONE, "Before it happens", ("Warm up inside Saloo",), "phone"),
        ]
    elif video_format == "confession":
        cards = [
            KaylaCard(E_NERVOUS, "Before", ("I understood English...",), "hook"),
            KaylaCard(E_QUIET, "But then", ("My mouth froze",), "myth"),
            KaylaCard(fix_icon, "What helped", (fix_line,), "truth"),
            KaylaCard(E_SPARK, "After practice", ("Speaking felt less scary",), "takeaway"),
        ]
    else:
        cards = [
            KaylaCard(problem_icon, "This is the problem", (problem_line,), "hook"),
            KaylaCard(E_CHAT, "Real English", ("You need replies, not random words",), "takeaway"),
            KaylaCard(fix_icon, "The fix", (fix_line,), "truth"),
            KaylaCard(E_PHONE, "Saloo English", ("Practice before you need it",), "phone"),
        ]

    deduped: list[KaylaCard] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for card in cards:
        key = (card.title.lower(), tuple(line.lower() for line in card.lines))
        if key in seen:
            continue
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


def _icon_style(symbol: str, tone: str) -> tuple[str, tuple[int, int, int], str]:
    red = (246, 72, 92)
    green = (34, 197, 94)
    blue = (59, 130, 246)
    amber = (245, 158, 11)
    purple = (139, 92, 246)
    slate = (55, 65, 81)

    if symbol == E_WRONG or tone == "myth":
        return "X", red, "square"
    if symbol == E_RIGHT or tone == "truth" or tone == "correction":
        return "OK", green, "square"
    if symbol == E_PHONE or tone == "phone":
        return "APP", blue, "pill"
    if symbol == E_AUDIO:
        return "AUDIO", purple, "pill"
    if symbol == E_WARN:
        return "!", amber, "circle"
    if symbol == E_REPEAT:
        return "REPEAT", green, "pill"
    if symbol == E_SPEAK:
        return "SAY", blue, "pill"
    if symbol == E_WORK:
        return "WORK", slate, "pill"
    if symbol == E_TRAVEL:
        return "TRAVEL", blue, "pill"
    return "TIP", slate, "pill"


def _line_without_emoji(text: str) -> str:
    cleaned = text
    for emoji in [
        E_WARN,
        E_WRONG,
        E_RIGHT,
        E_PHONE,
        E_AUDIO,
        E_SPEAK,
        E_REPEAT,
        E_SPARK,
        E_MYTH,
        E_NERVOUS,
        E_CHAT,
        E_WORK,
        E_TRAVEL,
        E_COFFEE,
        E_BRAIN,
        E_MELT,
        E_BOOKS,
        E_CLOCK,
        E_QUIET,
    ]:
        cleaned = cleaned.replace(emoji, "")
    return re.sub(r"\s+", " ", cleaned).strip()


def _draw_icon(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    symbol: str,
    color: tuple[int, int, int],
    shape: str,
    font: ImageFont.ImageFont,
) -> int:
    if shape == "circle":
        draw.ellipse((x, y, x + 64, y + 64), fill=color)
        label = symbol
        width = 64
    elif shape == "square":
        draw.rounded_rectangle((x, y, x + 64, y + 64), radius=12, fill=color)
        label = symbol
        width = 64
    else:
        width = 128 if len(symbol) > 3 else 88
        draw.rounded_rectangle((x, y, x + width, y + 64), radius=18, fill=color)
        label = symbol
    bbox = draw.textbbox((0, 0), label, font=font)
    draw.text(
        (x + (width - (bbox[2] - bbox[0])) // 2, y + (64 - (bbox[3] - bbox[1])) // 2 - 2),
        label,
        font=font,
        fill=(255, 255, 255, 255),
    )
    return width


def render_card_png(card: KaylaCard, output_path: Path) -> Path:
    image = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    text_font = _font(50, bold=True)
    icon_font = _font(24, bold=True)

    def strip_text(raw: str, max_chars: int = 54) -> str:
        return _clean_text(_line_without_emoji(raw), max_chars)

    if card.tone == "correction" and len(card.lines) >= 2:
        rows = [(E_WRONG, strip_text(card.lines[0], 44)), (E_RIGHT, strip_text(card.lines[1], 44))]
    elif card.tone in {"hook", "myth", "truth"}:
        main = strip_text(card.lines[0] if card.lines else card.title, 50)
        rows = [(card.emoji, main)]
    else:
        main = strip_text(card.lines[0] if card.lines else card.title, 48)
        rows = [(card.emoji, main)]

    max_strip_width = 900
    strips: list[tuple[str, str, tuple[int, int, int], str, int, int]] = []
    total_height = 0
    for symbol, text in rows:
        label, color, shape = _icon_style(symbol, card.tone)
        icon_width = 128 if shape == "pill" and len(label) > 3 else 88
        available_width = max_strip_width - icon_width - 78
        lines = _wrap_line(draw, text, text_font, available_width)[:1]
        line = lines[0] if lines else text
        text_width = draw.textbbox((0, 0), line, font=text_font)[2]
        strip_width = min(max_strip_width, max(620, icon_width + text_width + 118))
        strip_height = 104
        strips.append((label, line, color, shape, strip_width, strip_height))
        total_height += strip_height + 18
    total_height -= 18

    y = 760 if card.tone in {"hook", "myth"} else 850
    y = min(y, 1120 - total_height)
    for label, line, color, shape, strip_width, strip_height in strips:
        x0 = (1080 - strip_width) // 2
        x1 = x0 + strip_width
        y1 = y + strip_height
        draw.rounded_rectangle((x0 + 6, y + 8, x1 + 6, y1 + 8), radius=16, fill=(0, 0, 0, 88))
        draw.rounded_rectangle((x0, y, x1, y1), radius=16, fill=(255, 255, 255, 250))

        icon_x = x0 + 22
        icon_y = y + 20
        icon_width = _draw_icon(draw, icon_x, icon_y, label, color, shape, icon_font)

        text_x = icon_x + icon_width + 26
        bbox = draw.textbbox((0, 0), line, font=text_font)
        draw.text((text_x, y + (strip_height - (bbox[3] - bbox[1])) // 2 - 5), line, font=text_font, fill=(14, 18, 24, 255))
        y = y1 + 18

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
    active_duration = min(duration, CARD_MAX_SECONDS)
    end_limit = max(start + 1.0, active_duration - 0.55)
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

    normalized_source_duration = _video_duration_seconds(ffmpeg, normalized_source)
    if normalized_source_duration >= APPEND_OUTRO_MAX_SOURCE_SECONDS:
        print(
            "Source video already looks longer than a raw Flow clip. "
            "Skipping appended outro to avoid double outro."
        )
        shutil.copyfile(source_with_cards, output_video)
        if not output_video.exists() or output_video.stat().st_size < 1024:
            raise RuntimeError("Final Kayla video was not created correctly.")
        return output_video

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
        cards = build_kayla_cards_v2(
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
