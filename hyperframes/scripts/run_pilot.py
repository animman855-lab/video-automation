from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pytz

from dialogue_parser import parse_dialogue_script
from drive_client import check_drive_secrets, upload_video_make_public
from notion_client import (
    load_local_env,
    prop_text,
    query_ready_hyperframes_rows,
    set_ready_to_publish,
)
from podcast_parser import parse_podcast_script
from render_cindy_podcast import render_cindy_podcast_video
from render_oliviaa_drama import render_oliviaa_drama_video
from render_thefluentbuild_grandma import render_thefluentbuild_grandma_video
from render_video import download_image, render_teacher_ryan_video
from safety import (
    PilotLimits,
    SafetyError,
    require_file_created,
    require_item_budget,
    require_non_empty,
    require_tts_budget,
)
from script_parser import parse_vocabulary_items
from tts_google import (
    check_tts_secrets,
    synthesize_cindy_podcast_audios,
    synthesize_dialogue_audios,
    synthesize_item_audios,
    synthesize_thefluentbuild_audios,
)


SLOT_HOURS = {
    "08:00": 8 * 60,
    "16:00": 16 * 60,
    "00:00": 0,
}
SUPPORTED_AVATARS = {"teacherryan", "oliviaa", "thefluentbuild", "cindy"}
TEACHERRYAN_FIXED_TARGETS = [
    (300, 420),
    (780, 420),
    (300, 650),
    (780, 650),
    (300, 880),
    (780, 880),
    (300, 1110),
    (780, 1110),
    (300, 1340),
    (780, 1340),
]


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run ready HyperFrames rows from Notion.")
    parser.add_argument("--execute", action="store_true", help="Run real generation/upload/update.")
    return parser.parse_args()


def toronto_now() -> datetime:
    return datetime.now(pytz.timezone("America/Toronto"))


def slot_is_due(slot_name: str, now: datetime | None = None) -> bool:
    current = now or toronto_now()
    current_minutes = current.hour * 60 + current.minute
    slot_minutes = SLOT_HOURS.get(slot_name)
    if slot_minutes is None:
        return False
    diff = current_minutes - slot_minutes
    if slot_minutes == 0:
        diff = current_minutes if current_minutes < 180 else -1
    return 0 <= diff <= 480


def max_videos_per_run() -> int:
    value = int(os.getenv("HYPERFRAMES_MAX_VIDEOS", "5"))
    return max(1, min(value, 5))


def _ready_rows_for_now() -> list[dict]:
    now = toronto_now()
    today = now.strftime("%Y-%m-%d")
    rows = query_ready_hyperframes_rows(today)
    due_rows: list[dict] = []
    avatars_seen: set[str] = set()

    for row in rows:
        props = row.get("properties", {})
        avatar = prop_text(props, "Avatar").lower()
        slot = prop_text(props, "Slot")
        if avatar not in SUPPORTED_AVATARS:
            print(f"Skipping unsupported HyperFrames avatar: {avatar}")
            continue
        if not slot_is_due(slot, now):
            print(f"Slot {slot} not due yet for HyperFrames row {row.get('id')} - skipping.")
            continue
        if avatar in avatars_seen:
            print(f"Avatar {avatar} already selected for this HyperFrames run - skipping row {row.get('id')}.")
            continue
        due_rows.append(row)
        avatars_seen.add(avatar)
        if len(due_rows) >= max_videos_per_run():
            break

    return due_rows


def _print_row_summary(row: dict) -> None:
    props = row.get("properties", {})
    print("Matched HyperFrames row:")
    print(f"- page_id: {row.get('id')}")
    print(f"- title: {prop_text(props, 'Titre')}")
    print(f"- avatar: {prop_text(props, 'Avatar')}")
    print(f"- date: {prop_text(props, 'Date Publication')}")
    print(f"- slot: {prop_text(props, 'Slot')}")
    print(f"- video_type: {prop_text(props, 'Video Type')}")
    print(f"- status: {prop_text(props, 'Statut')}")
    print(f"- image_hyperframes_present: {bool(prop_text(props, 'Image HyperFrames'))}")
    print(f"- lien_video_empty: {not bool(prop_text(props, 'Lien Video'))}")


