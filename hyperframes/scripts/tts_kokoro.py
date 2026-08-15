from __future__ import annotations

from pathlib import Path
import re

import numpy as np
import soundfile as sf


KOKORO_TEACHERRYAN_VOICE = "am_santa"
KOKORO_OLIVIAA_VOICE = "bf_emma"
KOKORO_OLIVIAA_MALE_VOICE = "bm_daniel"
KOKORO_THEFLUENTBUILD_GRANDMA_VOICE = "af_aoede"
KOKORO_THEFLUENTBUILD_LEARNER_VOICE = "am_echo"
KOKORO_CINDY_VOICE = "af_jessica"
KOKORO_CINDY_GUEST_VOICE = "am_puck"
KOKORO_SAMPLE_RATE = 24000


def _safe_audio_name(index: int, text: str, suffix: str = "wav") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if not slug:
        slug = "item"
    return f"{index:02d}_{slug}.{suffix}"


def _synthesize_to_wav(
    pipeline,
    text: str,
    output_path: Path,
    voice: str = KOKORO_TEACHERRYAN_VOICE,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    chunks: list[np.ndarray] = []

    for _, _, audio in pipeline(text, voice=voice):
        chunks.append(np.asarray(audio, dtype=np.float32))

    if not chunks:
        raise RuntimeError(f"Kokoro returned no audio chunks for '{text}'.")

    combined = np.concatenate(chunks)
    sf.write(output_path, combined, KOKORO_SAMPLE_RATE)
    if output_path.stat().st_size <= 0:
        raise RuntimeError(f"Kokoro wrote an empty audio file for '{text}'.")
    return output_path


def synthesize_teacher_ryan_audios_kokoro(
    words: list[str],
    cta: str,
    output_dir: Path,
) -> tuple[dict[str, Path], Path]:
    if not words:
        raise RuntimeError("Refusing to synthesize an empty TeacherRyan word list with Kokoro.")

    output_dir.mkdir(parents=True, exist_ok=True)
    audio_paths: dict[str, Path] = {}
    from kokoro import KPipeline

    pipeline = KPipeline(lang_code="a")

    for index, word in enumerate(words, start=1):
        output_path = output_dir / _safe_audio_name(index, word)
        audio_paths[word] = _synthesize_to_wav(pipeline, word, output_path)

    cta_path = output_dir / "cta.wav"
    _synthesize_to_wav(pipeline, cta, cta_path)
    return audio_paths, cta_path


def synthesize_text_kokoro(
    text: str,
    output_path: Path,
    voice: str,
    pipeline=None,
) -> Path:
    if pipeline is None:
        from kokoro import KPipeline

        pipeline = KPipeline(lang_code="a")
    return _synthesize_to_wav(pipeline, text, output_path, voice=voice)
