from __future__ import annotations

import argparse
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from notion_client import get_database_id, load_local_env, prop_text


NOTION_VERSION = "2022-06-28"
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
NORMAL_VIDEO_AVATARS = {"oliviaa", "oliviaaa", "cindy", "teacherryan", "thefluentbuild"}
NORMAL_VIDEO_SLOTS = ["08:00", "16:00"]
KAYLA_AD_SLOTS = ["08:00", "10:00", "12:00", "14:00", "16:00", "18:00", "20:00", "22:00", "00:00", "02:00"]


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def toronto_now() -> datetime:
    try:
        return datetime.now(ZoneInfo("America/Toronto"))
    except Exception:
        return datetime.now(timezone(timedelta(hours=-4)))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only watchdog for Saloo publishing rows.")
    parser.add_argument("--date", help="YYYY-MM-DD. Defaults to today in Montreal.")
    parser.add_argument("--lookback-runs", type=int, default=5, help="GitHub Actions runs to summarize per workflow.")
    parser.add_argument("--output", help="Optional markdown report path.")
    parser.add_argument("--telegram-output", help="Optional short Telegram message path.")
    return parser.parse_args()


def notion_headers() -> dict[str, str]:
    token = os.getenv("NOTION_TOKEN")
    if not token:
        raise RuntimeError("Missing NOTION_TOKEN.")
    return {
        "Authorization": f"Bearer {token.strip()}",
        "Notion-Version": os.getenv("NOTION_VERSION", NOTION_VERSION),
        "Content-Type": "application/json",
    }


def prop_multi_select(props: dict, name: str) -> list[str]:
    prop = props.get(name)
    if not prop or prop.get("type") != "multi_select":
        return []
    return [item.get("name", "") for item in prop.get("multi_select", []) if item.get("name")]


def query_rows_for_date(publication_date: str) -> list[dict]:
    database_id = get_database_id()
    url = f"https://api.notion.com/v1/databases/{database_id}/query"
    payload = {
        "filter": {"property": "Date Publication", "date": {"equals": publication_date}},
        "sorts": [
            {"property": "Slot", "direction": "ascending"},
            {"property": "Avatar", "direction": "ascending"},
        ],
        "page_size": 100,
    }

    rows: list[dict] = []
    while True:
        response = requests.post(url, headers=notion_headers(), json=payload, timeout=30)
        if response.status_code >= 400:
            raise RuntimeError(f"Notion query failed: {response.status_code} {response.text}")
        data = response.json()
        rows.extend(data.get("results", []))
        if not data.get("has_more"):
            return rows
        payload["start_cursor"] = data.get("next_cursor")


def query_image_rows_for_date(publication_date: str) -> list[dict]:
    database_id = os.getenv("NOTION_IMAGE_DATABASE_ID", "").strip()
    if not database_id:
        return []

    url = f"https://api.notion.com/v1/databases/{database_id}/query"
    payload = {
        "filter": {"property": "Date Publication", "date": {"equals": publication_date}},
        "sorts": [
            {"property": "Slot", "direction": "ascending"},
            {"property": "Avatar", "direction": "ascending"},
        ],
        "page_size": 100,
    }

    rows: list[dict] = []
    while True:
        response = requests.post(url, headers=notion_headers(), json=payload, timeout=30)
        if response.status_code >= 400:
            raise RuntimeError(f"Notion image query failed: {response.status_code} {response.text}")
        data = response.json()
        rows.extend(data.get("results", []))
        if not data.get("has_more"):
            return rows
        payload["start_cursor"] = data.get("next_cursor")


def slot_has_started(publication_date: str, slot: str, now: datetime) -> bool:
    slot_minutes = SLOT_HOURS.get(slot)
    if slot_minutes is None:
        return False

    today = now.strftime("%Y-%m-%d")
    if publication_date < today:
        return True
    if publication_date > today:
        return False

    current_minutes = now.hour * 60 + now.minute
    return current_minutes >= slot_minutes


