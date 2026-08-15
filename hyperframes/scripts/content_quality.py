"""Reusable visual and script quality checks for HyperFrames candidates."""

from __future__ import annotations

import re
from typing import Any


VISUAL_RULES: dict[str, dict[str, tuple[str, ...]]] = {
    "oliviaa": {
        "vertical_format": ("vertical 9:16", "9:16 vertical"),
        "avatar": ("oliviaa",),
        "second_character_man": ("man", "male", "young man", "gentleman"),
        "two_characters": ("two characters", "two people", "both characters"),
        "oliviaa_left": ("oliviaa on the left", "oliviaa on left", "oliviaa at left", "oliviaa is on the left", "oliviaa stands on the left"),
        "man_right": ("man on the right", "man on right", "male on the right", "male to the right", "man is on the right", "male is on the right"),
    },
    "cindy": {
        "vertical_format": ("vertical 9:16", "9:16 vertical"),
        "avatar": ("cindy",),
        "microphones": ("microphones", "podcast microphone", "microphone"),
        "guest_man": ("guest man", "male guest", "man guest", "male guest speaker"),
    },
    "thefluentbuild": {
        "vertical_format": ("vertical 9:16", "9:16 vertical"),
        "grandma": ("grandma", "grandmother", "older woman"),
        "learner": ("learner", "young man", "man"),
        "learner_left": ("learner on the left", "man on the left", "learner on left", "man on left"),
        "grandma_right": ("grandma on the right", "grandmother on the right", "woman on the right", "grandma on right"),
    },
    "teacherryan": {
        "vertical_format": ("vertical 9:16", "9:16 vertical"),
        "ten_items": ("exactly 10", "10 items", "10 phrases"),
        "columns": ("2 columns", "two columns"),
        "rows": ("5 rows", "five rows"),
        "equal_cells": ("equal rectangular cells", "equal cells", "rectangular cells"),
        "visible_borders": ("visible borders", "visible cell borders"),
        "green_arrow": ("green arrow", "green pointer"),
    },
}

GENERIC_PHRASES = (
    "keep practicing",
    "this is important",
    "that is the natural way",
    "practice your english",
    "you can improve",
    "speak with confidence",
    "real life english",
)


def _has_any(text: str, values: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(value.lower() in lowered for value in values)


def visual_checks(avatar: str, content_format: str, prompt: str) -> dict[str, Any]:
    """Check stable composition rules without judging artistic quality."""
    if not prompt or content_format == "manual":
        return {"status": "WARN", "issues": ["visual_prompt_missing_or_unclassified"]}
    rules = VISUAL_RULES.get(avatar.lower())
    if not rules:
        return {"status": "WARN", "issues": ["no_visual_rule_defined_for_avatar"]}
    issues: list[str] = []
    lowered = prompt.lower()
    for rule_name, aliases in rules.items():
        if not _has_any(prompt, aliases):
            issues.append(f"missing_visual_rule:{rule_name}")
    if avatar.lower() == "oliviaa" and "woman" in lowered and "man" not in lowered and "male" not in lowered:
        issues.append("second_character_must_be_a_man")
    if avatar.lower() == "cindy" and not _has_any(prompt, ("man", "male", "guest man", "male guest")):
        issues.append("guest_must_be_a_man")
    if avatar.lower() == "thefluentbuild" and not _has_any(prompt, ("learner", "young man", "man")):
        issues.append("learner_must_be_a_man")
    return {"status": "BLOCK" if issues else "PASS", "issues": issues}


def _dialogue_lines(script: str) -> list[str]:
    return [line.strip() for line in script.splitlines() if re.match(r"^\s*[A-Za-z][A-Za-z ]+\s*:", line)]


def script_checks(avatar: str, content_format: str, script: str) -> dict[str, Any]:
    """Check naturalness signals and reject template leakage."""
    issues: list[str] = []
    warnings: list[str] = []
    if not script.strip():
        return {"status": "BLOCK", "issues": ["script_missing"], "warnings": []}

    if re.search(r"(?im)^\s*(hook|cta|style|rules)\s*:", script):
        issues.append("template_label_must_not_be_spoken")

    lines = _dialogue_lines(script)
    if avatar.lower() in {"oliviaa", "cindy", "thefluentbuild"} and len(lines) < 4:
        warnings.append("dialogue_may_be_too_short_or_unnatural")
    if avatar.lower() == "cindy":
        words = len(re.findall(r"\b\w+\b", script))
        if words < 150:
            warnings.append("podcast_script_may_be_under_60_seconds")
        if words > 300:
            warnings.append("podcast_script_may_exceed_90_seconds")

    normalized_lines = [re.sub(r"[^a-z0-9 ]", "", line.lower()).strip() for line in lines]
    repeated_lines = {line for line in normalized_lines if line and normalized_lines.count(line) > 1}
    if repeated_lines:
        issues.append("duplicate_dialogue_line")

    generic_hits = [phrase for phrase in GENERIC_PHRASES if phrase in script.lower()]
    if len(generic_hits) >= 2:
        warnings.append("generic_language_detected:" + ",".join(generic_hits))

    if avatar.lower() in {"oliviaa", "thefluentbuild"}:
        final_line = lines[-1].lower() if lines else ""
        if not any(token in final_line for token in ("saloo", "profile", "bio")):
            warnings.append("cta_not_detected_in_final_spoken_line")
    elif not any(token in script.lower() for token in ("saloo", "profile", "bio")):
        warnings.append("cta_not_detected")

    if avatar.lower() == "thefluentbuild" and "natural way" in script.lower() and not any(
        token in script.lower() for token in ("because", "irregular", "past tense", "grammar")
    ):
        warnings.append("grandma_explanation_may_be_too_vague")

    return {"status": "BLOCK" if issues else "PASS", "issues": issues, "warnings": warnings}


def quality_check(candidate: dict[str, Any]) -> dict[str, Any]:
    script_result = script_checks(candidate["avatar"], candidate["format"], candidate["script"])
    visual_result = visual_checks(candidate["avatar"], candidate["format"], candidate["prompt"])
    blocking = script_result["issues"] + visual_result["issues"]
    warnings = script_result["warnings"]
    return {
        "status": "BLOCK" if blocking else ("WARN" if warnings or visual_result["status"] == "WARN" else "PASS"),
        "script": script_result,
        "visual": visual_result,
        "blocking_reasons": blocking,
        "warnings": warnings + visual_result.get("issues", []) if visual_result["status"] == "WARN" else warnings,
    }