def dry_run() -> int:
    rows = _ready_rows_for_now()
    missing_drive = check_drive_secrets()
    missing_tts = check_tts_secrets()

    print("HYPERFRAMES DRY-RUN")
    print("No paid calls will be made.")
    print("No Notion update will be made.")
    print("No audio, video, Drive upload, or Upload-Post publication will run.")
    print(f"Max videos per run: {max_videos_per_run()}")
    print("")

    if not rows:
        print("No ready due HyperFrames row found. Nothing to do.")
    for row in rows:
        _print_row_summary(row)
        print("")

    print("Secret readiness check:")
    print(f"- drive_missing: {', '.join(missing_drive) if missing_drive else 'none'}")
    print(f"- tts_missing: {', '.join(missing_tts) if missing_tts else 'none'}")
    return 0


def _output_name(row: dict) -> str:
    props = row.get("properties", {})
    avatar = prop_text(props, "Avatar").lower()
    date = prop_text(props, "Date Publication")
    slot = prop_text(props, "Slot").replace(":", "")
    page_id = row.get("id", "")[:8]
    return f"hyperframes-{avatar}-{date}-{slot}-{page_id}.mp4"


def _teacher_ryan_fixed_targets(items: list[str]) -> dict[str, tuple[int, int]]:
    if len(items) > len(TEACHERRYAN_FIXED_TARGETS):
        raise SafetyError(
            "TeacherRyan fixed 2x5 layout supports at most "
            f"{len(TEACHERRYAN_FIXED_TARGETS)} items, got {len(items)}."
        )
    return {item: TEACHERRYAN_FIXED_TARGETS[index] for index, item in enumerate(items)}


def _render_teacher_ryan(row: dict, work_dir: Path) -> Path:
    limits = PilotLimits()
    props = row.get("properties", {})
    script = prop_text(props, "Script")
    image_url = prop_text(props, "Image HyperFrames")
    prompt_1 = prop_text(props, "Prompt 1")

    require_non_empty(image_url, "Image HyperFrames")
    require_non_empty(script, "Script")
    require_non_empty(prompt_1, "Prompt 1")

    items = parse_vocabulary_items(script)
    require_item_budget(items, limits)
    require_tts_budget(script, limits)

    image_path = download_image(image_url, work_dir / "source_image")
    item_targets = _teacher_ryan_fixed_targets(items)
    print("TeacherRyan fixed 2x5 arrow targets enabled. Image analysis is not required.")

    item_audio_paths = synthesize_item_audios(items, work_dir / "item_audio")
    for item in items:
        require_file_created(str(item_audio_paths[item]), f"TTS audio for {item}")

    video_path = render_teacher_ryan_video(
        image_path=image_path,
        item_audio_paths=item_audio_paths,
        output_path=work_dir / _output_name(row),
        frames_dir=work_dir / "frames",
        items=items,
        item_targets=item_targets,
    )
    require_file_created(str(video_path), "TeacherRyan HyperFrames video")
    return video_path


def _render_oliviaa(row: dict, work_dir: Path) -> Path:
    props = row.get("properties", {})
    script = prop_text(props, "Script")
    image_url = prop_text(props, "Image HyperFrames")
    prompt_1 = prop_text(props, "Prompt 1")

    require_non_empty(image_url, "Image HyperFrames")
    require_non_empty(script, "Script")
    require_non_empty(prompt_1, "Prompt 1")

    dialogue = parse_dialogue_script(script)
    image_path = download_image(image_url, work_dir / "source_image")
    line_audio_paths, cta_audio_path = synthesize_dialogue_audios(
        dialogue.lines,
        dialogue.cta,
        work_dir / "dialogue_audio",
    )
    for index, audio_path in enumerate(line_audio_paths, start=1):
        require_file_created(str(audio_path), f"TTS audio for Oliviaa line {index}")
    require_file_created(str(cta_audio_path), "TTS audio for Oliviaa CTA")

    video_path = render_oliviaa_drama_video(
        image_path=image_path,
        output_path=work_dir / _output_name(row),
        frames_dir=work_dir / "frames",
        dialogue=dialogue,
        line_audio_paths=line_audio_paths,
        cta_audio_path=cta_audio_path,
    )
    require_file_created(str(video_path), "Oliviaa HyperFrames video")
    return video_path


