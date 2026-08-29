from __future__ import annotations

import argparse
import os
import random
import re
import shutil
import sys
import tempfile
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytz
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dialogue_parser import parse_dialogue_script
from drive_client import check_drive_secrets, upload_video_make_public
from image_analyzer import ImageAnalysisError, analyze_vocabulary_grid, analyze_vocabulary_labels_ocr
from notion_client import (
    load_local_env,
    prop_text,
    query_ready_hyperframes_rows,
    set_ready_to_publish,
    set_status_ready_to_publish,
)
from podcast_parser import parse_podcast_script
from render_cindy_podcast import render_cindy_podcast_video
from hyperframes_outro import append_hyperframes_outro
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
from publish_timing import (
    queryable_dates,
    read_slot_lock,
    slot_is_due as shared_slot_is_due,
    slot_key,
    slot_sort_value,
)
from script_parser import parse_vocabulary_cta, parse_vocabulary_items
from tts_google import (
    _access_token,
    check_tts_secrets,
    _smooth_spoken_text,
    _synthesize_text,
    synthesize_cindy_podcast_audios as synthesize_cindy_podcast_audios_google,
    synthesize_teacher_ryan_audios,
    synthesize_thefluentbuild_audios as synthesize_thefluentbuild_audios_google,
)

try:
    from kokoro import KPipeline
    from tts_kokoro import (
        KOKORO_CINDY_GUEST_VOICE,
        KOKORO_CINDY_VOICE,
        KOKORO_OLIVIAA_MALE_VOICE,
        KOKORO_OLIVIAA_VOICE,
        KOKORO_TEACHERRYAN_VOICE,
        KOKORO_THEFLUENTBUILD_GRANDMA_VOICE,
        KOKORO_THEFLUENTBUILD_LEARNER_VOICE,
        synthesize_teacher_ryan_audios_kokoro,
        synthesize_text_kokoro,
    )
except Exception as exc:
    KPipeline = None
    KOKORO_CINDY_GUEST_VOICE = "am_puck"
    KOKORO_CINDY_VOICE = "af_jessica"
    KOKORO_OLIVIAA_MALE_VOICE = "bm_daniel"
    KOKORO_OLIVIAA_VOICE = "bf_emma"
    KOKORO_TEACHERRYAN_VOICE = "am_echo"
    KOKORO_THEFLUENTBUILD_GRANDMA_VOICE = "af_aoede"
    KOKORO_THEFLUENTBUILD_LEARNER_VOICE = "am_echo"
    synthesize_teacher_ryan_audios_kokoro = None
    synthesize_text_kokoro = None
    KOKORO_IMPORT_ERROR = exc
else:
    KOKORO_IMPORT_ERROR = None


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
TEACHERRYAN_FALLBACK_CTA = "Practice these words in real conversations with Saloo English."
TRANSIENT_HTTP_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}


class HyperFramesStageError(RuntimeError):
    """Preserve the pipeline stage while keeping the original exception chain."""

    def __init__(self, stage: str, cause: Exception):
        self.stage = stage
        self.cause = cause
        super().__init__(f"{stage}: {type(cause).__name__}: {cause}")


def _run_stage(stage: str, operation):
    try:
        return operation()
    except Exception as exc:
        raise HyperFramesStageError(stage, exc) from exc


def _retry_attempts() -> int:
    try:
        value = int(os.getenv("HYPERFRAMES_RETRY_ATTEMPTS", "3"))
    except ValueError:
        value = 3
    return max(1, min(value, 5))


def _retry_base_delay() -> float:
    try:
        value = float(os.getenv("HYPERFRAMES_RETRY_BASE_DELAY", "5"))
    except ValueError:
        value = 5.0
    return max(0.0, min(value, 60.0))


def _retry_max_delay() -> float:
    try:
        value = float(os.getenv("HYPERFRAMES_RETRY_MAX_DELAY", "30"))
    except ValueError:
        value = 30.0
    return max(0.0, min(value, 300.0))


def _retry_delay(failed_attempt: int) -> float:
    exponential = min(_retry_base_delay() * (2 ** max(0, failed_attempt - 1)), _retry_max_delay())
    return round(exponential + random.uniform(0.0, min(1.0, exponential / 5 if exponential else 0.0)), 2)


