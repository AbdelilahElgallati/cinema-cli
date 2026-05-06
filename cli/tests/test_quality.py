"""Unit tests for video quality selection and mpv/yt-dlp argument building.

Run with:
    cd d:\\My_Projects\\cinema-cli\\cli
    python -m pytest tests/test_quality.py -v
"""
import os
import sys

import pytest

# Ensure the src package is importable from the cli directory
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.utils.source_strategy import (
    build_quality_menu_options,
    canonicalize_quality,
    filter_sources_for_quality,
    quality_sort_key,
    sort_manifest_qualities,
    adaptive_quality_from_speed,
)
from src.utils.player import _quality_to_ytdl_format, _build_mpv_args


# ─── _quality_to_ytdl_format ─────────────────────────────────────────────────

class TestQualityToYtdlFormat:
    def test_1080p(self):
        result = _quality_to_ytdl_format("1080p")
        assert result == "bestvideo[height<=1080]+bestaudio/best[height<=1080]"

    def test_720p(self):
        result = _quality_to_ytdl_format("720p")
        assert result == "bestvideo[height<=720]+bestaudio/best[height<=720]"

    def test_480p(self):
        result = _quality_to_ytdl_format("480p")
        assert result == "bestvideo[height<=480]+bestaudio/best[height<=480]"

    def test_360p(self):
        result = _quality_to_ytdl_format("360p")
        assert result == "bestvideo[height<=360]+bestaudio/best[height<=360]"

    def test_240p(self):
        """240p should work for bandwidth-limited users."""
        result = _quality_to_ytdl_format("240p")
        assert result == "bestvideo[height<=240]+bestaudio/best[height<=240]"

    def test_4k(self):
        result = _quality_to_ytdl_format("4k")
        assert result == "bestvideo[height<=2160]+bestaudio/best[height<=2160]"

    def test_auto_returns_none(self):
        assert _quality_to_ytdl_format("auto") is None

    def test_best_returns_none(self):
        assert _quality_to_ytdl_format("best") is None

    def test_none_returns_none(self):
        assert _quality_to_ytdl_format(None) is None

    def test_empty_returns_none(self):
        assert _quality_to_ytdl_format("") is None

    def test_raw_height_number(self):
        result = _quality_to_ytdl_format("1080")
        assert "1080" in result


# ─── _build_mpv_args ─────────────────────────────────────────────────────────

class TestBuildMpvArgs:
    """Check that key flags are always present in the built argument list."""

    _DUMMY_URL = "https://example.com/stream/index.m3u8"
    _TITLE = "Test Movie"

    def _build(self, quality=None, use_ytdl=False, headers=None):
        return _build_mpv_args(
            self._DUMMY_URL,
            self._TITLE,
            headers,
            [],  # sub_paths
            "ar",
            0,  # start_time
            use_ytdl=use_ytdl,
            quality=quality,
        )

    def test_hls_bitrate_max_always_present(self):
        """--hls-bitrate=max must be in args for both direct and yt-dlp modes."""
        args_direct = self._build(use_ytdl=False)
        args_ytdl = self._build(use_ytdl=True)
        assert "--hls-bitrate=max" in args_direct, "Missing --hls-bitrate=max in direct mode"
        assert "--hls-bitrate=max" in args_ytdl, "Missing --hls-bitrate=max in yt-dlp mode"

    def test_ytdl_format_auto_mode_uses_format_sort(self):
        """Without a locked quality, yt-dlp should use format-sort=res,fps."""
        args = self._build(use_ytdl=True, quality=None)
        assert any("format-sort=res,fps" in a for a in args), (
            "format-sort hint missing from auto yt-dlp mode"
        )
        assert "--ytdl-format=bestvideo+bestaudio/best" in args

    def test_ytdl_format_locked_quality(self):
        """Locked quality should produce a height-capped format selector."""
        args = self._build(use_ytdl=True, quality="720p")
        matched = [a for a in args if "--ytdl-format=" in a]
        assert matched, "No --ytdl-format flag found for locked quality"
        assert "720" in matched[0]
        # format-sort should NOT be added when quality is locked
        assert not any("format-sort" in a for a in args), (
            "format-sort should not be present when quality is locked"
        )

    def test_title_present(self):
        args = self._build()
        assert f"--title={self._TITLE}" in args

    def test_url_is_second_arg(self):
        args = self._build(use_ytdl=False)
        assert args[1] == self._DUMMY_URL

    def test_ytdl_flag_inserted_before_url(self):
        args = self._build(use_ytdl=True)
        assert "--ytdl" in args
        ytdl_idx = args.index("--ytdl")
        url_idx = args.index(self._DUMMY_URL)
        assert ytdl_idx < url_idx


