from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

from drive_client import check_drive_secrets, upload_video_make_public
from notion_client import load_local_env, prop_text, query_teacher_ryan_animals_pilot, set_ready_to_publish
from render_video import ANIMALS, download_image, render_teacher_ryan_video
from safety import (
    PilotLimits,
    SafetyError,
    require_at_most_one_row,
    require_empty,
    require_file_created,
    require_non_empty,
    require_single_row,
    require_tts_budget,
)
from tts_google import check_tts_secrets, synthesize_words


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="HyperFrames TeacherRyan animals pilot.")
    parser.add_argument("--execute", action="store_true", help="Run real generation/upload/update.")
    return parser.parse_args()


def _load_and_validate(dry_run: bool) -> tuple[dict, dict, int]:
    limits = PilotLimits()
    rows = query_teacher_ryan_animals_pilot()
    page = require_single_row(rows, limits) if dry_run else require_at_most_one_row(rows)

    if page is None:
        print("No matching HyperFrames pilot row found. Nothing to do.")
        raise SystemExit(0)

    props = page.get("properties", {})
    script = prop_text(props, "Script")
    image_url = prop_text(props, "Image HyperFrames")
    video_url = prop_text(props, "Lien Video")
    prompt_1 = prop_text(props, "Prompt 1")

    require_non_empty(image_url, "Image HyperFrames")
    if video_url:
        print("Lien Video is already filled. Skipping to avoid duplicate generation.")
        raise SystemExit(0)
    require_empty(video_url, "Lien Video")
    require_non_empty(script, "Script")
    require_non_empty(prompt_1, "Prompt 1")
    tts_chars = require_tts_budget(script, limits)
    return page, props, tts_chars


def _print_summary(page: dict, props: dict, tts_chars: int) -> None:
    limits = PilotLimits()
    print("Matched Notion row:")
    print(f"- page_id: {page.get('id')}")
    print(f"- title: {prop_text(props, 'Titre')}")
    print(f"- avatar: {prop_text(props, 'Avatar')}")
    print(f"- date: {prop_text(props, 'Date Publication')}")
    print(f"- slot: {prop_text(props, 'Slot')}")
    print(f"- video_type: {prop_text(props, 'Video Type')}")
    print(f"- status: {prop_text(props, 'Statut')}")
    print(f"- image_hyperframes_present: {bool(prop_text(props, 'Image HyperFrames'))}")
    print(f"- lien_video_empty: {not bool(prop_text(props, 'Lien Video'))}")
    print(f"- script_chars_for_tts: {tts_chars}/{limits.max_tts_chars}")


def dry_run() -> int:
    page, props, tts_chars = _load_and_validate(dry_run=True)
    missing_drive = check_drive_secrets()
    missing_tts = check_tts_secrets()

    print("HYPERFRAMES PILOT DRY-RUN")
    print("No paid calls will be made.")
    print("No Notion update will be made.")
    print("No audio, video, Drive upload, or Upload-Post publication will run.")
    print("")
    _print_summary(page, props, tts_chars)
    print("")
    print("Would do later, in real mode:")
    print("- download/read the Flow image from Image HyperFrames")
    print("- generate Google Cloud TTS audio for the vocabulary words")
    print("- render the TeacherRyan animals MP4")
    print("- upload the MP4 to Google Drive")
    print("- make the Drive file public/shareable")
    print("- write the public Drive URL to Lien Video")
    print("- set Statut to A publier")
    print("")
    print("Secret readiness check:")
    print(f"- drive_missing: {', '.join(missing_drive) if missing_drive else 'none'}")
    print(f"- tts_missing: {', '.join(missing_tts) if missing_tts else 'none'}")
    return 0


def execute() -> int:
    page, props, tts_chars = _load_and_validate(dry_run=False)
    _print_summary(page, props, tts_chars)

    work_dir = Path(tempfile.mkdtemp(prefix="hyperframes_teacherryan_"))
    try:
        image_path = download_image(prop_text(props, "Image HyperFrames"), work_dir / "source_image")
        audio_path = synthesize_words(ANIMALS, work_dir / "teacherryan-animals.mp3")
        video_path = render_teacher_ryan_video(
            image_path=image_path,
            audio_path=audio_path,
            output_path=work_dir / "teacherryan-hyperframes-animals-2026-06-07.mp4",
            frames_dir=work_dir / "frames",
        )

        require_file_created(str(audio_path), "TTS audio")
        require_file_created(str(video_path), "HyperFrames video")

        drive_url = upload_video_make_public(video_path, "teacherryan-hyperframes-animals-2026-06-07.mp4")
        require_non_empty(drive_url, "Google Drive public URL")
        set_ready_to_publish(page["id"], drive_url)
        print("HyperFrames pilot completed and Notion set to A publier.")
        print(f"Drive URL: {drive_url}")
        return 0
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def main() -> int:
    args = parse_args()
    root = repo_root()
    load_local_env(root)
    return execute() if args.execute else dry_run()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SafetyError as exc:
        print(f"SAFETY_STOP: {exc}", file=sys.stderr)
        raise SystemExit(2)