def _render_thefluentbuild(row: dict, work_dir: Path) -> Path:
    props = row.get("properties", {})
    script = prop_text(props, "Script")
    image_url = prop_text(props, "Image HyperFrames")
    prompt_1 = prop_text(props, "Prompt 1")

    require_non_empty(image_url, "Image HyperFrames")
    require_non_empty(script, "Script")
    require_non_empty(prompt_1, "Prompt 1")

    dialogue = parse_dialogue_script(script)
    image_path = download_image(image_url, work_dir / "source_image")
    line_audio_paths, cta_audio_path = synthesize_thefluentbuild_audios(
        dialogue.lines,
        dialogue.cta,
        work_dir / "thefluentbuild_audio",
    )
    for index, audio_path in enumerate(line_audio_paths, start=1):
        require_file_created(str(audio_path), f"TTS audio for TheFluentBuild line {index}")
    require_file_created(str(cta_audio_path), "TTS audio for TheFluentBuild CTA")

    video_path = render_thefluentbuild_grandma_video(
        image_path=image_path,
        output_path=work_dir / _output_name(row),
        frames_dir=work_dir / "frames",
        dialogue=dialogue,
        line_audio_paths=line_audio_paths,
        cta_audio_path=cta_audio_path,
    )
    require_file_created(str(video_path), "TheFluentBuild HyperFrames video")
    return video_path


def _render_cindy(row: dict, work_dir: Path) -> Path:
    props = row.get("properties", {})
    script = prop_text(props, "Script")
    image_url = prop_text(props, "Image HyperFrames")
    prompt_1 = prop_text(props, "Prompt 1")

    require_non_empty(image_url, "Image HyperFrames")
    require_non_empty(script, "Script")
    require_non_empty(prompt_1, "Prompt 1")

    podcast = parse_podcast_script(script)
    image_path = download_image(image_url, work_dir / "source_image")
    line_audio_paths = synthesize_cindy_podcast_audios(
        podcast.lines,
        work_dir / "cindy_audio",
    )
    for index, audio_path in enumerate(line_audio_paths, start=1):
        require_file_created(str(audio_path), f"TTS audio for Cindy podcast line {index}")

    video_path = render_cindy_podcast_video(
        image_path=image_path,
        output_path=work_dir / _output_name(row),
        frames_dir=work_dir / "frames",
        podcast=podcast,
        line_audio_paths=line_audio_paths,
    )
    require_file_created(str(video_path), "Cindy HyperFrames podcast video")
    return video_path


def _render_row(row: dict, work_dir: Path) -> Path:
    avatar = prop_text(row.get("properties", {}), "Avatar").lower()
    if avatar == "teacherryan":
        return _render_teacher_ryan(row, work_dir)
    if avatar == "oliviaa":
        return _render_oliviaa(row, work_dir)
    if avatar == "thefluentbuild":
        return _render_thefluentbuild(row, work_dir)
    if avatar == "cindy":
        return _render_cindy(row, work_dir)
    raise SafetyError(f"Unsupported HyperFrames avatar: {avatar}")


def _execute_row(row: dict) -> bool:
    _print_row_summary(row)
    work_dir = Path(tempfile.mkdtemp(prefix="hyperframes_"))
    try:
        video_path = _render_row(row, work_dir)
        drive_url = upload_video_make_public(video_path, video_path.name)
        require_non_empty(drive_url, "Google Drive public URL")
        set_ready_to_publish(row["id"], drive_url)
        print("HyperFrames row completed. Notion Lien Video filled and Statut set to A publier.")
        print(f"Drive URL: {drive_url}")
        return True
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def execute() -> int:
    rows = _ready_rows_for_now()
    if not rows:
        print("No ready due HyperFrames row found. Nothing to do.")
        return 0

    succeeded = 0
    failed = 0
    for row in rows:
        try:
            if _execute_row(row):
                succeeded += 1
        except Exception as exc:
            failed += 1
            print(f"HYPERFRAMES_ROW_FAILED page_id={row.get('id')}: {exc}", file=sys.stderr)

    print(f"HyperFrames summary: succeeded={succeeded} failed={failed}")
    if succeeded == 0 and failed > 0:
        return 2
    return 0


def main() -> int:
    args = parse_args()
    root = repo_root()
    load_local_env(root)
    if args.execute:
        return execute()
    return dry_run()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SafetyError as exc:
        print(f"SAFETY_STOP: {exc}", file=sys.stderr)
        raise SystemExit(2)