def next_due_slot(slots: list[str], now: datetime, target_date: str) -> str:
    today = now.strftime("%Y-%m-%d")
    if target_date < today:
        return "past date"
    if target_date > today:
        return slots[0] if slots else "none"

    current_minutes = now.hour * 60 + now.minute
    for slot in slots:
        slot_minutes = SLOT_HOURS.get(slot)
        if slot_minutes is not None and current_minutes < slot_minutes:
            return slot
    return "done today"


def is_normal_hyperframes_video(item: dict) -> bool:
    return (
        item["avatar"] in NORMAL_VIDEO_AVATARS
        and item["video_type"] == "HyperFrames"
        and item["slot"] in NORMAL_VIDEO_SLOTS
    )


def is_kayla_ad(item: dict) -> bool:
    return item["avatar"] == "kayla"


def bucket_counts(items: list[dict]) -> dict[str, int]:
    return {
        "total": len(items),
        "published": sum(1 for item in items if item["status"] == "Publie"),
        "ready": sum(1 for item in items if not item["issues"] and item["status"] == "A publier"),
        "blocked": sum(1 for item in items if item["issues"]),
        "waiting": sum(1 for item in items if not item["issues"] and item["status"] not in {"A publier", "Publie"}),
    }


def urgent_items(items: list[dict]) -> list[dict]:
    return [item for item in items if item["issues"] and item["due"]]


def preparation_items(items: list[dict]) -> list[dict]:
    return [item for item in items if item["issues"] and not item["due"]]


def short_issue_lines(prefix: str, items: list[dict], limit: int) -> list[str]:
    lines: list[str] = []
    for item in [entry for entry in items if entry["issues"]][:limit]:
        first_issue = item["issues"][0] if item["issues"] else "Probleme inconnu."
        lines.append(f"- {prefix} {item['slot']} {item['avatar']} ({item['status']}): {first_issue}")
    return lines


def row_summary(row: dict, now: datetime) -> dict:
    props = row.get("properties", {})
    title = prop_text(props, "Titre") or "(no title)"
    avatar = prop_text(props, "Avatar").lower()
    video_type = prop_text(props, "Video Type")
    status = prop_text(props, "Statut")
    publication_date = prop_text(props, "Date Publication")
    slot = prop_text(props, "Slot")
    lien_video = prop_text(props, "Lien Video")
    image_hyperframes = prop_text(props, "Image HyperFrames")
    prompt_1 = prop_text(props, "Prompt 1")
    script = prop_text(props, "Script")
    platforms = prop_multi_select(props, "Plateforme")
    due = slot_has_started(publication_date, slot, now)

    issues: list[str] = []
    notes: list[str] = []

    if status == "Publie":
        if not lien_video and avatar != "kayla":
            issues.append("Statut Publie mais Lien Video vide.")
        else:
            notes.append("Publie.")
    elif status == "A publier":
        if not lien_video and due:
            issues.append("A publier mais Lien Video vide apres le debut du slot: publication impossible.")
        elif not lien_video:
            notes.append("A publier avec Lien Video vide, mais slot pas encore arrive.")
        if not platforms:
            issues.append("A publier mais Plateforme vide: aucun réseau ne sera ciblé.")
        if due and lien_video and platforms:
            issues.append("A publier avec video et plateformes apres le debut du slot: verifier si le workflow de publication a tourne.")
        if not due:
            notes.append("Pret mais slot pas encore arrive.")
    elif status == "En cours":
        if video_type == "HyperFrames":
            if due and image_hyperframes and not lien_video:
                issues.append("HyperFrames due mais Lien Video vide: generation probablement bloquee ou pas encore lancee.")
            elif image_hyperframes and not lien_video:
                notes.append("HyperFrames pret a generer quand le slot arrive.")
            elif not image_hyperframes:
                issues.append("HyperFrames sans Image HyperFrames: generation impossible.")
        elif avatar == "kayla":
            if due and image_hyperframes and not lien_video:
                issues.append("Kayla source video presente mais Lien Video vide: post-process Kayla a verifier.")
            elif not image_hyperframes:
                notes.append("Kayla attend une video Flow dans Image HyperFrames.")
        else:
            if due:
                issues.append("En cours apres le debut du slot: verifier preparation ou workflow.")
    else:
        if due:
            issues.append(f"Statut inhabituel pour un slot deja commence: {status or '(vide)'}.")

    if video_type == "HyperFrames" and avatar == "teacherryan" and prompt_1 and "2 columns" not in prompt_1.lower():
        notes.append("TeacherRyan prompt peut manquer la regle 2 columns / 5 rows.")
    if video_type == "HyperFrames" and not script:
        issues.append("Script vide pour HyperFrames.")

    return {
        "id": row.get("id", ""),
        "title": title,
        "avatar": avatar,
        "video_type": video_type,
        "status": status,
        "date": publication_date,
        "slot": slot,
        "platforms": platforms,
        "due": due,
        "has_video": bool(lien_video),
        "has_image_hyperframes": bool(image_hyperframes),
        "issues": issues,
        "notes": notes,
    }


