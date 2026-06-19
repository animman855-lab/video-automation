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


def render_report(rows: list[dict], now: datetime, target_date: str, lookback_runs: int) -> str:
    summaries = [row_summary(row, now) for row in rows]
    issue_rows = [item for item in summaries if item["issues"]]
    ready_rows = [item for item in summaries if not item["issues"] and item["status"] == "A publier"]
    published_rows = [item for item in summaries if item["status"] == "Publie"]

    lines: list[str] = []
    lines.append("# Saloo Publishing Watchdog")
    lines.append("")
    lines.append(f"- Date checked: {target_date}")
    lines.append(f"- Current Montreal time: {now.strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"- Rows found: {len(rows)}")
    lines.append(f"- Rows with issues: {len(issue_rows)}")
    lines.append(f"- Rows ready/no detected issue: {len(ready_rows)}")
    lines.append(f"- Rows published: {len(published_rows)}")
    lines.append("")

    if issue_rows:
        lines.append("## Issues")
        for item in issue_rows:
            lines.append(f"- [{item['slot']}] {item['avatar']} | {item['video_type']} | {item['status']} | {item['title']}")
            for issue in item["issues"]:
                lines.append(f"  - ISSUE: {issue}")
            for note in item["notes"]:
                lines.append(f"  - note: {note}")
        lines.append("")
    else:
        lines.append("## Issues")
        lines.append("- No issues detected.")
        lines.append("")

    lines.append("## Rows")
    for item in summaries:
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


def main() -> int:
    args = parse_args()
    load_local_env(repo_root())
    now = toronto_now()
    target_date = args.date or now.strftime("%Y-%m-%d")
    rows = query_rows_for_date(target_date)
    report = render_report(rows, now, target_date, args.lookback_runs)
    print(report)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
