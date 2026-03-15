import unittest

from src.utils.source_strategy import (
    adaptive_quality_from_speed,
    filter_sources_for_quality,
    sort_manifest_qualities,
)


class SourceStrategyTests(unittest.TestCase):
    def test_sort_manifest_qualities(self):
        files = [
            {"quality": "720p"},
            {"quality": "1080p"},
            {"quality": "4k"},
            {"quality": "480p"},
        ]
        self.assertEqual(sort_manifest_qualities(files), ["4k", "1080p", "720p", "480p"])

    def test_filter_sources_exact_quality(self):
        files = [{"quality": "1080p"}, {"quality": "720p"}]
        filtered, mode = filter_sources_for_quality(files, "720p")
        self.assertEqual(mode, "ok_exact")
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["quality"], "720p")

    def test_filter_sources_unavailable_tagged(self):
        files = [{"quality": "1080p"}, {"quality": "720p"}]
        filtered, mode = filter_sources_for_quality(files, "480p")
        self.assertEqual(mode, "unavailable_tagged")
        self.assertEqual(filtered, [])

    def test_filter_sources_manifest_enforced(self):
        files = [{"file": "https://example.com/master.m3u8"}]
        filtered, mode = filter_sources_for_quality(files, "720p")
        self.assertEqual(mode, "enforced_manifest")
        self.assertEqual(len(filtered), 1)

    def test_adaptive_mapping(self):
        self.assertEqual(adaptive_quality_from_speed(25), "1080p")
        self.assertEqual(adaptive_quality_from_speed(10), "720p")
        self.assertEqual(adaptive_quality_from_speed(4), "480p")
        self.assertEqual(adaptive_quality_from_speed(1), "360p")


if __name__ == "__main__":
    unittest.main()