def image_row_summary(row: dict, now: datetime) -> dict:
    props = row.get("properties", {})
    title = prop_text(props, "Titre") or prop_text(props, "Name") or "(image quiz)"
    avatar = prop_text(props, "Avatar").lower()
    status = prop_text(props, "Statut")
    publication_date = prop_text(props, "Date Publication")
    slot = prop_text(props, "Slot")
    caption = prop_text(props, "Caption")
    image_file = prop_text(props, "Image File")
    prompt_image = prop_text(props, "Prompt Image") or prop_text(props, "Prompt 1")
    platforms = prop_multi_select(props, "Plateforme")
    due = slot_has_started(publication_date, slot, now)

    issues: list[str] = []
    notes: list[str] = []

    if status == "Publie":
        notes.append("Image quiz publie.")
    elif status == "A publier":
        if due and not image_file:
            issues.append("Image Quiz A publier mais Image File vide apres le debut du slot.")
        elif not image_file:
            notes.append("Image Quiz pret dans Notion mais attend encore Image File.")
        if not caption:
            issues.append("Image Quiz sans Caption: publication faible ou impossible.")
        if not platforms:
            issues.append("Image Quiz sans Plateforme: aucun reseau ne sera cible.")
        if due and image_file and caption and platforms:
            issues.append("Image Quiz A publier apres le debut du slot: verifier si publish_images.py a tourne.")
        if not due:
            notes.append("Slot pas encore arrive.")
    elif status == "En cours":
        if due:
            if not prompt_image and not image_file:
                issues.append("Image Quiz En cours apres le slot, sans Prompt Image ni Image File.")
            elif not image_file:
                issues.append("Image Quiz En cours apres le slot, Image File vide.")
            else:
                issues.append("Image Quiz En cours apres le slot avec Image File: verifier statut.")
        else:
            notes.append("Image Quiz en preparation.")
    else:
        if due:
            issues.append(f"Statut image inhabituel apres le debut du slot: {status or '(vide)'}.")

    return {
        "id": row.get("id", ""),
        "title": title,
        "avatar": avatar,
        "status": status,
        "date": publication_date,
        "slot": slot,
        "platforms": platforms,
        "due": due,
        "has_image": bool(image_file),
        "has_caption": bool(caption),
        "has_prompt": bool(prompt_image),
        "issues": issues,
        "notes": notes,
    }


def github_headers() -> dict[str, str] | None:
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if not token:
        return None
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def github_runs(workflow_file: str, limit: int) -> list[dict]:
    headers = github_headers()
    repository = os.getenv("GITHUB_REPOSITORY", "animman855-lab/video-automation")
    if not headers or not repository:
        return []
    url = f"https://api.github.com/repos/{repository}/actions/workflows/{workflow_file}/runs"
    response = requests.get(url, headers=headers, params={"per_page": limit}, timeout=30)
    if response.status_code >= 400:
        return [{"error": f"GitHub API failed for {workflow_file}: {response.status_code} {response.text[:200]}"}]
    return response.json().get("workflow_runs", [])


