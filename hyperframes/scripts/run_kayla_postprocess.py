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
MAX_CARDS = 5
CARD_MAX_SECONDS = 12.4


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


def _strip_card_label(value: str) -> str:
    return re.sub(
        r"^\s*(hook|problem|solution|main message|ad message|script|cta|visual direction|prompt 1|image prompt)\s*:\s*",
        "",
        value,
        flags=re.IGNORECASE,
    ).strip()


def _card_line(value: str, fallback: str, max_chars: int = 42) -> str:
    text = _clean_text(value, max_chars + 18)
    text = _strip_card_label(text)
    text = text.strip(" \"'")
    if not text:
        return fallback
    if len(text) <= max_chars:
        return text
    sentence = re.split(r"[.!?;]", text, maxsplit=1)[0].strip()
    if 8 <= len(sentence) <= max_chars:
        return sentence
    words = text.split()
    shortened = " ".join(words[:6]).strip()
    return _clean_text(shortened or fallback, max_chars)


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


def _is_visual_direction(text: str) -> bool:
    lower = text.lower()
    visual_terms = [
        "vertical 9:16",
        "ugc tiktok",
        "iphone quality",
        "natural light",
        "no text overlay",
        "no captions",
        "no futuristic",
        "no robotic",
        "camera",
        "background",
        "bedroom",
        "hotel room",
        "campus library",
        "visual direction",
        "style:",
        "scene:",
        "decor:",
        "prompt:",
        "ad style",
    ]
    return any(term in lower for term in visual_terms)


def _spoken_parts_from_script(script: str) -> list[str]:
    parts: list[str] = []
    normalized = re.sub(
        r"\s+(hook|problem|solution|main message|ad message|script|kayla|saloo|voice|app|narrator|cta|visual direction|image prompt|prompt 1|scene|camera|style)\s*:",
        r"\n\1:",
        script,
        flags=re.IGNORECASE,
    )
    for raw_line in re.split(r"[\n\r]+", normalized):
        line = raw_line.replace("\u2022", " ").strip(" -\t")
        if not line:
            continue
        line = re.sub(
            r"^(hook|problem|solution|main message|ad message|script|kayla|saloo|voice|app|narrator)\s*:\s*",
            "",
            line,
            flags=re.IGNORECASE,
        ).strip()
        line = re.sub(
            r"^kayla speaks in .*?ad style\s+(for|to)\s+",
            "This is for ",
            line,
            flags=re.IGNORECASE,
        )
        line = re.sub(r"^kayla speaks\s+", "", line, flags=re.IGNORECASE)
        line = line.strip("\"'")
        if _is_visual_direction(line):
            continue
        if re.match(r"^(cta|visual direction|image prompt|prompt 1|scene|camera|style)\s*:", line, re.IGNORECASE):
            continue
        if not line or _is_visual_direction(line):
            continue
        parts.extend(piece.strip() for piece in re.split(r"(?<=[.!?])\s+|;\s+", line) if piece.strip())
    return parts


