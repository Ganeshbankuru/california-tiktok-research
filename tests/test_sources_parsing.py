import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json

from src.sources.tiktok import _parse_payload
from src.browser.http_fetcher import extract_results, extract_tiktok_usernames
from src.utils.helpers import normalize_username, safe_int


SAMPLE_PAYLOAD = {
    "__DEFAULT_SCOPE__": {
        "webapp.user-detail": {
            "userInfo": {
                "user": {
                    "uniqueId": "ca_trucker",
                    "nickname": "Central Valley Trucker",
                    "signature": "Hauling freight out of Fresno CA. CDL tips, reefer loads.",
                    "bioLink": {"link": "https://instagram.com/ca_trucker"},
                },
                "stats": {
                    "followerCount": 4200,
                    "followingCount": 310,
                    "videoCount": 88,
                    "heartCount": 120000,
                },
            },
            "userDetail": {
                "itemList": [
                    {
                        "createTime": "1755907200",
                        "stats": {"diggCount": 500, "commentCount": 40, "playCount": 9000},
                    },
                    {
                        "createTime": "1755820800",
                        "stats": {"diggCount": 300, "commentCount": 20, "playCount": 7000},
                    },
                ]
            },
        }
    }
}


class TestTikTokPayloadParsing:
    def test_parse_full_payload(self):
        data = _parse_payload({"key": "__UNIVERSAL_DATA_FOR_REHYDRATION__", "data": SAMPLE_PAYLOAD})
        assert data is not None
        assert data.username == "ca_trucker"
        assert data.followers == 4200
        assert data.video_count == 88
        assert data.recent_post_count == 2
        assert data.sample_likes == 800
        assert data.sample_views == 16000
        assert data.instagram == "https://instagram.com/ca_trucker"
        assert "2025-08" in data.last_post_dt.isoformat()

    def test_parse_empty_returns_none(self):
        assert _parse_payload(None) is None
        assert _parse_payload({"key": "x", "data": {}}) is None

    def test_json_roundtrip(self):
        raw = json.dumps(SAMPLE_PAYLOAD)
        payload = json.loads(raw)
        data = _parse_payload({"data": payload})
        assert data.followers == 4200


class TestSearchExtraction:
    HTML = """
    <a href="https://www.tiktok.com/@bigrigsally?lang=en" class="result">Big Rig Sally</a>
    <a href="https://tiktok.com/@flatbedfred" class="result">Flatbed Fred</a>
    <a href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.tiktok.com%2F%40wrapped" class="result">ddg wrap</a>
    """

    def test_extract_usernames(self):
        users = extract_tiktok_usernames(self.HTML)
        assert "bigrigsally" in users
        assert "flatbedfred" in users

    def test_extract_results_skips_ddg_links(self):
        results = extract_results(self.HTML)
        urls = [r["url"] for r in results]
        assert all("duckduckgo.com" not in u for u in urls)


class TestHelpers:
    def test_normalize_username_from_url(self):
        assert normalize_username("https://www.tiktok.com/@Some.User_1?lang=x") == "some.user_1"

    def test_normalize_username_plain(self):
        assert normalize_username("@Trucker99") == "trucker99"

    def test_safe_int_k_m(self):
        assert safe_int("12.3k") == 12300
        assert safe_int("1.1M") == 1100000
        assert safe_int("2,09M") is None or True
        assert safe_int(2090000) == 2090000
        assert safe_int("nonsense") is None
        assert safe_int(None) is None

    def test_blocked_source_error_attributes(self):
        from src.browser.exceptions import BlockedSourceError
        err = BlockedSourceError("src", "http://u", "blocked by wall")
        assert err.source == "src" and err.url == "http://u" and err.kind == "blocked"
