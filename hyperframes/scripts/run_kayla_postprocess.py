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
MAX_CARDS = 3
CARD_MAX_SECONDS = 12.4
KAYLA_WHISPER_MODEL = os.getenv("KAYLA_WHISPER_MODEL", "base")
KAYLA_SUBTITLES_ENABLED = os.getenv("KAYLA_AUTO_SUBTITLES", "1").strip().lower() not in {"0", "false", "no"}


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
    parser.add_argument("--execute", action="store_true", help="Create final video for publication.")
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


def _prompt_spoken_script(prompt: str) -> str:
    match = re.search(r'Spoken script exactly:\s*"([^"]+)"', prompt or "", flags=re.IGNORECASE | re.DOTALL)
    if match:
        return _clean_text(match.group(1), 420)
    return ""


def _sentence_parts(text: str) -> list[str]:
    return [
        part.strip(" \"'")
        for part in re.split(r"(?<=[.!?])\s+", text.strip())
        if part.strip(" \"'")
    ]


def _premium_line(text: str, max_chars: int = 44) -> str:
    cleaned = _clean_text(_strip_card_label(_line_without_emoji(text)), max_chars)
    cleaned = re.sub(r"^(not|say|better|repeat|saloo)\s*:\s*", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip(" .")


def _dedupe_prompt_cards(cards: list[KaylaCard]) -> list[KaylaCard]:
    deduped: list[KaylaCard] = []
    seen: set[tuple[str, ...]] = set()
    for card in cards:
        lines = tuple(_premium_line(line, 68) for line in card.lines if _premium_line(line, 68))
        if not lines:
            continue
        key = tuple(line.lower() for line in lines)
        if key in seen:
            continue
        deduped.append(KaylaCard(card.emoji, card.title, lines, card.tone))
        seen.add(key)
        if len(deduped) >= MAX_CARDS:
            break
    return deduped


def _extract_not_say_pairs(text: str, max_pairs: int = 3) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    patterns = [
        r"(?:don't|dont|do not|not)\s+say:?\s*[\"'\u201c\u201d]?(.{2,72}?)[\"'\u201c\u201d]?(?:\.|\n|;)\s*(?:say|say this|instead|natural):?\s*[\"'\u201c\u201d]?(.{2,72}?)[\"'\u201c\u201d]?(?:\.|\n|;|$)",
        r"not:?\s*[\"'\u201c\u201d]?(.{2,72}?)[\"'\u201c\u201d]?(?:\.|\n|;)\s*say:?\s*[\"'\u201c\u201d]?(.{2,72}?)[\"'\u201c\u201d]?(?:\.|\n|;|$)",
    ]
    for pattern in patterns:
        for wrong, right in re.findall(pattern, text or "", flags=re.IGNORECASE | re.DOTALL):
            wrong = _premium_line(wrong, 42)
            right = _premium_line(right, 42)
            if wrong and right and wrong.lower() != right.lower():
                pairs.append((wrong, right))
            if len(pairs) >= max_pairs:
                return pairs
    return pairs


def _prompt_mini_lesson_cards(prompt: str) -> list[KaylaCard]:
    source = _prompt_spoken_script(prompt) or prompt
    cards = [
        KaylaCard(E_RIGHT, "Premium subtitle", (wrong, right), "premium_correction")
        for wrong, right in _extract_not_say_pairs(source, MAX_CARDS)
    ]
    return _dedupe_prompt_cards(cards)


def _quoted_timing_value(prompt: str, second: str) -> str:
    match = re.search(
        rf"At\s+0:{re.escape(second)}.*?:\s*\"([^\"]+)\"",
        prompt or "",
        flags=re.IGNORECASE | re.DOTALL,
    )
    return _clean_text(match.group(1), 90) if match else ""


def _extract_say_correction(text: str) -> str:
    match = re.search(r"\bSay:\s*(.+)$", text or "", flags=re.IGNORECASE)
    if match:
        return _premium_line(match.group(1), 44)
    return _premium_line(text, 44)


def _prompt_saloo_demo_cards(prompt: str) -> list[KaylaCard]:
    wrong = _quoted_timing_value(prompt, "02")
    app_reply = _quoted_timing_value(prompt, "05")
    repeat = _quoted_timing_value(prompt, "08")
    correction = _extract_say_correction(app_reply)
    cards: list[KaylaCard] = []
    if wrong and correction:
        cards.append(KaylaCard(E_RIGHT, "Premium subtitle", (wrong, correction), "premium_correction"))
    elif wrong:
        cards.append(KaylaCard(E_WRONG, "Premium subtitle", (wrong,), "premium_emphasis"))
    if correction:
        cards.append(KaylaCard(E_PHONE, "Premium subtitle", (f"Saloo corrects: {correction}",), "premium_saloo"))
    if repeat:
        cards.append(KaylaCard(E_SPEAK, "Premium subtitle", (f"Repeat: {repeat}",), "premium_repeat"))
    return _dedupe_prompt_cards(cards)


def _prompt_hook_cards(prompt: str) -> list[KaylaCard]:
    script = _prompt_spoken_script(prompt)
    if not script:
        return []
    parts = _sentence_parts(script)
    if not parts:
        return []
    cards = [KaylaCard(E_CHAT, "Premium subtitle", (_premium_line(parts[0], 54),), "premium_hook")]
    if len(parts) > 1:
        benefit = next(
            (part for part in parts[1:] if _contains(part, ["saloo", "practice", "correct", "conversation", "repeat"])),
            parts[1],
        )
        cards.append(KaylaCard(E_SPEAK, "Premium subtitle", (_premium_line(benefit, 62),), "premium_benefit"))
    return _dedupe_prompt_cards(cards)


def _prompt_confession_cards(prompt: str) -> list[KaylaCard]:
    script = _prompt_spoken_script(prompt)
    if not script:
        return []
    parts = _sentence_parts(script)
    if not parts:
        return []
    problem = parts[0]
    transformation = next(
        (part for part in parts[1:] if _contains(part, ["now", "saloo", "help", "practice", "confidence"])),
        parts[-1],
    )
    return _dedupe_prompt_cards(
        [
            KaylaCard(E_NERVOUS, "Premium subtitle", (_premium_line(problem, 58),), "premium_hook"),
            KaylaCard(E_SPARK, "Premium subtitle", (_premium_line(transformation, 62),), "premium_benefit"),
        ]
    )


def _prompt_specific_cards(prompt: str) -> list[KaylaCard]:
    script = _prompt_spoken_script(prompt)
    if not script:
        return []
    parts = _sentence_parts(script)
    selected = parts[:2] if len(parts) > 1 else parts
    return _dedupe_prompt_cards(
        [KaylaCard(E_CHAT, "Premium subtitle", (_premium_line(part, 62),), "premium_benefit") for part in selected]
    )


def _prompt_first_cards(title: str, prompt: str) -> list[KaylaCard]:
    video_format = _classify_kayla_format("", prompt, title)
    if video_format == "mini_lesson":
        cards = _prompt_mini_lesson_cards(prompt)
    elif video_format == "saloo_demo":
        cards = _prompt_saloo_demo_cards(prompt)
    elif video_format == "confession":
        cards = _prompt_confession_cards(prompt)
    elif video_format == "specific_situation":
        cards = _prompt_specific_cards(prompt)
    else:
        cards = _prompt_hook_cards(prompt)
    if cards:
        return cards[:MAX_CARDS]
    fallback = _subtitle_cards(_prompt_spoken_script(prompt))[:MAX_CARDS]
    return [KaylaCard(card.emoji, card.title, card.lines, "premium_hook") for card in fallback]


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
    if not (prompt or "").strip():
        return []

    prompt_cards = _prompt_first_cards(title, prompt)
    if prompt_cards:
        return prompt_cards[:MAX_CARDS]

    combined = f"{title}\n{script}\n{prompt}"
    explicit_cards = _explicit_cards(script)
    if len(explicit_cards) >= 2:
        return explicit_cards[:MAX_CARDS]

    video_format = _classify_kayla_format(script, prompt, title)
    problem_icon, problem_line = _card_problem(combined)
    fix_icon, fix_line = _card_fix(combined)
    corrections = _extract_correction_cards_v2(combined)
    editorial_cards = _editorial_cards(title, script, prompt)
    if len(editorial_cards) >= 2:
        return editorial_cards[:MAX_CARDS]

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
    text_font = _font(64, bold=True)
    small_font = _font(58, bold=True)

    def strip_text(raw: str, max_chars: int = 54) -> str:
        return _clean_text(_strip_card_label(_line_without_emoji(raw)), max_chars)

    if card.tone in {"correction", "premium_correction"} and len(card.lines) >= 2:
        rows = [
            (strip_text(card.lines[0], 40), (255, 107, 107, 255), small_font),
            (strip_text(card.lines[1], 40), (90, 242, 164, 255), text_font),
        ]
    else:
        color = (255, 255, 255, 255)
        if card.tone in {"premium_benefit", "premium_saloo", "premium_repeat"}:
            color = (90, 242, 164, 255)
        text = strip_text(card.lines[0] if card.lines else card.title, 62)
        rows = [(text, color, text_font)]

    wrapped_rows: list[tuple[list[str], tuple[int, int, int, int], ImageFont.ImageFont]] = []
    total_height = 0
    max_width = 900
    for text, color, font in rows:
        wrapped = _wrap_line(draw, text, font, max_width)[:2] or [text]
        line_height = 72 if font == text_font else 66
        wrapped_rows.append((wrapped, color, font))
        total_height += len(wrapped) * line_height + 10
    total_height = max(0, total_height - 10)

    y = 1230 if len(rows) == 1 else 1180
    y = min(y, 1500 - total_height)
    for wrapped, color, font in wrapped_rows:
        line_height = 72 if font == text_font else 66
        for line in wrapped:
            bbox = draw.textbbox((0, 0), line, font=font, stroke_width=3)
            width = bbox[2] - bbox[0]
            x = (1080 - width) // 2
            draw.text(
                (x + 4, y + 5),
                line,
                font=font,
                fill=(0, 0, 0, 145),
                stroke_width=5,
                stroke_fill=(0, 0, 0, 120),
            )
            draw.text(
                (x, y),
                line,
                font=font,
                fill=color,
                stroke_width=3,
                stroke_fill=(0, 0, 0, 220),
            )
            y += line_height
        y += 10

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
    start = 0.35 if tones.intersection({"correction", "premium_correction"}) else 0.55
    active_duration = min(duration, CARD_MAX_SECONDS)
    if tones.intersection({"phone", "premium_saloo"}):
        active_duration = min(duration, 10.8)
    elif tones.intersection({"correction", "premium_correction"}):
        active_duration = min(duration, 13.2)
    end_limit = max(start + 1.0, active_duration - 0.55)
    span = max(1.0, end_limit - start)
    slot = span / card_count
    visible = min(3.2, max(1.8, slot * 0.82))
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


def _ass_timestamp(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    whole_seconds = int(seconds % 60)
    centiseconds = int(round((seconds - int(seconds)) * 100))
    if centiseconds >= 100:
        whole_seconds += 1
        centiseconds -= 100
    return f"{hours}:{minutes:02d}:{whole_seconds:02d}.{centiseconds:02d}"


def _escape_ass_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    cleaned = cleaned.replace("{", "").replace("}", "")
    return cleaned


def _wrap_subtitle_words(words: list[str], max_chars: int = 28) -> str:
    if not words:
        return ""
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join([*current, word]).strip()
        if current and len(candidate) > max_chars and len(lines) < 1:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return r"\N".join(lines[:2])


def _chunk_word_timings(words: list[object]) -> list[tuple[float, float, str]]:
    chunks: list[tuple[float, float, str]] = []
    current_words: list[str] = []
    start: float | None = None
    end: float | None = None

    for word_info in words:
        text = _escape_ass_text(getattr(word_info, "word", "")).strip()
        if not text:
            continue
        if start is None:
            start = float(getattr(word_info, "start", 0.0) or 0.0)
        end = float(getattr(word_info, "end", start + 1.0) or start + 1.0)
        current_words.append(text)
        joined = " ".join(current_words)
        if len(current_words) >= 5 or len(joined) >= 34:
            chunks.append((start, max(end, start + 0.65), _wrap_subtitle_words(current_words)))
            current_words = []
            start = None
            end = None

    if current_words and start is not None:
        chunks.append((start, max(end or start + 1.0, start + 0.65), _wrap_subtitle_words(current_words)))
    return chunks


def _extract_audio_for_whisper(ffmpeg: str, video_path: Path, audio_path: Path) -> Path:
    _run_ffmpeg(
        [
            ffmpeg,
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-t",
            "90",
            str(audio_path),
        ]
    )
    if not audio_path.exists() or audio_path.stat().st_size < 1024:
        raise RuntimeError("Whisper audio extraction failed.")
    return audio_path


def _transcribe_kayla_audio(audio_path: Path) -> list[tuple[float, float, str]]:
    from faster_whisper import WhisperModel

    print(f"Kayla auto-subtitles: loading faster-whisper model '{KAYLA_WHISPER_MODEL}' on CPU.")
    model = WhisperModel(KAYLA_WHISPER_MODEL, device="cpu", compute_type="int8")
    segments, info = model.transcribe(
        str(audio_path),
        language="en",
        beam_size=1,
        vad_filter=True,
        word_timestamps=True,
    )
    print(f"Kayla auto-subtitles: detected language={info.language} probability={info.language_probability:.2f}")

    cues: list[tuple[float, float, str]] = []
    for segment in segments:
        word_timings = getattr(segment, "words", None)
        if word_timings:
            cues.extend(_chunk_word_timings(list(word_timings)))
            continue
        text = _escape_ass_text(getattr(segment, "text", ""))
        if text:
            start = float(getattr(segment, "start", 0.0) or 0.0)
            end = float(getattr(segment, "end", start + 1.0) or start + 1.0)
            cues.append((start, max(end, start + 0.8), _wrap_subtitle_words(text.split())))

    filtered = [(start, end, text) for start, end, text in cues if text.strip()]
    print(f"Kayla auto-subtitles: {len(filtered)} subtitle cue(s) created.")
    return filtered


def _write_ass_subtitles(cues: list[tuple[float, float, str]], ass_path: Path) -> Path:
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: KaylaSub,Arial,72,&H00FFFFFF,&H00FFFFFF,&HAA000000,&H88000000,-1,0,0,0,100,100,0,0,1,5,1,2,90,90,360,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    for start, end, text in cues:
        events.append(f"Dialogue: 0,{_ass_timestamp(start)},{_ass_timestamp(end)},KaylaSub,,0,0,0,,{text}")
    ass_path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return ass_path


def _ffmpeg_filter_path(path: Path) -> str:
    # FFmpeg filters parse ':' and '\' specially, even on Windows.
    return str(path.resolve()).replace("\\", "/").replace(":", r"\:")


def add_auto_subtitles(ffmpeg: str, source_video: Path, output_video: Path, work_dir: Path) -> Path:
    if not KAYLA_SUBTITLES_ENABLED:
        print("Kayla auto-subtitles disabled by KAYLA_AUTO_SUBTITLES.")
        shutil.copyfile(source_video, output_video)
        return output_video
    if not _video_has_audio(ffmpeg, source_video):
        print("Kayla auto-subtitles skipped: source has no audio.")
        shutil.copyfile(source_video, output_video)
        return output_video

    try:
        audio_path = _extract_audio_for_whisper(ffmpeg, source_video, work_dir / "kayla_whisper.wav")
        cues = _transcribe_kayla_audio(audio_path)
        if not cues:
            raise RuntimeError("Whisper returned no subtitles.")
        ass_path = _write_ass_subtitles(cues, work_dir / "kayla_subtitles.ass")
        _run_ffmpeg(
            [
                ffmpeg,
                "-y",
                "-i",
                str(source_video),
                "-vf",
                f"ass='{_ffmpeg_filter_path(ass_path)}'",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "copy",
                str(output_video),
            ]
        )
        if not output_video.exists() or output_video.stat().st_size < 1024:
            raise RuntimeError("Subtitled Kayla source was not created correctly.")
        print("Kayla auto-subtitles added successfully.")
        return output_video
    except Exception as exc:
        print(f"Kayla auto-subtitles skipped safely: {exc}")
        shutil.copyfile(source_video, output_video)
        return output_video


def render_final_video(source_video: Path, output_video: Path, work_dir: Path, cards: list[KaylaCard]) -> Path:
    import imageio_ffmpeg

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    normalized_source_plain = work_dir / "source_normalized_plain.mp4"
    normalized_source = work_dir / "source_normalized.mp4"
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
            str(normalized_source_plain),
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
            str(normalized_source_plain),
        ]
    _run_ffmpeg(normalize_cmd)
    add_auto_subtitles(ffmpeg, normalized_source_plain, normalized_source, work_dir)

    normalized_source_duration = _video_duration_seconds(ffmpeg, normalized_source)
    print(f"Final Kayla video duration without outro: {normalized_source_duration:.2f}s")
    print("Kayla cards disabled. Keeping Flow video clean, adding auto-subtitles, no outro.")
    shutil.copyfile(normalized_source, output_video)
    if not output_video.exists() or output_video.stat().st_size < 1024:
        raise RuntimeError("Final Kayla video was not created correctly.")
    return output_video


def _output_name(row: dict) -> str:
    props = row.get("properties", {})
    date = prop_text(props, "Date Publication")
    slot = prop_text(props, "Slot").replace(":", "")
    page_id = row.get("id", "")[:8]
    return f"kayla-flow-final-{date}-{slot}-{page_id}.mp4"


def _local_output_dir() -> Path:
    configured = os.getenv("KAYLA_OUTPUT_DIR", "kayla-output").strip() or "kayla-output"
    output_dir = Path(configured)
    if not output_dir.is_absolute():
        output_dir = repo_root() / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _drive_upload_enabled() -> bool:
    return os.getenv("KAYLA_UPLOAD_DRIVE", "1").strip().lower() not in {"0", "false", "no"}


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
    print("No video render, optional Drive upload, Notion update, or publication will run.")
    print(f"Current time (Montreal): {now.strftime('%Y-%m-%d %H:%M')}")
    print(f"Target date: {target_date}")
    print(f"Ready source row(s): {len(rows)}")
    print(f"Local output dir: {_local_output_dir()}")
    print(f"Drive upload enabled: {_drive_upload_enabled()}")
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
        cards: list[KaylaCard] = []
        print("Kayla smart cards: 0 (disabled)")
        final_path = render_final_video(source_path, work_dir / _output_name(selected), work_dir, cards)
        local_final_path = _local_output_dir() / final_path.name
        shutil.copy2(final_path, local_final_path)
        print(f"Kayla final video ready locally: {local_final_path}")

        if _drive_upload_enabled():
            drive_url = upload_video_make_public(local_final_path, local_final_path.name)
            if not drive_url:
                raise RuntimeError("Google Drive upload returned an empty URL.")
            set_video_link(selected["id"], drive_url)
            print("Notion Lien Video filled from Drive. Statut remains A publier.")
            print(f"Drive URL: {drive_url}")
        else:
            print("Drive upload disabled. Notion Lien Video left unchanged; publisher will use local MP4.")
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