def _is_transient_error(exc: BaseException) -> bool:
    """Retry only connection/timeouts and explicitly temporary HTTP failures."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(
            current,
            (
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
                requests.exceptions.ChunkedEncodingError,
                ConnectionResetError,
                TimeoutError,
            ),
        ):
            return True

        response = getattr(current, "response", None)
        if response is not None and getattr(response, "status_code", None) in TRANSIENT_HTTP_STATUS_CODES:
            return True

        # tts_google.py exposes HTTP failures as RuntimeError text rather than
        # requests.HTTPError, so classify only its explicit status code form.
        match = re.search(r"Google TTS failed(?: for '[^']*')?:\s*(\d{3})\b", str(current))
        if match and int(match.group(1)) in TRANSIENT_HTTP_STATUS_CODES:
            return True

        current = getattr(current, "cause", None) or current.__cause__ or current.__context__
    return False


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run ready HyperFrames rows from Notion.")
    parser.add_argument("--execute", action="store_true", help="Run real generation/upload/update.")
    return parser.parse_args()


def toronto_now() -> datetime:
    return datetime.now(pytz.timezone("America/Toronto"))


def max_videos_per_run() -> int:
    value = int(os.getenv("HYPERFRAMES_MAX_VIDEOS", "5"))
    return max(1, min(value, 5))


def _ready_rows_for_now() -> list[dict]:
    now = toronto_now()
    rows: list[dict] = []
    seen_ids: set[str] = set()
    for publication_date in queryable_dates(now):
        for row in query_ready_hyperframes_rows(publication_date):
            if row.get("id") not in seen_ids:
                rows.append(row)
                seen_ids.add(row.get("id"))

    due_rows: list[dict] = []
    candidates: list[tuple[datetime, str]] = []
    for row in rows:
        props = row.get("properties", {})
        avatar = prop_text(props, "Avatar").lower()
        slot = prop_text(props, "Slot")
        publication_date = prop_text(props, "Date Publication")
        if avatar not in SUPPORTED_AVATARS:
            print(f"Skipping unsupported HyperFrames avatar: {avatar}")
            continue
        if not shared_slot_is_due(slot, publication_date, now):
            print(f"Slot {slot} not due yet for HyperFrames row {row.get('id')} - skipping.")
            continue
        candidates.append((slot_sort_value(publication_date, slot), slot_key(publication_date, slot)))

    if not candidates:
        return []

    selected_key = min(candidates, key=lambda item: item[0])[1]
    existing_lock = read_slot_lock()
    if existing_lock and existing_lock != selected_key:
        print(f"Another slot is already active in this run ({existing_lock}); skipping HyperFrames generation.")
        return []

    avatars_seen: set[str] = set()

    for row in rows:
        props = row.get("properties", {})
        avatar = prop_text(props, "Avatar").lower()
        slot = prop_text(props, "Slot")
        publication_date = prop_text(props, "Date Publication")
        if avatar not in SUPPORTED_AVATARS:
            continue
        if slot_key(publication_date, slot) != selected_key:
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
    print("No audio, video, optional Drive upload, or Upload-Post publication will run.")
    print(f"Max videos per run: {max_videos_per_run()}")
    print(f"Local output dir: {_local_output_dir()}")
    print(f"Drive upload enabled: {_drive_upload_enabled()}")
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


def _local_output_dir() -> Path:
    configured = os.getenv("HYPERFRAMES_OUTPUT_DIR", "hyperframes-output").strip() or "hyperframes-output"
    output_dir = Path(configured)
    if not output_dir.is_absolute():
        output_dir = repo_root() / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _drive_upload_enabled() -> bool:
    return os.getenv("HYPERFRAMES_UPLOAD_DRIVE", "1").strip().lower() not in {"0", "false", "no"}


def _teacher_ryan_fixed_targets(items: list[str]) -> dict[str, tuple[int, int]]:
    if len(items) > len(TEACHERRYAN_FIXED_TARGETS):
        raise SafetyError(
            "TeacherRyan fixed 2x5 layout supports at most "
            f"{len(TEACHERRYAN_FIXED_TARGETS)} items, got {len(items)}."
        )
    return {item: TEACHERRYAN_FIXED_TARGETS[index] for index, item in enumerate(items)}


def _teacher_ryan_ocr_hybrid_targets(image_path: Path, items: list[str]) -> dict[str, tuple[int, int]]:
    ocr_analysis = None
    grid_analysis = None
    ocr_error = None
    grid_error = None

    try:
        ocr_analysis = analyze_vocabulary_labels_ocr(image_path, items)
    except ImageAnalysisError as exc:
        ocr_error = exc

    try:
        grid_analysis = analyze_vocabulary_grid(image_path, items)
    except ImageAnalysisError as exc:
        grid_error = exc

    if ocr_analysis is None and grid_analysis is None:
        raise SafetyError(
            "TeacherRyan arrow target detection failed: "
            f"OCR error: {ocr_error}; grid error: {grid_error}"
        )

    if ocr_analysis is not None and not ocr_analysis.missing:
        print("TeacherRyan OCR arrow targets enabled. All labels detected.")
        for item in items:
            print(f"- OCR target {item}: {ocr_analysis.targets[item]}")
        return ocr_analysis.targets

    if grid_analysis is not None:
        item_targets = dict(grid_analysis.targets)
        ocr_missing = items if ocr_analysis is None else ocr_analysis.missing
        if ocr_analysis is not None:
            for item, target in ocr_analysis.targets.items():
                item_targets[item] = target

        print(
            "TeacherRyan OCR + detected-grid arrow targets enabled. "
            f"OCR used for {0 if ocr_analysis is None else len(ocr_analysis.targets)} label(s); "
            f"detected grid used for {len(ocr_missing)} label(s). "
            f"Grid cells found: {grid_analysis.cells_found}."
        )
        if ocr_error is not None:
            print(f"- OCR unavailable, grid fallback used for all labels: {ocr_error}")
        if grid_error is not None:
            print(f"- Grid detection warning: {grid_error}")
        for item in items:
            source = "detected grid" if item in ocr_missing else "OCR"
            print(f"- {source} target {item}: {item_targets[item]}")
        return item_targets

    detected = ", ".join(ocr_analysis.detected_words[:80])
    if ocr_analysis.targets:
        print(
            "TeacherRyan grid detection failed; using OCR targets plus fixed fallback "
            f"for {len(ocr_analysis.missing)} missing label(s). Grid error: {grid_error}"
        )
    fixed_targets = _teacher_ryan_fixed_targets(items)
    item_targets = dict(ocr_analysis.targets)
    for missing_item in ocr_analysis.missing:
        item_targets[missing_item] = fixed_targets[missing_item]

    print(
        "TeacherRyan OCR hybrid arrow targets enabled. "
        f"OCR missing {len(ocr_analysis.missing)} label(s); fixed grid fallback used for: "
        f"{', '.join(ocr_analysis.missing)}. Detected words: {detected}"
    )
    for item in items:
        source = "fixed fallback" if item in ocr_analysis.missing else "OCR"
        print(f"- {source} target {item}: {item_targets[item]}")
    return item_targets


def _synthesize_teacher_ryan_audios(
    items: list[str],
    cta: str,
    output_dir: Path,
) -> tuple[dict[str, Path], Path]:
    print("TeacherRyan TTS provider: Google TTS")
    return synthesize_teacher_ryan_audios(items, cta, output_dir / "google")


def _kokoro_pipeline():
    if KPipeline is None or synthesize_text_kokoro is None:
        raise RuntimeError(f"Kokoro unavailable: {KOKORO_IMPORT_ERROR}")
    return KPipeline(lang_code="a")


def _synthesize_line_with_kokoro_google_fallback(
    text: str,
    output_path: Path,
    kokoro_voice: str,
    google_voice: str,
    google_rate: float,
    pipeline,
    label: str,
) -> Path:
    spoken = _smooth_spoken_text(text)
    try:
        print(f"{label} TTS provider: Kokoro {kokoro_voice}")
        return synthesize_text_kokoro(spoken, output_path.with_suffix(".wav"), kokoro_voice, pipeline=pipeline)
    except Exception as exc:
        print(
            f"WARNING: {label} Kokoro TTS failed "
            f"({type(exc).__name__}: {exc}). Falling back to Google TTS for this line."
        )
        token = _access_token()
        return _synthesize_text(
            spoken,
            output_path.with_suffix(".mp3"),
            token,
            voice_name=google_voice,
            speaking_rate=google_rate,
        )


def _synthesize_line_with_google_kokoro_fallback(
    text: str,
    output_path: Path,
    kokoro_voice: str,
    google_voice: str,
    google_rate: float,
    pipeline,
    label: str,
) -> Path:
    """Use Google as the primary voice and Kokoro only as a local fallback."""
    spoken = _smooth_spoken_text(text)
    try:
        print(f"{label} TTS provider: Google TTS {google_voice}")
        token = _access_token()
        return _synthesize_text(
            spoken,
            output_path.with_suffix(".mp3"),
            token,
            voice_name=google_voice,
            speaking_rate=google_rate,
        )
    except Exception as exc:
        print(
            f"WARNING: {label} Google TTS failed "
            f"({type(exc).__name__}: {exc}). Falling back to Kokoro {kokoro_voice}."
        )
        if pipeline is None:
            raise RuntimeError(
                f"{label} failed with Google TTS and Kokoro is unavailable."
            ) from exc
        try:
            print(f"{label} fallback provider: Kokoro {kokoro_voice}")
            return synthesize_text_kokoro(
                spoken,
                output_path.with_suffix(".wav"),
                kokoro_voice,
                pipeline=pipeline,
            )
        except Exception as fallback_exc:
            raise RuntimeError(
                f"{label} failed with both Google TTS and Kokoro fallback."
            ) from fallback_exc


def _synthesize_oliviaa_dialogue_audios(
    lines: list[str],
    cta: str,
    output_dir: Path,
    speakers: list[str] | None = None,
) -> tuple[list[Path], Path | None]:
    if not lines:
        raise RuntimeError("Refusing to synthesize an empty Oliviaa dialogue.")

    output_dir.mkdir(parents=True, exist_ok=True)
    speakers = speakers or []
    line_paths: list[Path] = []
    pipeline = None
    try:
        pipeline = _kokoro_pipeline()
    except Exception as exc:
        print(f"WARNING: Oliviaa Kokoro pipeline unavailable ({type(exc).__name__}: {exc}). Google fallback will be used.")

    for index, line in enumerate(lines, start=1):
        speaker = speakers[index - 1] if index - 1 < len(speakers) else ("oliviaa" if index % 2 == 1 else "male")
        is_oliviaa = speaker in {"olivia", "oliviaa", "oliviaaa"}
        kokoro_voice = KOKORO_OLIVIAA_VOICE if is_oliviaa else KOKORO_OLIVIAA_MALE_VOICE
        google_voice = "en-US-Neural2-F" if is_oliviaa else "en-US-Neural2-D"
        google_rate = 0.94 if is_oliviaa else 0.92
        output_path = output_dir / f"line_{index:02d}"
        if is_oliviaa:
            line_paths.append(
                _synthesize_line_with_kokoro_google_fallback(
                    line,
                    output_path,
                    kokoro_voice,
                    google_voice,
                    google_rate,
                    pipeline,
                    f"Oliviaa line {index}",
                )
            )
        else:
            line_paths.append(
                _synthesize_line_with_google_kokoro_fallback(
                    line,
                    output_path,
                    KOKORO_OLIVIAA_MALE_VOICE,
                    google_voice,
                    google_rate,
                    pipeline,
                    f"Oliviaa male line {index}",
                )
            )

    cta_path = None
    if cta:
        cta_path = _synthesize_line_with_kokoro_google_fallback(
            cta,
            output_dir / "cta",
            KOKORO_OLIVIAA_VOICE,
            "en-US-Neural2-F",
            0.9,
            pipeline,
            "Oliviaa CTA",
        )
    return line_paths, cta_path


def _synthesize_thefluentbuild_dialogue_audios(
    lines: list[str],
    cta: str,
    output_dir: Path,
    speakers: list[str] | None = None,
) -> tuple[list[Path], Path | None]:
    print("TheFluentBuild TTS provider: Google TTS")
    if not cta:
        if not lines:
            raise RuntimeError("Refusing to synthesize an empty TheFluentBuild dialogue.")
        output_dir = output_dir / "google"
        output_dir.mkdir(parents=True, exist_ok=True)
        token = _access_token()
        line_paths: list[Path] = []
        speakers = speakers or []
        for index, line in enumerate(lines, start=1):
            speaker = speakers[index - 1] if index - 1 < len(speakers) else ("grandma" if index % 2 == 0 else "learner")
            is_grandma = speaker == "grandma"
            voice = "en-US-Neural2-F" if is_grandma else "en-US-Neural2-C"
            rate = 0.9 if is_grandma else 0.94
            output_path = output_dir / f"line_{index:02d}.mp3"
            line_paths.append(
                _synthesize_text(_smooth_spoken_text(line), output_path, token, voice_name=voice, speaking_rate=rate)
            )
        return line_paths, None
    return synthesize_thefluentbuild_audios_google(lines, cta, output_dir / "google", speakers=speakers)


def _synthesize_cindy_podcast_audios(lines: list, output_dir: Path) -> list[Path]:
    print("Cindy TTS provider: Google TTS")
    return synthesize_cindy_podcast_audios_google(lines, output_dir / "google")


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
    cta = parse_vocabulary_cta(script, TEACHERRYAN_FALLBACK_CTA)
    require_item_budget(items, limits)
    require_tts_budget(script, limits)
    print(
        "TeacherRyan TTS input (spoken segments): "
        + " | ".join(f"{index}. {item}" for index, item in enumerate(items, start=1))
    )

    image_path = _run_stage(
        "image_download",
        lambda: download_image(image_url, work_dir / "source_image"),
    )
    target_mode = os.getenv("TEACHERRYAN_ARROW_TARGET_MODE", "ocr").strip().lower()
    if target_mode == "fixed":
        item_targets = _teacher_ryan_fixed_targets(items)
        print("TeacherRyan fixed 2x5 arrow targets enabled.")
    else:
        item_targets = _teacher_ryan_ocr_hybrid_targets(image_path, items)

    item_audio_paths, cta_audio_path = _run_stage(
        "tts",
        lambda: _synthesize_teacher_ryan_audios(items, "", work_dir / "item_audio"),
    )
    for item in items:
        require_file_created(str(item_audio_paths[item]), f"TTS audio for {item}")
    if cta_audio_path is not None:
        require_file_created(str(cta_audio_path), "TTS audio for TeacherRyan CTA")

    video_path = _run_stage(
        "video_render",
        lambda: render_teacher_ryan_video(
            image_path=image_path,
            item_audio_paths=item_audio_paths,
            output_path=work_dir / _output_name(row),
            frames_dir=work_dir / "frames",
            items=items,
            item_targets=item_targets,
            cta_audio_path=cta_audio_path,
            cta_text=cta,
            include_cta=False,
        ),
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

    dialogue = parse_dialogue_script(script, require_cta=False)
    image_path = _run_stage(
        "image_download",
        lambda: download_image(image_url, work_dir / "source_image"),
    )
    line_audio_paths, cta_audio_path = _run_stage(
        "tts",
        lambda: _synthesize_oliviaa_dialogue_audios(
            dialogue.lines,
            "",
            work_dir / "dialogue_audio",
            speakers=dialogue.speakers,
        ),
    )
    for index, audio_path in enumerate(line_audio_paths, start=1):
        require_file_created(str(audio_path), f"TTS audio for Oliviaa line {index}")
    cta_audio_path = None

    video_path = _run_stage(
        "video_render",
        lambda: render_oliviaa_drama_video(
            image_path=image_path,
            output_path=work_dir / _output_name(row),
            frames_dir=work_dir / "frames",
            dialogue=dialogue,
            line_audio_paths=line_audio_paths,
            cta_audio_path=cta_audio_path,
        ),
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

    dialogue = parse_dialogue_script(script, require_cta=False)
    if "grandma" not in dialogue.speakers:
        dialogue = replace(
            dialogue,
            speakers=["learner" if index % 2 == 0 else "grandma" for index in range(len(dialogue.lines))],
        )
    image_path = _run_stage(
        "image_download",
        lambda: download_image(image_url, work_dir / "source_image"),
    )
    line_audio_paths, cta_audio_path = _run_stage(
        "tts",
        lambda: _synthesize_thefluentbuild_dialogue_audios(
            dialogue.lines,
            "",
            work_dir / "thefluentbuild_audio",
            speakers=dialogue.speakers,
        ),
    )
    for index, audio_path in enumerate(line_audio_paths, start=1):
        require_file_created(str(audio_path), f"TTS audio for TheFluentBuild line {index}")
    cta_audio_path = None

    video_path = _run_stage(
        "video_render",
        lambda: render_thefluentbuild_grandma_video(
            image_path=image_path,
            output_path=work_dir / _output_name(row),
            frames_dir=work_dir / "frames",
            dialogue=dialogue,
            line_audio_paths=line_audio_paths,
            cta_audio_path=cta_audio_path,
        ),
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
    image_path = _run_stage(
        "image_download",
        lambda: download_image(image_url, work_dir / "source_image"),
    )
    line_audio_paths = _run_stage(
        "tts",
        lambda: _synthesize_cindy_podcast_audios(podcast.lines, work_dir / "cindy_audio"),
    )
    for index, audio_path in enumerate(line_audio_paths, start=1):
        require_file_created(str(audio_path), f"TTS audio for Cindy podcast line {index}")

    video_path = _run_stage(
        "video_render",
        lambda: render_cindy_podcast_video(
            image_path=image_path,
            output_path=work_dir / _output_name(row),
            frames_dir=work_dir / "frames",
            podcast=podcast,
            line_audio_paths=line_audio_paths,
        ),
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


def _render_row_with_retry(row: dict) -> tuple[Path, Path]:
    """Retry only the render phase so finalization/publication stays single-shot."""
    attempts = _retry_attempts()
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        work_dir = Path(tempfile.mkdtemp(prefix="hyperframes_"))
        try:
            print(f"HyperFrames render attempt {attempt}/{attempts} for {row.get('id')}.")
            return _render_row(row, work_dir), work_dir
        except Exception as exc:
            last_error = exc
            shutil.rmtree(work_dir, ignore_errors=True)
            if not _is_transient_error(exc) or attempt >= attempts:
                if _is_transient_error(exc):
                    print(
                        f"HYPERFRAMES_RETRY_EXHAUSTED page_id={row.get('id')} "
                        f"stage={getattr(exc, 'stage', 'unknown')} attempts={attempt}",
                        file=sys.stderr,
                    )
                raise

            delay = _retry_delay(attempt)
            print(
                f"HYPERFRAMES_TRANSIENT_FAILURE page_id={row.get('id')} "
                f"stage={getattr(exc, 'stage', 'unknown')} attempt={attempt}/{attempts} "
                f"error={exc}; retrying_in={delay}s",
                file=sys.stderr,
            )
            time.sleep(delay)

    raise RuntimeError(f"HyperFrames render failed without an exception for {row.get('id')}") from last_error


def _execute_row(row: dict) -> bool:
    _print_row_summary(row)
    video_path, work_dir = _render_row_with_retry(row)
    try:
        avatar = prop_text(row.get("properties", {}), "Avatar")
        outro_enabled = os.getenv("HYPERFRAMES_OUTRO_ENABLED", "1").strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }
        if outro_enabled:
            try:
                video_path = append_hyperframes_outro(video_path, avatar, work_dir / "outro")
                print(f"HyperFrames outro appended for {avatar}.")
            except Exception as exc:
                print(
                    f"WARNING: HyperFrames outro failed for {avatar} "
                    f"({type(exc).__name__}: {exc}). Continuing with the base video."
                )
        # Keep the canonical row-based filename so publish.py can discover the
        # final video after an avatar-specific outro has been appended.
        local_video_path = _local_output_dir() / _output_name(row)
        shutil.copy2(video_path, local_video_path)
        print(f"HyperFrames final video ready locally: {local_video_path}")

        if _drive_upload_enabled():
            drive_url = upload_video_make_public(local_video_path, local_video_path.name)
            require_non_empty(drive_url, "Google Drive public URL")
            set_ready_to_publish(row["id"], drive_url)
            print("HyperFrames row completed. Notion Lien Video filled and Statut set to A publier.")
            print(f"Drive URL: {drive_url}")
        else:
            set_status_ready_to_publish(row["id"])
            print("Drive upload disabled. Statut set to A publier; publisher will use local MP4.")
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
