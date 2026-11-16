import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

import httplib2
from googleapiclient.errors import HttpError

# src.config requires these environment variables at import time, so set dummy values before importing
for _key in ("GOOGLE_TOKEN", "DISCORD_TOKEN", "DISCORD_CHANNEL_ID"):
    os.environ.setdefault(_key, "dummy")

from src import __main__ as app

SYNTHETIC_KEY = "DUMMY-KEY-123"
REQUEST_URI = (
    "https://www.googleapis.com/youtube/v3/playlists"
    "?part=snippet&id=PL1&key=" + SYNTHETIC_KEY + "&alt=json"
)
LOGGER_NAME = "youtube-playlist-video-checker"


def http_error(body, status=403):
    return HttpError(httplib2.Response({"status": status}), body, uri=REQUEST_URI)


def quota_error():
    return http_error(
        json.dumps(
            {
                "error": {
                    "message": "bad key=" + SYNTHETIC_KEY,
                    "errors": [{"reason": "quotaExceeded"}],
                }
            }
        ).encode()
    )


def reason_error(reason):
    return http_error(
        json.dumps({"error": {"message": "m", "errors": [{"reason": reason}]}}).encode()
    )


class TestMainYoutubeError(unittest.TestCase):
    def setUp(self):
        original_cwd = os.getcwd()
        self.addCleanup(os.chdir, original_cwd)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        os.chdir(tmp.name)
        with open("playlists.json", "w") as f:
            json.dump(["PL1"], f)

    def seed_already(self):
        with open("already.json", "w") as f:
            f.write('{"PL1": ["old"]}')

    def read_already_bytes(self):
        with open("already.json", "rb") as f:
            return f.read()

    def youtube(self, playlists_error=None):
        youtube = mock.MagicMock()
        execute = youtube.playlists.return_value.list.return_value.execute
        if playlists_error is not None:
            execute.side_effect = playlists_error
        else:
            execute.return_value = {
                "items": [{"snippet": {"title": "t", "channelTitle": "c"}}]
            }
        return youtube

    def run_main_expecting_exit(self, youtube, **patches):
        stderr = io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(app, "build", return_value=youtube))
            for name, replacement in patches.items():
                stack.enter_context(mock.patch.object(app, name, replacement))
            stack.enter_context(contextlib.redirect_stderr(stderr))
            logs = stack.enter_context(self.assertLogs(LOGGER_NAME, "ERROR"))
            raised = stack.enter_context(self.assertRaises(SystemExit))
            app.main()
        return raised.exception, logs, stderr.getvalue()

    def assert_credential_free_exit(self, exit_, logs, stderr, message):
        self.assertEqual(exit_.code, 1)
        self.assertIsNone(exit_.__context__)
        self.assertIsNone(exit_.__cause__)
        self.assertEqual([record.getMessage() for record in logs.records], [message])
        self.assertNotIn(SYNTHETIC_KEY, logs.output[0])
        self.assertNotIn(SYNTHETIC_KEY, stderr)

    def test_playlists_list_error_logs_status_and_reason_only(self):
        exit_, logs, stderr = self.run_main_expecting_exit(
            self.youtube(playlists_error=quota_error())
        )

        self.assert_credential_free_exit(
            exit_,
            logs,
            stderr,
            "YouTube API request failed: HTTP 403, reason: quotaExceeded",
        )

    def test_playlist_items_error_is_handled_the_same(self):
        exit_, logs, stderr = self.run_main_expecting_exit(
            self.youtube(),
            get_playlist_items=mock.Mock(side_effect=quota_error()),
        )

        self.assert_credential_free_exit(
            exit_,
            logs,
            stderr,
            "YouTube API request failed: HTTP 403, reason: quotaExceeded",
        )

    def test_videos_details_error_is_handled_the_same(self):
        new_video = {
            "videoOwnerChannelTitle": "uploader",
            "resourceId": {"videoId": "v1"},
            "title": "title",
            "publishedAt": "2024-01-01T00:00:00Z",
        }
        exit_, logs, stderr = self.run_main_expecting_exit(
            self.youtube(),
            get_playlist_items=mock.Mock(return_value=[new_video]),
            get_videos_details=mock.Mock(side_effect=quota_error()),
        )

        self.assert_credential_free_exit(
            exit_,
            logs,
            stderr,
            "YouTube API request failed: HTTP 403, reason: quotaExceeded",
        )

    def test_unparsable_body_logs_status_only(self):
        for body in (b"{}", b"not json"):
            with self.subTest(body=body):
                exit_, logs, stderr = self.run_main_expecting_exit(
                    self.youtube(playlists_error=http_error(body))
                )

                self.assert_credential_free_exit(
                    exit_, logs, stderr, "YouTube API request failed: HTTP 403"
                )

    def test_reason_outside_allowlist_is_not_logged(self):
        exit_, logs, stderr = self.run_main_expecting_exit(
            self.youtube(playlists_error=reason_error("bad reason\n" + SYNTHETIC_KEY))
        )

        self.assert_credential_free_exit(
            exit_, logs, stderr, "YouTube API request failed: HTTP 403"
        )

    def test_already_json_is_left_unchanged(self):
        self.seed_already()
        before = self.read_already_bytes()

        self.run_main_expecting_exit(self.youtube(playlists_error=quota_error()))

        self.assertEqual(self.read_already_bytes(), before)

    def test_already_json_is_not_created(self):
        self.run_main_expecting_exit(self.youtube(playlists_error=quota_error()))

        self.assertFalse(os.path.exists("already.json"))


if __name__ == "__main__":
    unittest.main()