def render_report(rows: list[dict], image_rows: list[dict], now: datetime, target_date: str, lookback_runs: int) -> str:
    summaries = [row_summary(row, now) for row in rows]
    issue_rows = [item for item in summaries if item["issues"]]
    image_summaries = [image_row_summary(row, now) for row in image_rows]
    image_issue_rows = [item for item in image_summaries if item["issues"]]
    normal_video_rows = [item for item in summaries if is_normal_hyperframes_video(item)]
    kayla_rows = [item for item in summaries if is_kayla_ad(item)]
    other_video_rows = [item for item in summaries if item not in normal_video_rows and item not in kayla_rows]
    normal_counts = bucket_counts(normal_video_rows)
    kayla_counts = bucket_counts(kayla_rows)
    image_counts = bucket_counts(image_summaries)
    urgent_video_rows = urgent_items(normal_video_rows)
    urgent_image_rows = urgent_items(image_summaries)
    urgent_kayla_rows = urgent_items(kayla_rows)
    urgent_other_rows = urgent_items(other_video_rows)
    prep_video_rows = preparation_items(normal_video_rows)
    prep_image_rows = preparation_items(image_summaries)
    prep_kayla_rows = preparation_items(kayla_rows)
    prep_other_rows = preparation_items(other_video_rows)
    urgent_total = len(urgent_video_rows) + len(urgent_image_rows) + len(urgent_kayla_rows) + len(urgent_other_rows)
    prep_total = len(prep_video_rows) + len(prep_image_rows) + len(prep_kayla_rows) + len(prep_other_rows)

    lines: list[str] = []
    lines.append("# Saloo Publishing Watchdog")
    lines.append("")
    lines.append(f"- Date checked: {target_date}")
    lines.append(f"- Current Montreal time: {now.strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"- Global issues: {len(issue_rows) + len(image_issue_rows)}")
    lines.append(f"- Urgent issues: {urgent_total}")
    lines.append(f"- Preparation issues: {prep_total}")
    lines.append(f"- Video rows found: {len(rows)}")
    lines.append(f"- Image Quiz rows found: {len(image_rows)}")
    lines.append("")

    lines.append("## Operational Summary")
    lines.append("")
    lines.append("### HyperFrames Videos")
    lines.append(f"- Expected schedule: 4 avatars x 2 slots ({', '.join(NORMAL_VIDEO_SLOTS)}) = 8 videos/day")
    lines.append(f"- Rows found today: {normal_counts['total']}")
    lines.append(f"- Published: {normal_counts['published']}")
    lines.append(f"- Ready: {normal_counts['ready']}")
    lines.append(f"- Waiting / in preparation: {normal_counts['waiting']}")
    lines.append(f"- Blocked / needs attention: {normal_counts['blocked']}")
    lines.append(f"- Next due slot: {next_due_slot(NORMAL_VIDEO_SLOTS, now, target_date)}")
    lines.append("")
    lines.append("### Image Quiz")
    lines.append("- Expected schedule: usually once per day, normally around the Image Quiz slot.")
    lines.append(f"- Rows found today: {image_counts['total']}")
    lines.append(f"- Published: {image_counts['published']}")
    lines.append(f"- Ready: {image_counts['ready']}")
    lines.append(f"- Waiting / in preparation: {image_counts['waiting']}")
    lines.append(f"- Blocked / needs attention: {image_counts['blocked']}")
    lines.append("")
    lines.append("### Kayla Ads")
    lines.append(f"- Expected schedule: up to 10 slots/day ({', '.join(KAYLA_AD_SLOTS)})")
    lines.append(f"- Rows found today: {kayla_counts['total']}")
    lines.append(f"- Published: {kayla_counts['published']}")
    lines.append(f"- Ready: {kayla_counts['ready']}")
    lines.append(f"- Waiting / in preparation: {kayla_counts['waiting']}")
    lines.append(f"- Blocked / needs attention: {kayla_counts['blocked']}")
    lines.append(f"- Next due slot: {next_due_slot(KAYLA_AD_SLOTS, now, target_date)}")
    lines.append("")

    lines.append("## Alert Levels")
    if urgent_total:
        lines.append("### Urgent - action needed now")
        for line in short_issue_lines("Video", urgent_video_rows, 20):
            lines.append(line)
        for line in short_issue_lines("Quiz", urgent_image_rows, 20):
            lines.append(line)
        for line in short_issue_lines("Kayla", urgent_kayla_rows, 20):
            lines.append(line)
        for line in short_issue_lines("Other", urgent_other_rows, 20):
            lines.append(line)
        lines.append("")
    else:
        lines.append("### Urgent - action needed now")
        lines.append("- No urgent issue detected.")
        lines.append("")

    if prep_total:
        lines.append("### Preparation - fix before the slot")
        for line in short_issue_lines("Video", prep_video_rows, 20):
            lines.append(line)
        for line in short_issue_lines("Quiz", prep_image_rows, 20):
            lines.append(line)
        for line in short_issue_lines("Kayla", prep_kayla_rows, 20):
            lines.append(line)
        for line in short_issue_lines("Other", prep_other_rows, 20):
            lines.append(line)
        lines.append("")
    else:
        lines.append("### Preparation - fix before the slot")
        lines.append("- No preparation issue detected.")
        lines.append("")

    if issue_rows or image_issue_rows:
        lines.append("## Issues")
    if issue_rows:
        lines.append("### Video / HyperFrames / Kayla")
        for item in issue_rows:
            lines.append(f"- [{item['slot']}] {item['avatar']} | {item['video_type']} | {item['status']} | {item['title']}")
            for issue in item["issues"]:
                lines.append(f"  - ISSUE: {issue}")
            for note in item["notes"]:
                lines.append(f"  - note: {note}")
        lines.append("")
    if image_issue_rows:
        lines.append("### Image Quiz")
        for item in image_issue_rows:
            lines.append(f"- [{item['slot']}] {item['avatar']} | {item['status']} | {item['title']}")
            for issue in item["issues"]:
                lines.append(f"  - ISSUE: {issue}")
            for note in item["notes"]:
                lines.append(f"  - note: {note}")
        lines.append("")
    if not issue_rows and not image_issue_rows:
        lines.append("## Issues")
        lines.append("- No issues detected.")
        lines.append("")

    lines.append("## HyperFrames Video Rows")
    for item in normal_video_rows:
        platforms = ", ".join(item["platforms"]) if item["platforms"] else "(none)"
        flags = []
        if item["due"]:
            flags.append("due")
        if item["has_video"]:
            flags.append("video")
        if item["has_image_hyperframes"]:
            flags.append("image/video source")
        flag_text = ", ".join(flags) if flags else "not due/no media"
        lines.append(
            f"- [{item['slot']}] {item['avatar']} | {item['video_type']} | {item['status']} | "
            f"platforms: {platforms} | {flag_text}"
        )
    lines.append("")

    lines.append("## Kayla Ads Rows")
    for item in kayla_rows:
        platforms = ", ".join(item["platforms"]) if item["platforms"] else "(none)"
        flags = []
        if item["due"]:
            flags.append("due")
        if item["has_video"]:
            flags.append("video")
        if item["has_image_hyperframes"]:
            flags.append("source")
        flag_text = ", ".join(flags) if flags else "not due/no media"
        lines.append(
            f"- [{item['slot']}] {item['avatar']} | {item['video_type']} | {item['status']} | "
            f"platforms: {platforms} | {flag_text}"
        )
    if other_video_rows:
        lines.append("")
        lines.append("## Other Video Rows")
        for item in other_video_rows:
            platforms = ", ".join(item["platforms"]) if item["platforms"] else "(none)"
            lines.append(f"- [{item['slot']}] {item['avatar']} | {item['video_type']} | {item['status']} | platforms: {platforms}")
    lines.append("")

    lines.append("## Image Quiz Rows")
    if not image_summaries and not os.getenv("NOTION_IMAGE_DATABASE_ID", "").strip():
        lines.append("- Image Quiz database unavailable. Set NOTION_IMAGE_DATABASE_ID to enable it.")
    elif not image_summaries:
        lines.append("- No Image Quiz rows found for this date.")
    for item in image_summaries:
        platforms = ", ".join(item["platforms"]) if item["platforms"] else "(none)"
        flags = []
        if item["due"]:
            flags.append("due")
        if item["has_image"]:
            flags.append("image")
        if item["has_caption"]:
            flags.append("caption")
        if item["has_prompt"]:
            flags.append("prompt")
        flag_text = ", ".join(flags) if flags else "not due/no media"
        lines.append(f"- [{item['slot']}] {item['avatar']} | {item['status']} | platforms: {platforms} | {flag_text}")
    lines.append("")

    lines.append("## Recent GitHub Runs")
    for workflow in ["publish.yml", "publish_kayla_ads.yml"]:
        lines.append(f"### {workflow}")
        runs = github_runs(workflow, lookback_runs)
        if not runs:
            lines.append("- GitHub run data unavailable. Set GITHUB_TOKEN/GH_TOKEN to enable it.")
            continue
        for run in runs:
            if "error" in run:
                lines.append(f"- {run['error']}")
                continue
            lines.append(
                f"- {run.get('created_at')} | {run.get('conclusion') or run.get('status')} | "
                f"{run.get('html_url')}"
            )
    lines.append("")

    lines.append("## Next Safe Actions")
    if issue_rows:
        lines.append("- Review the issue rows above before rerunning workflows.")
        lines.append("- Fix Notion fields first when the issue is missing platform, missing link, missing script, or missing Image HyperFrames.")
        lines.append("- If HyperFrames failed with media present, inspect the latest workflow logs before changing content.")
    else:
        lines.append("- No urgent action detected.")
    lines.append("")
    lines.append("_Read-only report. No Notion, Drive, Upload-Post, or GitHub state was modified._")
    return "\n".join(lines)


def render_telegram_message(rows: list[dict], image_rows: list[dict], now: datetime, target_date: str) -> str:
    summaries = [row_summary(row, now) for row in rows]
    issue_rows = [item for item in summaries if item["issues"]]
    image_summaries = [image_row_summary(row, now) for row in image_rows]
    image_issue_rows = [item for item in image_summaries if item["issues"]]
    normal_video_rows = [item for item in summaries if is_normal_hyperframes_video(item)]
    kayla_rows = [item for item in summaries if is_kayla_ad(item)]
    other_video_rows = [item for item in summaries if item not in normal_video_rows and item not in kayla_rows]
    normal_counts = bucket_counts(normal_video_rows)
    kayla_counts = bucket_counts(kayla_rows)
    image_counts = bucket_counts(image_summaries)
    urgent_video_rows = urgent_items(normal_video_rows)
    urgent_image_rows = urgent_items(image_summaries)
    urgent_kayla_rows = urgent_items(kayla_rows)
    urgent_other_rows = urgent_items(other_video_rows)
    prep_video_rows = preparation_items(normal_video_rows)
    prep_image_rows = preparation_items(image_summaries)
    prep_kayla_rows = preparation_items(kayla_rows)
    prep_other_rows = preparation_items(other_video_rows)
    urgent_total = len(urgent_video_rows) + len(urgent_image_rows) + len(urgent_kayla_rows) + len(urgent_other_rows)
    prep_total = len(prep_video_rows) + len(prep_image_rows) + len(prep_kayla_rows) + len(prep_other_rows)
    run_url = os.getenv("GITHUB_SERVER_URL", "https://github.com")
    repository = os.getenv("GITHUB_REPOSITORY", "animman855-lab/video-automation")
    run_id = os.getenv("GITHUB_RUN_ID", "")
    github_run_url = f"{run_url}/{repository}/actions/runs/{run_id}" if run_id else ""

    total_issues = len(issue_rows) + len(image_issue_rows)
    if urgent_total:
        status_line = f"Saloo Watchdog - URGENT ({urgent_total})"
    elif prep_total:
        status_line = f"Saloo Watchdog - PREPARATION ({prep_total})"
    else:
        status_line = "Saloo Watchdog - OK"

    lines = [
        status_line,
        f"Date: {target_date}",
        f"Heure Montreal: {now.strftime('%H:%M')}",
        "",
        "VIDEOS HYPERFRAMES",
        f"Expected: 8/day ({', '.join(NORMAL_VIDEO_SLOTS)})",
        f"Published: {normal_counts['published']}/{normal_counts['total']}",
        f"Ready: {normal_counts['ready']} | Waiting: {normal_counts['waiting']} | Blocked: {normal_counts['blocked']}",
        f"Next due: {next_due_slot(NORMAL_VIDEO_SLOTS, now, target_date)}",
        "",
        "IMAGE QUIZ",
        "Expected: daily quiz rows found in Notion",
        f"Published: {image_counts['published']}/{image_counts['total']}",
        f"Ready: {image_counts['ready']} | Waiting: {image_counts['waiting']} | Blocked: {image_counts['blocked']}",
        "",
        "KAYLA ADS",
        "Expected: up to 10/day",
        f"Published: {kayla_counts['published']}/{kayla_counts['total']}",
        f"Ready: {kayla_counts['ready']} | Waiting: {kayla_counts['waiting']} | Blocked: {kayla_counts['blocked']}",
        f"Next due: {next_due_slot(KAYLA_AD_SLOTS, now, target_date)}",
    ]

    if urgent_total:
        lines.append("")
        lines.append("URGENT")
        urgent_lines: list[str] = []
        urgent_lines.extend(short_issue_lines("Video", urgent_video_rows, 4))
        urgent_lines.extend(short_issue_lines("Quiz", urgent_image_rows, 3))
        urgent_lines.extend(short_issue_lines("Kayla", urgent_kayla_rows, 3))
        urgent_lines.extend(short_issue_lines("Other", urgent_other_rows, 2))
        for line in urgent_lines[:10]:
            lines.append(line)
        shown = len(urgent_lines[:10])
        if urgent_total > shown:
            lines.append(f"- +{urgent_total - shown} autre(s) urgence(s) dans le rapport GitHub")

    if prep_total:
        lines.append("")
        lines.append("PREPARATION")
        prep_lines: list[str] = []
        prep_lines.extend(short_issue_lines("Video", prep_video_rows, 4))
        prep_lines.extend(short_issue_lines("Quiz", prep_image_rows, 3))
        prep_lines.extend(short_issue_lines("Kayla", prep_kayla_rows, 3))
        prep_lines.extend(short_issue_lines("Other", prep_other_rows, 2))
        for line in prep_lines[:10]:
            lines.append(line)
        shown = len(prep_lines[:10])
        if prep_total > shown:
            lines.append(f"- +{prep_total - shown} autre(s) preparation(s) dans le rapport GitHub")

    if github_run_url:
        lines.append("")
        lines.append(f"Rapport complet: {github_run_url}")

    lines.append("")
    lines.append("Read-only: rien n'a ete modifie.")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    load_local_env(repo_root())
    now = toronto_now()
    target_date = args.date or now.strftime("%Y-%m-%d")
    rows = query_rows_for_date(target_date)
    image_rows = query_image_rows_for_date(target_date)
    report = render_report(rows, image_rows, now, target_date, args.lookback_runs)
    print(report)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report, encoding="utf-8")
    if args.telegram_output:
        telegram_path = Path(args.telegram_output)
        telegram_path.parent.mkdir(parents=True, exist_ok=True)
        telegram_path.write_text(render_telegram_message(rows, image_rows, now, target_date), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
