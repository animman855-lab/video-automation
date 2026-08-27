from __future__ import annotations

import subprocess
import shutil
from pathlib import Path


OUTRO_ASSETS = {
    "teacherryan": "teacherryan-cta.mp4",
    "oliviaa": "oliviaa-cta.mp4",
    "thefluentbuild": "thefluentbuild-cta.mp4",
    "cindy": "cindy-cta.mp4",
}


def _ffmpeg_path() -> str:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("FFmpeg is required to append a HyperFrames outro.")
        return ffmpeg


def _run(command: list[str], timeout: int = 300) -> None:
    subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)


def _validate_media(ffmpeg: str, path: Path) -> None:
    if not path.exists() or path.stat().st_size <= 0:
        raise RuntimeError(f"Outro output is missing or empty: {path}")
    _run([ffmpeg, "-v", "error", "-i", str(path), "-f", "null", "-"], timeout=60)


def append_hyperframes_outro(base_video: Path, avatar: str, work_dir: Path) -> Path:
    """Append the avatar-specific CTA clip with a hard cut.

    Both inputs are normalized in the FFmpeg filter graph so the result stays
    compatible with the publisher even when source videos use different audio
    sample rates or channel layouts.
    """
    avatar_key = avatar.strip().lower()
    asset_name = OUTRO_ASSETS.get(avatar_key)
    if not asset_name:
        raise ValueError(f"No HyperFrames outro is configured for avatar '{avatar}'.")
    if not base_video.exists() or base_video.stat().st_size <= 0:
        raise RuntimeError(f"Base video is missing or empty: {base_video}")

    asset_path = Path(__file__).resolve().parents[1] / "assets" / asset_name
    if not asset_path.exists() or asset_path.stat().st_size <= 0:
        raise FileNotFoundError(f"HyperFrames outro asset is missing: {asset_path}")

    work_dir.mkdir(parents=True, exist_ok=True)
    output_path = work_dir / f"{base_video.stem}-with-outro.mp4"
    ffmpeg = _ffmpeg_path()
    video_filter = (
        "scale=1080:1920:force_original_aspect_ratio=decrease,"
        "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps=30,format=yuv420p"
    )
    audio_filter = "aresample=44100,aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo"
    command = [
        ffmpeg,
        "-y",
        "-i",
        str(base_video),
        "-i",
        str(asset_path),
        "-filter_complex",
        (
            f"[0:v]{video_filter}[v0];"
            f"[1:v]{video_filter}[v1];"
            f"[0:a]{audio_filter}[a0];"
            f"[1:a]{audio_filter}[a1];"
            "[v0][a0][v1][a1]concat=n=2:v=1:a=1[outv][outa]"
        ),
        "-map",
        "[outv]",
        "-map",
        "[outa]",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-ar",
        "44100",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    _run(command)
    _validate_media(ffmpeg, output_path)
    return output_path
