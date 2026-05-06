import os
import sys
import tempfile
import unittest
from unittest.mock import patch

# Add cli directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.utils.download_manager import DownloadManager
from src.utils.player import (
    _background_subtitle_handler,
    _prepare_subtitles,
    _subtitle_result_to_temp_file,
)
from src.utils.subtitles import fetch_subtitles


class _FakeResponse:
    def __init__(self, content, headers=None):
        self.content = content
        self.headers = headers or {"content-type": "application/x-subrip"}

    def raise_for_status(self):
        return None


class TestSubtitleFallbackFlow(unittest.TestCase):
    def test_fetch_subtitles_uses_subdl_for_missing_langs(self):
        with (
            patch("src.utils.subtitles._get_api_key", return_value="os-key"),
            patch("src.utils.subtitles._get_subdl_key", return_value="subdl-key"),
            patch("src.utils.subtitles._fetch_from_opensubtitles", return_value=[]) as os_fetch,
            patch(
                "src.utils.subtitles.fetch_subtitles_subdl",
                return_value=[
                    {
                        "lang": "en",
                        "ext": "srt",
                        "content": b"1\n00:00:00,000 --> 00:00:01,000\nHello\n",
                    }
                ],
            ) as subdl_fetch,
        ):
            out = fetch_subtitles("Tracker", ["en"], season=3, episode=16, max_per_language=1)

            self.assertEqual(len(out), 1)
            self.assertEqual(out[0]["lang"], "en")
            os_fetch.assert_called_once()
            subdl_fetch.assert_called_once()

    def test_prepare_subtitles_fills_missing_preferred_langs(self):
        provider_sub = {
            "lang": "ar",
            "url": "https://example.com/subs/ar.srt",
        }
        fallback_sub = {
            "lang": "en",
            "ext": "srt",
            "content": b"1\n00:00:00,000 --> 00:00:01,000\nHello\n",
        }

        with (
            patch(
                "src.utils.player.requests.get",
                return_value=_FakeResponse(
                    b"1\n00:00:00,000 --> 00:00:01,000\n\xd9\x85\xd8\xb1\xd8\xad\xd8\xa8\xd8\xa7\n"
                ),
            ),
            patch(
                "src.utils.player.fetch_subtitles", return_value=[fallback_sub]
            ) as fallback_fetch,
            patch("src.utils.player.load_json_data", return_value={}),
        ):
            paths, langs, _ = _prepare_subtitles(
                subtitles=[provider_sub],
                headers={},
                meta={"year": 2024, "season": 3, "episode": 16},
                preferred_sub_lang="ar",
                include_all_subs=True,
                fallback_langs=["ar", "en"],
                preferred_langs=["ar", "en"],
                title="Tracker S3E16 - Struck",
            )

            self.assertIn("ar", langs)
            self.assertIn("en", langs)
            fallback_fetch.assert_called_once()
            called_langs = fallback_fetch.call_args.args[1]
            self.assertEqual(called_langs, ["en"])

            for path in paths:
                if path and os.path.exists(path):
                    os.remove(path)

    def test_download_subtitles_fills_missing_lang_even_with_provider_subs(self):
        manager = DownloadManager(settings={})
        manager._save = lambda *args, **kwargs: None

        task = {
            "title": "Tracker S3E16 - Struck",
            "filename": "Tracker.S03E16.mp4",
            "subtitles": [{"lang": "ar", "url": "https://example.com/subs/ar.srt"}],
            "preferred_sub_lang": "ar",
            "preferred_sub_langs": ["ar", "en"],
            "fallback_sub_langs": ["ar", "en"],
            "include_all_subs": True,
            "headers": {},
            "meta": {"year": 2024, "season": 3, "episode": 16},
        }

        fallback_sub = {
            "lang": "en",
            "ext": "srt",
            "content": b"1\n00:00:00,000 --> 00:00:01,000\nHello\n",
        }

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch(
                "src.utils.download_manager.requests.get",
                return_value=_FakeResponse(
                    b"1\n00:00:00,000 --> 00:00:01,000\n\xd9\x85\xd8\xb1\xd8\xad\xd8\xa8\xd8\xa7\n"
                ),
            ),
            patch(
                "src.utils.download_manager.fetch_subtitles", return_value=[fallback_sub]
            ) as fallback_fetch,
            patch("src.utils.storage.load_json_data", return_value={}),
        ):
            manager._download_subtitles(task, temp_dir)

            self.assertIn("subtitle_files", task)
            langs = [item.get("lang") for item in task["subtitle_files"]]
            self.assertIn("ar", langs)
            self.assertIn("en", langs)

            fallback_fetch.assert_called_once()
            called_langs = fallback_fetch.call_args.args[1]
            self.assertEqual(called_langs, ["en"])

        manager.stop()

    def test_prepare_subtitles_prefers_arabic_over_fallback_when_available(self):
        def _fake_fetch(title, langs, **kwargs):
            out = []
            for lang in langs:
                if lang == "ar":
                    out.append(
                        {
                            "lang": "ar",
                            "ext": "srt",
                            "content": b"1\n00:00:00,000 --> 00:00:01,000\n\xd9\x85\xd8\xb1\xd8\xad\xd8\xa8\xd8\xa7\n",
                        }
                    )
                if lang == "en":
                    out.append(
                        {
                            "lang": "en",
                            "ext": "srt",
                            "content": b"1\n00:00:00,000 --> 00:00:01,000\nHello\n",
                        }
                    )
            return out

        with (
            patch("src.utils.player.requests.get", side_effect=Exception("no provider subtitles")),
            patch(
                "src.utils.player.fetch_subtitles",
                side_effect=_fake_fetch,
            ),
            patch("src.utils.player.load_json_data", return_value={}),
        ):
            paths, langs, _ = _prepare_subtitles(
                subtitles=[],
                headers={},
                meta={"year": 2024, "season": 2, "episode": 8},
                preferred_sub_lang="ar",
                include_all_subs=False,
                fallback_langs=["ar", "en"],
                preferred_langs=["ar"],
                title="Hijack S2E8 - Terminal",
            )

            self.assertTrue(paths)
            self.assertEqual(langs[0], "ar")

            for path in paths:
                if path and os.path.exists(path):
                    os.remove(path)

    def test_temp_subtitle_filename_uses_clear_label(self):
        path = _subtitle_result_to_temp_file(
            {
                "lang": "ar",
                "ext": "srt",
                "content": b"1\n00:00:00,000 --> 00:00:01,000\n\xd9\x85\xd8\xb1\xd8\xad\xd8\xa8\xd8\xa7\n",
            }
        )
        try:
            self.assertIsNotNone(path)
            base = os.path.basename(path)
            self.assertIn("cinema_fallback_ar_", base)
        finally:
            if path and os.path.exists(path):
                os.remove(path)

    def test_background_handler_skips_fallback_when_arabic_already_present(self):
        with (
            patch("src.utils.player.os.path.exists", return_value=True),
            patch("src.utils.player.fetch_subtitles") as fetch_mock,
        ):
            _background_subtitle_handler(
                ipc_path="dummy-ipc",
                title="Hijack S2E8 - Terminal",
                subtitles=[],
                headers={},
                meta={"year": 2024, "season": 2, "episode": 8},
                preferred_sub_lang="ar",
                include_all_subs=False,
                fallback_langs=["ar", "en"],
                preferred_langs=["ar"],
                already_found_paths=set(),
                current_langs=["ar"],
            )

            fetch_mock.assert_not_called()

    def test_prepare_subtitles_fetches_each_missing_language_once(self):
        call_order = []

        def _fake_fetch(title, langs, **kwargs):
            call_order.append(tuple(langs))
            out = []
            for lang in langs:
                if lang == "ar":
                    out.append(
                        {
                            "lang": "ar",
                            "ext": "srt",
                            "content": b"1\n00:00:00,000 --> 00:00:01,000\n\xd9\x85\xd8\xb1\xd8\xad\xd8\xa8\xd8\xa7\n",
                        }
                    )
                if lang == "en":
                    out.append(
                        {
                            "lang": "en",
                            "ext": "srt",
                            "content": b"1\n00:00:00,000 --> 00:00:01,000\nHello\n",
                        }
                    )
                if lang == "fr":
                    out.append(
                        {
                            "lang": "fr",
                            "ext": "srt",
                            "content": b"1\n00:00:00,000 --> 00:00:01,000\nBonjour\n",
                        }
                    )
            return out

        with (
            patch("src.utils.player.requests.get", side_effect=Exception("no provider")),
            patch("src.utils.player.fetch_subtitles", side_effect=_fake_fetch),
            patch("src.utils.player.load_json_data", return_value={}),
        ):
            paths, langs, _ = _prepare_subtitles(
                subtitles=[],
                headers={},
                meta={"year": 2024, "season": 2, "episode": 8},
                preferred_sub_lang="ar",
                include_all_subs=True,
                fallback_langs=["ar", "en", "fr"],
                preferred_langs=["ar", "en", "fr"],
                title="Hijack S2E8 - Terminal",
            )

            self.assertEqual(call_order, [("ar", "en", "fr")])
            self.assertEqual(set(langs), {"ar", "en", "fr"})

            for path in paths:
                if path and os.path.exists(path):
                    os.remove(path)

    def test_download_subtitles_fetches_each_missing_language_once(self):
        manager = DownloadManager(settings={})
        manager._save = lambda *args, **kwargs: None

        task = {
            "title": "Hijack S2E8 - Terminal",
            "filename": "Hijack.S02E08.mp4",
            "subtitles": [],
            "preferred_sub_lang": "ar",
            "preferred_sub_langs": ["ar", "en", "fr"],
            "fallback_sub_langs": ["ar", "en", "fr"],
            "include_all_subs": True,
            "headers": {},
            "meta": {"year": 2024, "season": 2, "episode": 8},
        }

        call_order = []

        def _fake_fetch(title, langs, **kwargs):
            call_order.append(tuple(langs))
            out = []
            for lang in langs:
                if lang == "ar":
                    out.append(
                        {
                            "lang": "ar",
                            "ext": "srt",
                            "content": b"1\n00:00:00,000 --> 00:00:01,000\n\xd9\x85\xd8\xb1\xd8\xad\xd8\xa8\xd8\xa7\n",
                        }
                    )
                if lang == "en":
                    out.append(
                        {
                            "lang": "en",
                            "ext": "srt",
                            "content": b"1\n00:00:00,000 --> 00:00:01,000\nHello\n",
                        }
                    )
                if lang == "fr":
                    out.append(
                        {
                            "lang": "fr",
                            "ext": "srt",
                            "content": b"1\n00:00:00,000 --> 00:00:01,000\nBonjour\n",
                        }
                    )
            return out

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch("src.utils.download_manager.fetch_subtitles", side_effect=_fake_fetch),
            patch("src.utils.storage.load_json_data", return_value={}),
        ):
            manager._download_subtitles(task, temp_dir)

            self.assertEqual(call_order, [("ar", "en", "fr")])
            self.assertIn("subtitle_files", task)
            langs = [item.get("lang") for item in task["subtitle_files"]]
            self.assertEqual(set(langs), {"ar", "en", "fr"})

        manager.stop()


if __name__ == "__main__":
    unittest.main()