# ─── source_strategy ─────────────────────────────────────────────────────────

class TestSourceStrategy:
    def test_build_quality_menu_has_auto_option(self):
        options = build_quality_menu_options([])
        values = [o["value"] for o in options]
        assert "auto" in values

    def test_build_quality_menu_without_tags_only_auto(self):
        """When no quality tags exist, do not show synthetic quality choices."""
        options = build_quality_menu_options([])
        values = [o["value"] for o in options]
        assert values == ["auto"]

    def test_build_quality_menu_uses_real_tags(self):
        files = [
            {"file": "https://a.m3u8", "quality": "1080p"},
            {"file": "https://b.m3u8", "quality": "720p"},
        ]
        options = build_quality_menu_options(files)
        values = [o["value"] for o in options]
        assert "1080p" in values
        assert "720p" in values
        # 480p should NOT be in values since it wasn't in the source list
        assert "480p" not in values

    def test_filter_sources_exact_match(self):
        files = [
            {"file": "https://a.m3u8", "quality": "1080p"},
            {"file": "https://b.m3u8", "quality": "720p"},
        ]
        filtered, mode = filter_sources_for_quality(files, "1080p")
        assert mode == "ok_exact"
        assert len(filtered) == 1
        assert filtered[0]["quality"] == "1080p"

    def test_filter_sources_auto_returns_all(self):
        files = [{"file": "https://a.m3u8", "quality": "720p"}]
        filtered, mode = filter_sources_for_quality(files, "auto")
        assert mode == "auto"
        assert filtered == files

    def test_filter_sources_fallback_tagged(self):
        files = [{"file": "https://a.m3u8", "quality": "360p"}]
        filtered, mode = filter_sources_for_quality(files, "1080p")
        assert mode == "fallback_tagged"
        assert filtered == files

    def test_filter_sources_handles_provider_quality_aliases(self):
        files = [{"file": "https://a.m3u8", "quality": "astra"}]
        filtered, mode = filter_sources_for_quality(files, "1080p")
        assert mode == "ok_exact"
        assert filtered == files

    def test_canonicalize_quality_non_standard_tags(self):
        assert canonicalize_quality("hd") == "1080p"
        assert canonicalize_quality("fhd") == "1080p"
        assert canonicalize_quality("sd") == "480p"
        assert canonicalize_quality("low") == "360p"
        assert canonicalize_quality("auto") == "auto"

    def test_filter_sources_enforced_manifest(self):
        """When no quality tags exist, all files should be returned for manifest enforcement."""
        files = [{"file": "https://a.m3u8"}]
        filtered, mode = filter_sources_for_quality(files, "720p")
        assert mode == "enforced_manifest"
        assert filtered == files

    def test_sort_manifest_qualities_order(self):
        files = [
            {"quality": "480p"},
            {"quality": "1080p"},
            {"quality": "720p"},
            {"quality": "240p"},
        ]
        result = sort_manifest_qualities(files)
        assert result == ["1080p", "720p", "480p", "240p"]

    def test_quality_sort_key_240p(self):
        assert quality_sort_key("240p") == 5

    def test_adaptive_quality_from_speed_high(self):
        assert adaptive_quality_from_speed(25) == "1080p"

    def test_adaptive_quality_from_speed_medium(self):
        assert adaptive_quality_from_speed(10) == "720p"

    def test_adaptive_quality_from_speed_low(self):
        assert adaptive_quality_from_speed(4) == "480p"

    def test_adaptive_quality_from_speed_very_low(self):
        assert adaptive_quality_from_speed(1) == "360p"
