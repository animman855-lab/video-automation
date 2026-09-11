import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hyperframes" / "scripts"))

os.environ.setdefault("NOTION_TOKEN", "test-token")
os.environ.setdefault("NOTION_DATABASE_ID", "test-database")
os.environ.setdefault("UPLOAD_POST_API_KEY", "test-upload-key")

import publish
import publishing_watchdog


class FakeResponse:
    def __init__(self, content_type, chunks, status_code=200):
        self.headers = {"Content-Type": content_type}
        self.status_code = status_code
        self._chunks = chunks
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=32768):
        yield from self._chunks

    def close(self):
        self.closed = True


class VideoLinkValidationTests(unittest.TestCase):
    def test_mp4_content_type_and_payload_are_accepted(self):
        publish.validate_video_payload("video/mp4", b"\x00\x00\x00\x18ftypisom")

    def test_octet_stream_mp4_payload_is_accepted(self):
        publish.validate_video_payload("application/octet-stream", b"\x00\x00\x00\x18ftypisom")

    def test_html_permission_page_is_classified_as_invalid_link(self):
        with self.assertRaises(publish.VideoDownloadError) as error:
            publish.validate_video_payload("text/html; charset=utf-8", b"<html>permission</html>")
        self.assertEqual(error.exception.code, "invalid_video_link_html")

    def test_unknown_content_type_is_rejected(self):
        with self.assertRaises(publish.VideoDownloadError) as error:
            publish.validate_video_payload("application/json", b"{}")
        self.assertEqual(error.exception.code, "invalid_video_content_type")

    def test_malformed_drive_link_is_classified_without_network_access(self):
        with self.assertRaises(publish.VideoDownloadError) as error:
            publish.download_video("https://example.com/not-a-drive-file")
        self.assertEqual(error.exception.code, "invalid_video_link_format")

    @patch.object(publishing_watchdog.requests, "get")
    def test_watchdog_distinguishes_html_drive_link(self, get):
        get.return_value = FakeResponse("text/html", [b"<html>permission</html>"])
        state = publishing_watchdog.inspect_video_link("https://drive.google.com/file/d/abc123/view")
        self.assertEqual(state, "html_permission_or_confirmation_page")

    @patch.object(publishing_watchdog.requests, "get")
    def test_watchdog_accepts_valid_mp4_drive_link(self, get):
        get.return_value = FakeResponse("video/mp4", [b"\x00\x00\x00\x18ftypisom"])
        state = publishing_watchdog.inspect_video_link("https://drive.google.com/file/d/abc123/view")
        self.assertEqual(state, "valid")

    @patch.object(publishing_watchdog.requests, "get", side_effect=requests.Timeout("timeout"))
    def test_watchdog_distinguishes_unreachable_link(self, _get):
        state = publishing_watchdog.inspect_video_link("https://drive.google.com/file/d/abc123/view")
        self.assertEqual(state, "unreachable")

    def test_manual_row_without_link_is_not_classified_as_local_hyperframes(self):
        row = {
            "id": "manual-teacher-1",
            "properties": {
                "Titre": {"type": "title", "title": [{"plain_text": "Manual TeacherRyan"}]},
                "Avatar": {"type": "select", "select": {"name": "teacherryan"}},
                "Video Type": {"type": "select", "select": {"name": "Visual Vocabulary"}},
                "Statut": {"type": "select", "select": {"name": "A publier"}},
                "Date Publication": {"type": "date", "date": {"start": "2026-09-11"}},
                "Slot": {"type": "select", "select": {"name": "08:00"}},
                "Lien Video": {"type": "url", "url": None},
                "Image HyperFrames": {"type": "url", "url": None},
                "Prompt 1": {"type": "rich_text", "rich_text": []},
                "Script": {"type": "rich_text", "rich_text": [{"plain_text": "A script"}]},
                "Plateforme": {"type": "multi_select", "multi_select": [{"name": "YouTube"}]},
            },
        }
        summary = publishing_watchdog.row_summary(
            row,
            datetime(2026, 9, 11, 9, 0, tzinfo=timezone(timedelta(hours=-4))),
        )
        self.assertTrue(any(issue.startswith("Lien Video manquant") for issue in summary["issues"]))


if __name__ == "__main__":
    unittest.main()
