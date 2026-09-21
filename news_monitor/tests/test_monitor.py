import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import monitor


class ParseDynamicTests(unittest.TestCase):
    def test_parses_opus_dynamic(self):
        item = {
            "id_str": "987654321",
            "type": "DYNAMIC_TYPE_DRAW",
            "modules": {
                "module_author": {
                    "mid": 1197454103,
                    "name": "重返未来：1999",
                    "pub_ts": 1789975200,
                },
                "module_dynamic": {
                    "desc": {"text": "动态正文"},
                    "major": {
                        "type": "MAJOR_TYPE_OPUS",
                        "opus": {
                            "title": "动态标题",
                            "summary": {"text": "摘要"},
                            "pics": [{"url": "https://example.test/a.jpg"}],
                        },
                    },
                },
            },
        }
        parsed = monitor.parse_dynamic(item)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed["dynamic_id"], "987654321")
        self.assertEqual(parsed["author_uid"], "1197454103")
        self.assertEqual(parsed["title"], "动态标题")
        self.assertEqual(parsed["text"], "动态正文")
        self.assertEqual(parsed["cover_urls"], ["https://example.test/a.jpg"])
        self.assertEqual(
            parsed["url"], "https://www.bilibili.com/opus/987654321"
        )

    def test_parses_archive_when_description_is_missing(self):
        item = {
            "id_str": "123",
            "type": "DYNAMIC_TYPE_AV",
            "modules": {
                "module_author": {"pub_ts": 1789975200},
                "module_dynamic": {
                    "major": {
                        "type": "MAJOR_TYPE_ARCHIVE",
                        "archive": {
                            "title": "视频标题",
                            "desc": "视频简介",
                            "cover": "https://example.test/video.jpg",
                        },
                    }
                },
            },
        }
        parsed = monitor.parse_dynamic(item)
        assert parsed is not None
        self.assertEqual(parsed["title"], "视频标题")
        self.assertEqual(parsed["text"], "视频简介")

    def test_parses_repost_original(self):
        original = {
            "id_str": "100",
            "modules": {
                "module_author": {"name": "原作者", "pub_ts": 1789975100},
                "module_dynamic": {"desc": {"text": "原动态"}},
            },
        }
        repost = {
            "id_str": "200",
            "type": "DYNAMIC_TYPE_FORWARD",
            "orig": original,
            "modules": {
                "module_author": {"name": "转发者", "pub_ts": 1789975200},
                "module_dynamic": {"desc": {"text": "转发语"}},
            },
        }
        parsed = monitor.parse_dynamic(repost)
        assert parsed is not None
        self.assertTrue(parsed["is_repost"])
        self.assertEqual(parsed["original"]["text"], "原动态")


class ConfigurationTests(unittest.TestCase):
    def test_validates_and_deduplicates_users(self):
        config = monitor.validate_config(
            {
                "users": [
                    {"name": "A", "uid": "123"},
                    {"name": "duplicate", "uid": 123},
                ]
            }
        )
        self.assertEqual(config["users"], [{"name": "A", "uid": "123"}])

    def test_load_env_preserves_equals_signs(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ".env"
            path.write_text("BILI_COOKIE=SESSDATA=a=b; bili_jct=c;\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                monitor.load_env_file(path)
                self.assertEqual(
                    os.environ["BILI_COOKIE"], "SESSDATA=a=b; bili_jct=c;"
                )

    def test_parse_since_clock_uses_shanghai_day(self):
        now = datetime(2026, 9, 21, 20, 0, tzinfo=monitor.SHANGHAI_TZ)
        since = monitor.parse_since("08:00", now, 48)
        self.assertEqual(since.isoformat(), "2026-09-21T08:00:00+08:00")


class ReportTests(unittest.TestCase):
    def test_pinned_old_item_does_not_stop_pagination(self):
        now = datetime(2026, 9, 21, 20, 0, tzinfo=monitor.SHANGHAI_TZ)
        since = datetime(2026, 9, 21, 8, 0, tzinfo=monitor.SHANGHAI_TZ)

        def item(dynamic_id, published):
            return {
                "id_str": dynamic_id,
                "modules": {
                    "module_author": {"pub_ts": int(published.timestamp())},
                    "module_dynamic": {"desc": {"text": dynamic_id}},
                },
            }

        responses = [
            {
                "data": {
                    "items": [
                        item("pinned-old", datetime(2025, 1, 1, tzinfo=monitor.SHANGHAI_TZ)),
                        item("recent-1", now),
                    ],
                    "has_more": True,
                    "offset": "next-page",
                }
            },
            {
                "data": {
                    "items": [item("recent-2", now)],
                    "has_more": False,
                    "offset": "",
                }
            },
        ]
        options = {
            "max_pages": 3,
            "request_interval_seconds": 0,
            "timeout_seconds": 20,
            "retries": 1,
        }
        with patch.object(monitor, "_request_json", side_effect=responses), patch.object(
            monitor.time, "sleep"
        ):
            result = monitor.fetch_account(
                {"name": "test", "uid": "123"}, options, "", since
            )
        self.assertEqual(
            {entry["dynamic_id"] for entry in result}, {"recent-1", "recent-2"}
        )

    def test_mark_new_items_uses_previous_state(self):
        report = {
            "accounts": [
                {
                    "uid": "123",
                    "items": [
                        {"dynamic_id": "old"},
                        {"dynamic_id": "new"},
                    ],
                }
            ]
        }
        total = monitor.mark_new_items(report, {"123": {"old"}})
        self.assertEqual(total, 1)
        self.assertFalse(report["accounts"][0]["items"][0]["is_new"])
        self.assertTrue(report["accounts"][0]["items"][1]["is_new"])

    def test_atomic_json_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "result.json"
            monitor.write_json_atomic(path, {"text": "中文"})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["text"], "中文")


if __name__ == "__main__":
    unittest.main()
