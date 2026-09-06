import os
import shutil
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hyperframes" / "scripts"))

# Keep the unit tests focused and avoid importing the optional local TTS model.
sys.modules["kokoro"] = None
import run_pilot


class HyperFramesRetryTests(unittest.TestCase):
    def test_non_oliviaa_avatar_voice_mappings_are_local_kokoro(self):
        output_dir = Path("test-output")
        with patch.object(run_pilot, "_kokoro_pipeline", return_value=object()), patch.object(
            run_pilot, "synthesize_text_kokoro", side_effect=lambda text, path, voice, pipeline=None: path
        ) as synthesize:
            fluent_paths, fluent_cta = run_pilot._synthesize_thefluentbuild_dialogue_audios(
                ["learner line", "grandma line"], "", output_dir, speakers=["male", "grandma"]
            )
            cindy_lines = [
                type("Line", (), {"speaker": "cindy", "text": "host line"})(),
                type("Line", (), {"speaker": "guest", "text": "guest line"})(),
            ]
            cindy_paths = run_pilot._synthesize_cindy_podcast_audios(cindy_lines, output_dir)

        self.assertIsNone(fluent_cta)
        self.assertEqual(len(fluent_paths), 2)
        self.assertEqual(len(cindy_paths), 2)
        voices = [call.args[2] for call in synthesize.call_args_list]
        self.assertEqual(
            voices,
            [
                run_pilot.KOKORO_THEFLUENTBUILD_LEARNER_VOICE,
                run_pilot.KOKORO_THEFLUENTBUILD_GRANDMA_VOICE,
                run_pilot.KOKORO_CINDY_VOICE,
                run_pilot.KOKORO_CINDY_GUEST_VOICE,
            ],
        )

    def test_teacher_ryan_uses_oliviaa_male_kokoro_voice_path(self):
        with patch.object(
            run_pilot,
            "synthesize_teacher_ryan_audios_kokoro",
            return_value=({"hello": Path("hello.wav")}, None),
        ) as synthesize:
            result = run_pilot._synthesize_teacher_ryan_audios(
                ["hello"], "", Path("test-output")
            )

        self.assertEqual(result, ({"hello": Path("hello.wav")}, None))
        synthesize.assert_called_once_with(["hello"], "", Path("test-output") / "kokoro")
        self.assertEqual(
            run_pilot.KOKORO_TEACHERRYAN_VOICE,
            run_pilot.KOKORO_OLIVIAA_MALE_VOICE,
        )

    def test_transient_errors_are_retryable_but_auth_errors_are_not(self):
        self.assertTrue(run_pilot._is_transient_error(requests.ConnectionError("connection reset")))
        self.assertTrue(run_pilot._is_transient_error(requests.Timeout("read timeout")))
        self.assertTrue(run_pilot._is_transient_error(RuntimeError("Google TTS failed for 'hello': 503 temporary")))
        self.assertFalse(run_pilot._is_transient_error(RuntimeError("Google TTS failed for 'hello': 401 unauthorized")))
        self.assertFalse(run_pilot._is_transient_error(ValueError("invalid script")))

    def test_render_retries_a_transient_failure_then_succeeds(self):
        row = {"id": "page-123"}
        transient = run_pilot.HyperFramesStageError(
            "image_download",
            requests.ConnectionError("connection reset by peer"),
        )
        calls = []

        def render_once(current_row, work_dir):
            calls.append(work_dir)
            if len(calls) == 1:
                raise transient
            return work_dir / "output.mp4"

        with patch.dict(
            os.environ,
            {
                "HYPERFRAMES_RETRY_ATTEMPTS": "3",
                "HYPERFRAMES_RETRY_BASE_DELAY": "0",
                "HYPERFRAMES_RETRY_MAX_DELAY": "0",
            },
            clear=False,
        ), patch.object(run_pilot, "_render_row", side_effect=render_once), patch.object(
            run_pilot.time, "sleep"
        ) as sleep:
            output_path, work_dir = run_pilot._render_row_with_retry(row)

        try:
            self.assertEqual(len(calls), 2)
            self.assertEqual(output_path, work_dir / "output.mp4")
            sleep.assert_called_once_with(0.0)
            self.assertFalse(calls[0].exists())
            self.assertTrue(work_dir.exists())
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_permanent_failure_is_not_retried(self):
        row = {"id": "page-456"}
        with patch.object(run_pilot, "_render_row", side_effect=ValueError("invalid image")) as render, patch.object(
            run_pilot.time, "sleep"
        ) as sleep:
            with self.assertRaises(ValueError):
                run_pilot._render_row_with_retry(row)

        render.assert_called_once()
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
