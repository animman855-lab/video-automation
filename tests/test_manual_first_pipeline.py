import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("NOTION_TOKEN", "test-token")
os.environ.setdefault("NOTION_DATABASE_ID", "test-database")
os.environ.setdefault("UPLOAD_POST_API_KEY", "test-upload-key")

import publish
from hashtag_utils import prepare_video_metadata, validate_prepared_metadata
from metadata_provider import deterministic_metadata
from publication_report import final_status_for_successes, sanitize_error


def notion_row(
    video_type="Visual Vocabulary",
    link="https://drive.google.com/file/d/manual/view",
    status="A publier",
):
    return {
        "id": f"row-{video_type}-{status}",
        "properties": {
            "Titre": {"title": [{"plain_text": "Manual video"}]},
            "Avatar": {"select": {"name": "cindy"}},
            "Video Type": {"select": {"name": video_type}},
            "Statut": {"select": {"name": status}},
            "Date Publication": {"date": {"start": "2026-09-13"}},
            "Slot": {"select": {"name": "08:00"}},
            "Lien Video": {"url": link},
            "Script": {"rich_text": [{"plain_text": "A useful script"}]},
        },
    }


class ManualFirstPipelineTests(unittest.TestCase):
    def test_final_status_policy(self):
        self.assertEqual(final_status_for_successes(5, 5), ("Publie", "minimum_success_threshold_reached"))
        self.assertEqual(final_status_for_successes(4, 5), ("Publie", "minimum_success_threshold_reached"))
        self.assertEqual(final_status_for_successes(2, 5), ("Publie", "minimum_success_threshold_reached"))
        self.assertEqual(final_status_for_successes(1, 5), ("Partiel", "one_platform_succeeded"))
        self.assertEqual(final_status_for_successes(0, 5), ("Echec", "no_platform_succeeded"))

    def test_each_platform_is_attempted_after_one_exception(self):
        platforms = ["Instagram", "Facebook", "TikTok", "YouTube", "Pinterest"]
        metadata = {
            f"{platform.upper()}_{suffix}": f"{platform} {suffix}"
            for platform in platforms
            for suffix in ("TITLE", "DESCRIPTION")
        }
        calls = []

        def fake_publish(_path, _title, _description, _avatar, platform):
            calls.append(platform)
            if platform == "Facebook":
                raise RuntimeError("provider timeout")
            return {"success": True, "results": {platform.lower(): {"success": True}}}

        with patch.object(publish, "publish_video", side_effect=fake_publish):
            successes, failures, outcomes = publish.publish_video_to_platforms(
                "video.mp4", metadata, "kayla", platforms, "Manual video"
            )

        self.assertEqual(calls, platforms)
        self.assertEqual((successes, failures), (4, 1))
        self.assertFalse(outcomes["Facebook"]["success"])
        self.assertTrue(outcomes["Instagram"]["success"])

    def test_hyperframes_rows_are_not_selected_by_manual_flow(self):
        with patch.object(publish, "slot_is_due", return_value=True):
            selected, key = publish.select_due_slot_group(
                [notion_row("HyperFrames"), notion_row("Visual Vocabulary")],
                object(),
            )
        self.assertEqual(key, "2026-09-13|08:00")
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["properties"]["Video Type"]["select"]["name"], "Visual Vocabulary")

    def test_terminal_partial_and_failed_rows_are_not_selected(self):
        rows = [
            notion_row(status="Partiel"),
            notion_row(status="Echec"),
            notion_row(status="A publier"),
        ]
        with patch.object(publish, "slot_is_due", return_value=True):
            selected, key = publish.select_due_slot_group(rows, object())
        self.assertEqual(key, "2026-09-13|08:00")
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["properties"]["Statut"]["select"]["name"], "A publier")

    def test_localized_deterministic_metadata(self):
        arabic = deterministic_metadata("Say this English phrase", "cindy", ["Instagram"], ["#learnenglish"])
        spanish = deterministic_metadata("Say this English phrase", "thefluentbuild", ["Instagram"], ["#learnenglish"])
        indonesian = deterministic_metadata("Say this English phrase", "oliviaa", ["Instagram"], ["#learnenglish"])
        self.assertIn("تدرّب", arabic["INSTAGRAM_DESCRIPTION"])
        self.assertIn("Practica", spanish["INSTAGRAM_DESCRIPTION"])
        self.assertIn("Latih", indonesian["INSTAGRAM_DESCRIPTION"])
        self.assertNotIn("Portuguese", spanish["INSTAGRAM_DESCRIPTION"])

    def test_localized_hashtags_survive_platform_normalization(self):
        title, description = prepare_video_metadata("English phrase", "Practice this", "cindy", "Instagram")
        validate_prepared_metadata(title, description, "cindy", "Instagram")
        self.assertIn("#تعلم_الانجليزية", description)

    def test_manual_row_has_no_hyperframes_requirement(self):
        row = notion_row("Visual Vocabulary", "https://drive.google.com/file/d/manual/view")
        props = row["properties"]
        self.assertEqual(props["Video Type"]["select"]["name"], "Visual Vocabulary")
        self.assertTrue(props["Lien Video"]["url"])
        self.assertNotIn("Image HyperFrames", props)

    def test_error_sanitization_removes_credentials(self):
        cleaned = sanitize_error("Authorization: Bearer secret-value Apikey another-secret")
        self.assertNotIn("secret-value", cleaned)
        self.assertNotIn("another-secret", cleaned)


if __name__ == "__main__":
    unittest.main()
