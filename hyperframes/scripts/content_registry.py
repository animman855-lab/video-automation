"""Build a local, read-only anti-repetition registry from Notion.

The registry is intentionally metadata-first: it keeps fingerprints and compact
scenario fields instead of copying full scripts and prompts into a public repo.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import requests
from content_quality import quality_check


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE_ID = "909c1124-2b0c-48f3-938e-c0521b9d7bb2"
NOTION_VERSION = "2022-06-28"


# A format may repeat. The complete scenario, setting, hook, and wording still
# have to be new. These labels make that rule explicit across all avatars.
CONTENT_FAMILIES: dict[str, dict[str, tuple[str, ...]]] = {
    "oliviaa": {
        "conversation_daily": ("daily conversation", "real life dialogue", "everyday conversation"),
        "boundary_safety": ("boundary", "personal space", "safety", "stranger", "do not enter", "permission"),
        "work_social": ("workplace", "office", "coworker", "meeting", "project", "manager"),
        "texting_phone": ("texting", "text message", "voice note", "phone", "call", "message"),
        "dating_first_date": ("dating", "first date", "date", "restaurant date", "boyfriend"),
        "public_service": ("cafe", "coffee shop", "hotel", "airport", "pharmacy", "checkout", "parking"),
        "misunderstanding": ("misunderstanding", "wrong information", "confused", "mix-up", "misread"),
        "light_conflict": ("argument", "confrontation", "jealous", "gossip", "accusation", "fight"),
        "app_indirect": ("practice real", "practice conversations", "saloo english", "app"),
        "short_hook": ("hook", "relatable", "quick english"),
    },
    "cindy": {
        "podcast_story": ("podcast", "story", "yesterday", "happened to me"),
        "work_social": ("coworker", "office", "workplace", "lunch", "meeting"),
        "travel_public": ("airport", "hotel", "flight", "restaurant", "refund", "travel"),
        "awkward_misunderstanding": ("awkward", "misunderstood", "misunderstanding", "embarrassing"),
        "opinion_debate": ("i disagree", "disagree", "opinion", "debate", "honestly"),
        "listening_practice": ("listening practice", "conversation practice", "english listening"),
    },
    "teacherryan": {
        "visual_items": ("items", "vocabulary", "objects", "animals", "fruits", "parts", "tools"),
        "actions_commands": ("actions", "action phrases", "commands", "imperatives"),
        "state_affirmations": ("states", "feelings", "emotions", "affirmations", "i am", "i feel", "i need"),
        "grammar_contrast": ("today", "yesterday", "past", "present", "say", "tell", "hear", "listen", "take", "get"),
    },
    "thefluentbuild": {
        "grandma_correction": ("grandma", "grandmother", "corrects", "correction", "irregular verb", "natural way"),
        "real_life_grammar": ("grammar", "verb", "phrase", "mistake", "say this", "correct form"),
        "vocabulary_usage": ("vocabulary", "expression", "word choice", "natural english"),
    },
}

STOPWORDS = {
    "the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "with", "this",
    "that", "is", "are", "was", "were", "i", "you", "he", "she", "it", "we", "they",
    "my", "your", "his", "her", "our", "their", "just", "really", "like", "very",
}


def load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def prop_text(properties: dict[str, Any], name: str) -> str:
    prop = properties.get(name, {})
    prop_type = prop.get("type")
    if prop_type == "title":
        return "".join(item.get("plain_text", "") for item in prop.get("title", []))
    if prop_type == "rich_text":
        return "".join(item.get("plain_text", "") for item in prop.get("rich_text", []))
    if prop_type == "select":
        return (prop.get("select") or {}).get("name", "")
    if prop_type == "status":
        return (prop.get("status") or {}).get("name", "")
    if prop_type == "date":
        return (prop.get("date") or {}).get("start", "")
    if prop_type == "url":
        return prop.get("url") or ""
    return ""


def normalize(value: str) -> str:
    value = value.lower().replace("’", "'")
    value = re.sub(r"https?://\S+", " ", value)
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def digest(value: str) -> str:
    return hashlib.sha256(normalize(value).encode("utf-8")).hexdigest()[:16]


def terms(value: str) -> set[str]:
    return {word for word in normalize(value).split() if len(word) > 2 and word not in STOPWORDS}


def jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def slug(value: str) -> str:
    result = normalize(value).replace(" ", "-")
    return result[:100].strip("-") or "unknown"


def extract_dialogue(script: str) -> list[tuple[str, str]]:
    pattern = re.compile(
        r"(?im)^\s*(Oliviaa|Male|Man|Guest|Woman|Person)\s*:\s*[\"“]?(.+?)[\"”]?\s*$"
    )
    return [(speaker.lower(), text.strip()) for speaker, text in pattern.findall(script)]


def compact_script(script: str) -> str:
    # Remove reusable template instructions before creating similarity fields.
    script = re.sub(r"(?is)^\s*style:.*?(?=\n\s*(?:Oliviaa|Male|Man|Guest|Person)\s*:)", "", script)
    script = re.sub(r"(?is)^\s*rules:.*?(?=\n\s*(?:Oliviaa|Male|Man|Guest|Person)\s*:)", "", script)
    script = re.sub(r"(?im)^\s*CTA\s*:.*$", "", script)
    dialogue = extract_dialogue(script)
    if dialogue:
        return " ".join(f"{speaker} {line}" for speaker, line in dialogue)
    return script.strip()


def title_subject(title: str) -> str:
    """Use the title as a safe fallback when a row has no structured prompt."""
    value = re.sub(r"\s+-\s+\d{4}-\d{2}-\d{2}\s+-\s+\d{2}:\d{2}\s*$", "", title)
    parts = [part.strip() for part in value.split(" - ") if part.strip()]
    if parts and parts[0].lower() in {"oliviaa", "cindy", "teacherryan", "thefluentbuild", "kayla"}:
        parts = parts[1:]
    return " - ".join(parts)


def extract_subjects(title: str, script: str, prompt: str) -> tuple[str, str]:
    """Extract a scenario and setting across the four different content styles."""
    combined = f"{prompt}\n{script}"
    scenario = ""
    setting = ""
    for label in ("Scenario type", "Scene", "Topic", "Subject", "Category"):
        match = re.search(rf"(?i){re.escape(label)}\s*:\s*([^\.\n]+)", combined)
        if match:
            scenario = match.group(1).strip()
            break
    setting_match = re.search(r"(?i)(?:Setting|Location)\s*:\s*([^\.\n]+)", combined)
    if setting_match:
        setting = setting_match.group(1).strip()

    # TeacherRyan uses a fixed grid and puts its content in one ordered list.
    phrase_match = re.search(r"(?is)Exact phrases in order:\s*(.+?)(?:\.|$)", prompt)
    if phrase_match and not scenario:
        scenario = "phrases: " + phrase_match.group(1).strip()

    # Manual rows usually carry their subject in the title or in a Scene line.
    if not scenario:
        scenario = title_subject(title)
    if not setting:
        setting = title_subject(title)
    return scenario, setting


def infer_format(avatar: str, title: str, script: str, prompt: str) -> str:
    """Return a broad content family; it is not a duplicate key."""
    combined = normalize(f"{title} {script} {prompt}")
    explicit = re.search(r"(?i)(?:format|content family|family)\s*:\s*([^\.\n]+)", f"{title}\n{script}\n{prompt}")
    if explicit:
        return slug(explicit.group(1))
    families = CONTENT_FAMILIES.get(avatar.lower(), {})
    scores = {
        family: sum(1 for marker in markers if normalize(marker) in combined)
        for family, markers in families.items()
    }
    best = max(scores, key=scores.get, default="unknown")
    return best if scores.get(best, 0) else "unknown"


def fetch_rows(token: str, database_id: str, avatar: str, start: str, end: str) -> list[dict[str, Any]]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    body: dict[str, Any] = {
        "page_size": 100,
        "filter": {
            "and": [
                {"property": "Avatar", "select": {"equals": avatar}},
                {"property": "Date Publication", "date": {"on_or_after": start}},
                {"property": "Date Publication", "date": {"on_or_before": end}},
            ]
        },
    }
    rows: list[dict[str, Any]] = []
    cursor = None
    while True:
        if cursor:
            body["start_cursor"] = cursor
        response = requests.post(
            f"https://api.notion.com/v1/databases/{database_id}/query",
            headers=headers,
            json=body,
            timeout=45,
        )
        response.raise_for_status()
        data = response.json()
        rows.extend(data.get("results", []))
        if not data.get("has_more"):
            return rows
        cursor = data.get("next_cursor")


def build_registry(rows: list[dict[str, Any]], avatar: str, start: str, end: str) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    scenario_groups: defaultdict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    exact_groups: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in rows:
        properties = row.get("properties", {})
        title = prop_text(properties, "Titre")
        script = prop_text(properties, "Script")
        prompt = prop_text(properties, "Prompt 1")
        scenario, setting = extract_subjects(title, script, prompt)
        dialogue = extract_dialogue(script)
        hook = dialogue[0][1] if dialogue else ""
        compact = compact_script(script)
        scenario_key = f"{normalize(scenario)}|{normalize(setting)}"
        content_format = infer_format(avatar, title, script, prompt)
        entry = {
            "page_id": row.get("id", ""),
            "avatar": prop_text(properties, "Avatar") or avatar,
            "date": prop_text(properties, "Date Publication")[:10],
            "slot": prop_text(properties, "Slot"),
            "title": title,
            "video_type": prop_text(properties, "Video Type"),
            "status": prop_text(properties, "Statut"),
            "used": bool(prop_text(properties, "Lien Video") or prop_text(properties, "Statut") in {"Publie", "A publier"}),
            "scenario": scenario,
            "setting": setting,
            "format": content_format,
            "scenario_key": scenario_key,
            "hook": hook,
            "script_terms": sorted(terms(compact)),
            "prompt_terms": sorted(terms(prompt)),
            "script_fingerprint": digest(compact),
            "prompt_fingerprint": digest(prompt),
            "content_fingerprint": digest(f"{title}\n{compact}\n{prompt}"),
        }
        entries.append(entry)
        exact_groups[entry["content_fingerprint"]].append(entry)
        if scenario_key != "|":
            scenario_groups[(normalize(scenario), normalize(setting))].append(entry)

    for entry in entries:
        group = scenario_groups.get((normalize(entry["scenario"]), normalize(entry["setting"])), [])
        if entry["scenario_key"] == "|":
            entry["quality"] = "needs_improvement"
            entry["duplicate_scenario_rows"] = []
        elif len(group) > 1:
            entry["quality"] = "repetition_risk"
            entry["duplicate_scenario_rows"] = [
                {"date": item["date"], "slot": item["slot"], "page_id": item["page_id"]}
                for item in group
                if item["page_id"] != entry["page_id"]
            ]
        else:
            entry["quality"] = "keep"
            entry["duplicate_scenario_rows"] = []
        if not entry["used"]:
            entry["usage_note"] = "not_confirmed_published"

    exact_duplicates = sum(1 for group in exact_groups.values() if len(group) > 1)
    repeated_scenarios = sum(1 for group in scenario_groups.values() if len(group) > 1)
    return {
        "schema_version": 1,
        "generated_at": date.today().isoformat(),
        "source": "Notion Video Publishing",
        "avatar": avatar,
        "range": {"from": start, "to": end},
        "policy": {
            "history_is_kept": True,
            "repetition_risk_is_blocked_for_future_batches": True,
            "no_notion_writes": True,
        },
        "summary": {
            "rows": len(entries),
            "repeated_scenario_groups": repeated_scenarios,
            "exact_content_duplicate_groups": exact_duplicates,
            "quality_counts": {
                "keep": sum(item["quality"] == "keep" for item in entries),
                "repetition_risk": sum(item["quality"] == "repetition_risk" for item in entries),
                "needs_improvement": sum(item["quality"] == "needs_improvement" for item in entries),
            },
            "format_counts": {
                family: sum(item["format"] == family for item in entries)
                for family in sorted({item["format"] for item in entries})
            },
        },
        "entries": entries,
    }


def load_local_registry(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def reclassify_local_registry(path: Path) -> dict[str, Any]:
    registry = load_local_registry(path)
    for entry in registry.get("entries", []):
        entry.setdefault("format", infer_format(
            registry.get("avatar", entry.get("avatar", "")),
            entry.get("title", ""),
            entry.get("script", ""),
            entry.get("prompt", ""),
        ))
        entry.setdefault("script_terms", [])
        entry.setdefault("prompt_terms", [])
    format_counts: defaultdict[str, int] = defaultdict(int)
    for entry in registry.get("entries", []):
        format_counts[entry.get("format", "unknown")] += 1
    registry.setdefault("summary", {})["format_counts"] = dict(sorted(format_counts.items()))
    registry.setdefault("schema_version", 1)
    registry["schema_version"] = max(2, int(registry["schema_version"]))
    path.write_text(json.dumps(registry, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return registry


def candidate_entry(candidate: dict[str, Any]) -> dict[str, Any]:
    avatar = str(candidate.get("avatar", "")).strip().lower()
    title = str(candidate.get("title", ""))
    script = str(candidate.get("script", ""))
    prompt = str(candidate.get("prompt", candidate.get("prompt_1", "")))
    scenario = str(candidate.get("scenario", "")).strip()
    setting = str(candidate.get("setting", "")).strip()
    if not scenario or not setting:
        inferred_scenario, inferred_setting = extract_subjects(title, script, prompt)
        scenario = scenario or inferred_scenario
        setting = setting or inferred_setting
    dialogue = extract_dialogue(script)
    compact = compact_script(script)
    return {
        "avatar": avatar,
        "title": title,
        "script": script,
        "prompt": prompt,
        "format": str(candidate.get("format", "")).strip() or infer_format(avatar, title, script, prompt),
        "scenario": scenario,
        "setting": setting,
        "scenario_key": f"{normalize(scenario)}|{normalize(setting)}",
        "hook": dialogue[0][1] if dialogue else str(candidate.get("hook", "")),
        "script_terms": sorted(terms(compact)),
        "prompt_terms": sorted(terms(prompt)),
        "content_fingerprint": digest(f"{title}\n{compact}\n{prompt}"),
        "prompt_fingerprint": digest(prompt),
    }


def preflight(candidate_path: Path, registry_path: Path) -> int:
    registry = load_local_registry(registry_path)
    raw = json.loads(candidate_path.read_text(encoding="utf-8"))
    candidates = raw if isinstance(raw, list) else raw.get("videos", [raw])
    results: list[dict[str, Any]] = []
    history = registry.get("entries", [])
    for index, raw_candidate in enumerate(candidates, start=1):
        candidate = candidate_entry(raw_candidate)
        reasons: list[str] = []
        quality = quality_check(candidate)
        if candidate["avatar"] != str(registry.get("avatar", "")).lower():
            reasons.append("avatar_not_matching_registry")
        if not candidate["title"] or not candidate["script"]:
            reasons.append("title_or_script_missing")
        reasons.extend(quality["blocking_reasons"])
        for entry in history:
            if candidate["content_fingerprint"] == entry.get("content_fingerprint"):
                reasons.append("exact_content_duplicate")
                break
            if candidate["scenario_key"] != "|" and candidate["scenario_key"] == entry.get("scenario_key"):
                reasons.append(f"scenario_setting_already_used:{entry.get('date')}:{entry.get('slot')}")
                break
            if candidate["prompt"] and candidate["prompt_fingerprint"] == entry.get("prompt_fingerprint"):
                reasons.append("exact_prompt_duplicate")
                break
            old_hook = normalize(entry.get("hook", ""))
            if candidate["hook"] and old_hook and normalize(candidate["hook"]) == old_hook:
                reasons.append(f"hook_already_used:{entry.get('date')}:{entry.get('slot')}")
                break
            old_terms = set(entry.get("script_terms", []))
            if jaccard(set(candidate["script_terms"]), old_terms) >= 0.72:
                reasons.append(f"script_too_similar:{entry.get('date')}:{entry.get('slot')}")
                break
        results.append({
            "index": index,
            "format": candidate["format"],
            "status": "BLOCK" if reasons else quality["status"],
            "reasons": reasons,
            "warnings": quality["warnings"],
        })
    passed = sum(item["status"] == "PASS" for item in results)
    print(json.dumps({"registry": str(registry_path), "candidates": len(results), "passed": passed, "blocked": len(results) - passed, "results": results}, indent=2))
    return 1 if any(item["status"] == "BLOCK" for item in results) else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a local anti-repetition registry from Notion.")
    parser.add_argument("--avatar")
    parser.add_argument("--from", dest="start")
    parser.add_argument("--to", dest="end")
    parser.add_argument("--write", action="store_true", help="Write the local registry JSON.")
    parser.add_argument("--reclassify-local", help="Refresh format labels in an existing local registry JSON.")
    parser.add_argument("--check-candidate", help="Check a candidate JSON against a local registry.")
    parser.add_argument("--registry", help="Local registry JSON used by --check-candidate.")
    args = parser.parse_args()
    if args.reclassify_local:
        registry = reclassify_local_registry(Path(args.reclassify_local))
        print(json.dumps(registry.get("summary", {}), indent=2))
        return 0
    if args.check_candidate:
        if not args.registry:
            parser.error("--registry is required with --check-candidate")
        return preflight(Path(args.check_candidate), Path(args.registry))
    if not args.avatar or not args.start or not args.end:
        parser.error("--avatar, --from and --to are required unless using a local operation")
    load_env()
    token = os.getenv("NOTION_TOKEN")
    if not token:
        print("NOTION_TOKEN is missing.", file=sys.stderr)
        return 2
    rows = fetch_rows(token, os.getenv("NOTION_DATABASE_ID", DEFAULT_DATABASE_ID), args.avatar, args.start, args.end)
    registry = build_registry(rows, args.avatar, args.start, args.end)
    print(json.dumps(registry["summary"], indent=2))
    if args.write:
        output = ROOT / "content_registry" / f"{slug(args.avatar)}.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(registry, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