def _subtitle_chunks(script: str, max_cards: int = MAX_CARDS) -> list[str]:
    chunks: list[str] = []
    for part in _spoken_parts_from_script(script):
        words = part.split()
        if not words:
            continue
        if len(" ".join(words)) <= 56:
            chunks.append(" ".join(words))
            if len(chunks) >= max_cards:
                return chunks
            continue
        chunk_count = max(2, min(4, (len(words) + 6) // 7))
        chunk_size = max(4, (len(words) + chunk_count - 1) // chunk_count)
        index = 0
        while index < len(words):
            take = min(chunk_size, len(words) - index)
            candidate_words = words[index : index + take]
            while len(" ".join(candidate_words)) > 56 and len(candidate_words) > 3:
                candidate_words = candidate_words[:-1]
            chunks.append(" ".join(candidate_words))
            index += len(candidate_words)
            if len(chunks) >= max_cards:
                return chunks
    return [_clean_text(chunk, 56) for chunk in chunks if chunk]


def _subtitle_icon(text: str) -> str:
    lower = text.lower()
    if any(term in lower for term in ["don't say", "dont say", "do not say", "not:", "mistake", "wrong"]):
        return E_WRONG
    if any(term in lower for term in ["say", "better", "natural", "correct"]):
        return E_RIGHT
    if any(term in lower for term in ["saloo", "app", "phone"]):
        return E_PHONE
    if any(term in lower for term in ["speak", "speaking", "reply", "conversation"]):
        return E_SPEAK
    if any(term in lower for term in ["listen", "pronunciation", "voice"]):
        return E_AUDIO
    return E_CHAT


def _subtitle_cards(script: str) -> list[KaylaCard]:
    cards: list[KaylaCard] = []
    for chunk in _subtitle_chunks(script):
        icon = _subtitle_icon(chunk)
        tone = "subtitle"
        if icon == E_WRONG:
            tone = "myth"
        elif icon == E_RIGHT:
            tone = "truth"
        elif icon == E_PHONE:
            tone = "phone"
        cards.append(KaylaCard(icon, "Script card", (chunk,), tone))
    return cards[:MAX_CARDS]


def _explicit_card_lines(script: str) -> list[str]:
    lines: list[tuple[int, str]] = []
    pattern = r"(?:^|\n)\s*Card\s*(\d{1,2})\s*:\s*(.*?)(?=\n\s*Card\s*\d{1,2}\s*:|\n\s*(?:Hook|Problem|Solution|Main message|Ad message|CTA|Visual direction|Prompt 1|Image prompt)\s*:|$)"
    for number, text in re.findall(pattern, script or "", flags=re.IGNORECASE | re.DOTALL):
        cleaned = _card_line(text, "", 64)
        if cleaned and not _is_visual_direction(cleaned):
            lines.append((int(number), cleaned))
    return [text for _, text in sorted(lines, key=lambda item: item[0])][:MAX_CARDS]


def _explicit_cards(script: str) -> list[KaylaCard]:
    cards: list[KaylaCard] = []
    for line in _explicit_card_lines(script):
        icon = _subtitle_icon(line)
        tone = "tip"
        if icon == E_WRONG:
            tone = "stop"
        elif icon == E_RIGHT:
            tone = "fix"
        elif icon == E_PHONE:
            tone = "app"
        elif icon == E_AUDIO:
            tone = "try"
        cards.append(KaylaCard(icon, "Card", (line,), tone))
    return cards


def _extract_correction_cards_v2(text: str) -> list[KaylaCard]:
    cards: list[KaylaCard] = []
    patterns = [
        r"(?:don't|dont|do not|not)\s+say:?\s*[\"'\u201c\u201d]?(.{2,70}?)[\"'\u201c\u201d]?(?:\.|\n|;)\s*(?:say|say this|instead|natural):?\s*[\"'\u201c\u201d]?(.{2,70}?)[\"'\u201c\u201d]?(?:\.|\n|;|$)",
        r"not:?\s*[\"'\u201c\u201d]?(.{2,70}?)[\"'\u201c\u201d]?(?:\.|\n|;)\s*say:?\s*[\"'\u201c\u201d]?(.{2,70}?)[\"'\u201c\u201d]?(?:\.|\n|;|$)",
    ]
    for pattern in patterns:
        for wrong, right in re.findall(pattern, text, flags=re.IGNORECASE | re.DOTALL):
            wrong = _clean_text(wrong, 44)
            right = _clean_text(right, 44)
            if wrong and right and wrong.lower() != right.lower():
                cards.append(KaylaCard(E_RIGHT, "Say it naturally", (wrong, right), "correction"))
            if len(cards) >= MAX_CARDS:
                return cards
    return cards


def _field_value(script: str, label: str) -> str:
    value = _extract_field(script, label)
    if value and not _is_visual_direction(value):
        return value
    return ""


def _first_spoken_part(script: str) -> str:
    for part in _spoken_parts_from_script(script):
        cleaned = _card_line(part, "", 54)
        if cleaned and not _is_visual_direction(cleaned):
            return cleaned
    return ""


def _punchline(value: str, fallback: str, max_chars: int = 54) -> str:
    text = _card_line(value, fallback, max_chars)
    text = re.sub(r"^(this is for anyone who wants to|this is for learners who want to)\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^kayla speaks in .*?ad style\s+(for|to)\s+", "This is for ", text, flags=re.IGNORECASE)
    text = re.sub(r"^(kayla speaks|kayla says)\s+", "", text, flags=re.IGNORECASE)
    text = text.strip(" .")
    if not text:
        return fallback
    return text[0].upper() + text[1:]


def _problem_card_line(combined: str, script: str) -> str:
    problem = _field_value(script, "Problem")
    if problem:
        return _punchline(problem, "You understand English, then freeze")
    if _contains(combined, ["small mistake", "mistakes", "sound less natural"]):
        return "Small mistakes can sound less natural"
    if _contains(combined, ["starting strong", "stopping after", "few days"]):
        return "Starting is easy. Staying consistent is hard"
    if _contains(combined, ["freeze", "stuck", "mouth"]):
        return "You know the words, then freeze"
    if _contains(combined, ["translate", "translating"]):
        return "Translating first slows you down"
    if _contains(combined, ["confidence", "scared", "nervous"]):
        return "Confidence needs real practice"
    return _punchline(_first_spoken_part(script), "You need real speaking practice")


def _benefit_card_line(combined: str, script: str) -> str:
    solution = _field_value(script, "Solution") or _field_value(script, "Main message")
    if solution:
        if _contains(solution, ["saloo", "real conversations", "real conversation"]):
            return "Practice real replies before real conversations"
        return _punchline(solution, "Practice real replies before real life")
    if _contains(combined, ["natural", "phrases", "phrase"]):
        return "Use phrases people actually say"
    if _contains(combined, ["small repeatable lessons", "daily habit"]):
        return "Small repeatable lessons build the habit"
    if _contains(combined, ["reply", "answer", "conversation"]):
        return "Practice replies before real conversations"
    if _contains(combined, ["pronunciation", "listen", "voice"]):
        return "Listen, repeat, and improve"
    return "Practice real replies before you need them"


def _editorial_cards(title: str, script: str, prompt: str) -> list[KaylaCard]:
    combined = f"{title}\n{script}\n{prompt}"
    video_format = _classify_kayla_format(script, prompt, title)
    corrections = _extract_correction_cards_v2(combined)
    hook = _field_value(script, "Hook")
    problem = _problem_card_line(combined, script)
    benefit = _benefit_card_line(combined, script)

    if video_format == "mini_lesson":
        cards = corrections[:3]
        if not cards:
            cards = [
                KaylaCard(E_WRONG, "Stop", ("This sounds translated",), "stop"),
                KaylaCard(E_RIGHT, "Fix", ("Use the natural phrase",), "fix"),
            ]
        cards.append(KaylaCard(E_SPEAK, "Try", ("Say the better version out loud",), "try"))
    elif video_format == "saloo_demo":
        cards = [
            KaylaCard(E_WARN, "Why", (problem,), "why"),
            KaylaCard(E_PHONE, "App", ("Practice the reply in Saloo",), "app"),
            KaylaCard(E_RIGHT, "Fix", (benefit,), "fix"),
            KaylaCard(E_SPEAK, "Try", ("Repeat it before real life",), "try"),
        ]
    elif video_format == "myth_buster":
        cards = [
            KaylaCard(E_WRONG, "Stop", (_punchline(hook, "Watching is not enough"),), "stop"),
            KaylaCard(E_RIGHT, "Fix", (benefit,), "fix"),
            KaylaCard(E_PHONE, "App", ("Practice out loud in Saloo",), "app"),
        ]
    elif video_format == "confession":
        cards = [
            KaylaCard(E_NERVOUS, "Why", (problem,), "why"),
            KaylaCard(E_PHONE, "App", ("Practice privately first",), "app"),
            KaylaCard(E_SPARK, "Try", ("Build confidence before speaking",), "try"),
        ]
    elif video_format == "specific_situation":
        cards = [
            KaylaCard(_topic_icon_v2(combined), "Tip", (_punchline(hook, "Practice before the real moment"),), "tip"),
            KaylaCard(E_WARN, "Why", (problem,), "why"),
            KaylaCard(E_PHONE, "App", ("Warm up inside Saloo first",), "app"),
            KaylaCard(E_RIGHT, "Fix", (benefit,), "fix"),
        ]
    else:
        first_line = _first_spoken_part(script)
        cards = [
            KaylaCard(E_CHAT, "Tip", (_punchline(hook or first_line, "This is for real conversations"),), "tip"),
            KaylaCard(E_WARN, "Why", (problem,), "why"),
            KaylaCard(E_PHONE, "App", ("Practice inside Saloo English",), "app"),
            KaylaCard(E_RIGHT, "Fix", (benefit,), "fix"),
        ]

    deduped: list[KaylaCard] = []
    seen: set[str] = set()
    banned = ["kayla ads concept", "script card", "prompt", "visual direction"]
    for card in cards:
        lines = tuple(_punchline(line, "", 56) for line in card.lines)
        joined = " ".join(lines).lower()
        if not joined or joined in seen or any(term in joined for term in banned):
            continue
        deduped.append(KaylaCard(card.emoji, card.title, lines, card.tone))
        seen.add(joined)
        if len(deduped) >= MAX_CARDS:
            break
    return deduped


def build_kayla_cards_v2(title: str, script: str, prompt: str) -> list[KaylaCard]:
    combined = f"{title}\n{script}\n{prompt}"
    explicit_cards = _explicit_cards(script)
    if len(explicit_cards) >= 2:
        return explicit_cards

    video_format = _classify_kayla_format(script, prompt, title)
    problem_icon, problem_line = _card_problem(combined)
    fix_icon, fix_line = _card_fix(combined)
    corrections = _extract_correction_cards_v2(combined)
    editorial_cards = _editorial_cards(title, script, prompt)
    if len(editorial_cards) >= 2:
        return editorial_cards

    if video_format == "mini_lesson":
        cards = [KaylaCard(E_WARN, "Quick English fix", ("Stop sounding translated",), "hook")]
        cards.extend(corrections[:5])
        if not corrections:
            cards.extend(
                [
                    KaylaCard(E_WRONG, "Not natural", ("Textbook English",), "correction"),
                    KaylaCard(E_RIGHT, "Better", ("Real-life English",), "correction"),
                    KaylaCard(E_SPEAK, "Say it out loud", ("Most learners skip this part",), "takeaway"),
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
            KaylaCard(problem_icon, "The hard part", (_card_line(problem_line, "You need real replies"),), "myth"),
            KaylaCard(fix_icon, "Practice this first", (_card_line(fix_line, "Practice real replies"),), "truth"),
            KaylaCard(E_PHONE, "Before it happens", ("Warm up inside Saloo",), "phone"),
        ]
    elif video_format == "confession":
        cards = [
            KaylaCard(E_NERVOUS, "Before", ("I understood English...",), "hook"),
            KaylaCard(E_QUIET, "But then", ("My mouth froze",), "myth"),
            KaylaCard(fix_icon, "What helped", (_card_line(fix_line, "Practice before real life"),), "truth"),
            KaylaCard(E_SPARK, "After practice", ("Speaking felt less scary",), "takeaway"),
        ]
    else:
        cards = [
            KaylaCard(problem_icon, "This is the problem", (_card_line(problem_line, "You understand it..."),), "hook"),
            KaylaCard(E_CHAT, "Real English", ("You need replies, not random words",), "takeaway"),
            KaylaCard(fix_icon, "The fix", (_card_line(fix_line, "Practice real replies"),), "truth"),
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

    if tone == "stop" or symbol == E_WRONG or tone == "myth":
        return "STOP", red, "pill"
    if tone == "fix":
        return "FIX", green, "pill"
    if tone == "why":
        return "WHY", amber, "pill"
    if tone == "try":
        return "TRY", blue, "pill"
    if tone == "tip":
        return "TIP", blue, "pill"
    if tone == "app":
        return "APP", blue, "pill"
    if symbol == E_WRONG or tone == "myth":
        return "X", red, "square"
    if symbol == E_RIGHT or tone == "truth" or tone == "correction":
        return "OK", green, "square"
    if symbol == E_PHONE or tone == "phone":
        return "APP", blue, "pill"
    if symbol == E_AUDIO:
        return "AUDIO", purple, "pill"
    if symbol == E_CHAT or tone == "subtitle":
        return "TIP", blue, "pill"
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
        return _clean_text(_strip_card_label(_line_without_emoji(raw)), max_chars)

    if card.tone == "correction" and len(card.lines) >= 2:
        rows = [(E_WRONG, strip_text(card.lines[0], 44)), (E_RIGHT, strip_text(card.lines[1], 44))]
    elif card.tone in {"hook", "myth", "truth", "subtitle"}:
        main = strip_text(card.lines[0] if card.lines else card.title, 56)
        rows = [(card.emoji, main)]
    else:
        main = strip_text(card.lines[0] if card.lines else card.title, 56)
        rows = [(card.emoji, main)]

    max_strip_width = 900
    strips: list[tuple[str, list[str], tuple[int, int, int], str, int, int]] = []
    total_height = 0
    for symbol, text in rows:
        label, color, shape = _icon_style(symbol, card.tone)
        icon_width = 128 if shape == "pill" and len(label) > 3 else 88
        available_width = max_strip_width - icon_width - 78
        wrapped_lines = _wrap_line(draw, text, text_font, available_width)[:2]
        if not wrapped_lines:
            wrapped_lines = [text]
        text_width = max(draw.textbbox((0, 0), line, font=text_font)[2] for line in wrapped_lines)
        strip_width = min(max_strip_width, max(620, icon_width + text_width + 118))
        strip_height = 104 if len(wrapped_lines) == 1 else 154
        strips.append((label, wrapped_lines, color, shape, strip_width, strip_height))
        total_height += strip_height + 18
    total_height -= 18

    y = 760 if card.tone in {"hook", "myth"} else 850
    y = min(y, 1120 - total_height)
    for label, wrapped_lines, color, shape, strip_width, strip_height in strips:
        x0 = (1080 - strip_width) // 2
        x1 = x0 + strip_width
        y1 = y + strip_height
        draw.rounded_rectangle((x0 + 6, y + 8, x1 + 6, y1 + 8), radius=16, fill=(0, 0, 0, 88))
        draw.rounded_rectangle((x0, y, x1, y1), radius=16, fill=(255, 255, 255, 250))

        icon_x = x0 + 22
        icon_y = y + 20
        icon_width = _draw_icon(draw, icon_x, icon_y, label, color, shape, icon_font)

        text_x = icon_x + icon_width + 26
        line_height = 54
        text_block_height = len(wrapped_lines) * line_height
        text_y = y + (strip_height - text_block_height) // 2 - 5
        for line in wrapped_lines:
            draw.text((text_x, text_y), line, font=text_font, fill=(14, 18, 24, 255))
            text_y += line_height
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


def _card_windows(cards: list[KaylaCard], duration: float) -> list[tuple[float, float]]:
    card_count = len(cards)
    if card_count <= 0:
        return []
    tones = {card.tone for card in cards}
    start = 0.35 if "correction" in tones else 0.55
    active_duration = min(duration, CARD_MAX_SECONDS)
    if "phone" in tones:
        active_duration = min(duration, 10.8)
    elif "correction" in tones:
        active_duration = min(duration, 13.2)
    end_limit = max(start + 1.0, active_duration - 0.55)
    span = max(1.0, end_limit - start)
    slot = span / card_count
    visible = min(2.4, max(1.2, slot * 0.78))
    return [(start + index * slot, min(start + index * slot + visible, end_limit)) for index in range(card_count)]


def overlay_cards(ffmpeg: str, source_video: Path, output_video: Path, cards: list[KaylaCard], work_dir: Path) -> Path:
    if not cards:
        shutil.copyfile(source_video, output_video)
        return output_video

    duration = _video_duration_seconds(ffmpeg, source_video)
    windows = _card_windows(cards, duration)
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
    print(f"Source video duration before outro: {normalized_source_duration:.2f}s")
    print("Appending Kayla outro asset.")

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
            label, _, _ = _icon_style(card.emoji, card.tone)
            print(f"  card_{index}: {label} {card.title} | {' / '.join(card.lines)}")
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
