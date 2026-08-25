import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import base64

from src.discovery.discovery import DiscoveryEngine, YT_CHANNEL_RE
from src.sources.search_engine import BING_RESULT_RE, decode_bing_redirect


def _bing_wrap(target):
    token = base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
    return f"https://www.bing.com/ck/a?!&&p=abc123&u=a1{token}&ntb=1"


class TestBingRedirectDecoding:
    def test_decodes_to_real_url(self):
        wrapped = _bing_wrap("https://www.tiktok.com/@truckerbob?lang=en")
        assert decode_bing_redirect(wrapped) == "https://www.tiktok.com/@truckerbob?lang=en"

    def test_passthrough_plain_urls(self):
        assert decode_bing_redirect("https://www.tiktok.com/@x") == "https://www.tiktok.com/@x"

    def test_unescapes_html_entities(self):
        token = base64.urlsafe_b64encode("https://example.com/a?b=1&c=2".encode()).decode().rstrip("=")
        wrapped = f"https://www.bing.com/ck/a?!&&p=x&u=a1{token}".replace("&", "&amp;")
        assert decode_bing_redirect(wrapped).startswith("https://example.com")

    def test_garbage_token_returns_original(self):
        url = "https://www.bing.com/ck/a?u=a1%%%%"
        assert decode_bing_redirect(url) == url


class TestBingResultParsing:
    HTML = """
    <li class="b_algo"><h2><a href="%s">Big Rig Sally on TikTok</a></h2></li>
    <li class="b_algo"><h2><a href="https://www.tiktok.com/@flatfred">Flat Fred</a></h2></li>
    """

    def test_extracts_and_decodes(self):
        wrapped = _bing_wrap("https://www.tiktok.com/@sallyrigs")
        html = self.HTML % wrapped
        pairs = BING_RESULT_RE.findall(html)
        urls = [decode_bing_redirect(u) for u, t in pairs]
        assert "https://www.tiktok.com/@sallyrigs" in urls
        assert "https://www.tiktok.com/@flatfred" in urls


class TestYouTubeChannelExtraction:
    def test_channel_regex_matches_handles(self):
        m = YT_CHANNEL_RE.search("https://www.youtube.com/@MuthaTrucker?feature=share")
        assert m and m.group(1) == "@MuthaTrucker"

    def test_channel_regex_matches_channel_id(self):
        m = YT_CHANNEL_RE.search("https://www.youtube.com/channel/UCabc123def")
        assert m and m.group(1) == "channel/UCabc123def"

    def test_channels_in_results_dedupes(self):
        recs = [
            {"url": "https://www.youtube.com/@TruckerA?v=x", "title": "A"},
            {"url": "https://www.youtube.com/@TruckerA", "title": "dup"},
            {"url": "https://www.youtube.com/watch?v=zzz", "title": "video"},
        ]
        out = DiscoveryEngine._channels_in(recs)
        assert len(out) == 1
        assert "@TruckerA" in out[0]["url"]


class TestTagPageParsing:
    def test_parses_unique_ids(self):
        from src.sources.tiktok import parse_tag_users_from_html
        html = '{"uniqueId":"small_trucker_ca","nickname":"Fresno Hauler"} {"uniqueId":"cdl.journey"}'
        users = parse_tag_users_from_html(html)
        assert "small_trucker_ca" in users
        assert "cdl.journey" in users

    def test_parses_href_links(self):
        from src.sources.tiktok import parse_tag_users_from_html
        html = '<a href="/@flatbedfreddy?lang=en">x</a>'
        assert "flatbedfreddy" in parse_tag_users_from_html(html)

    def test_dedupes_and_cleans(self):
        from src.sources.tiktok import parse_tag_users_from_html
        html = '"uniqueId":"BobTrucks" "uniqueId":"bobtrucks" <a href="/@bobtrucks">'
        users = parse_tag_users_from_html(html)
        assert users.count("bobtrucks") == 1

    def test_empty_html(self):
        from src.sources.tiktok import parse_tag_users_from_html
        assert parse_tag_users_from_html("") == []
        assert parse_tag_users_from_html(None) == []

    def test_engine_has_tag_list(self):
        from src.discovery.discovery import DiscoveryEngine
        from src.utils.config import load_keywords, load_settings
        eng = DiscoveryEngine(load_settings(), load_keywords(), [], None)
        tags = eng.tag_list()
        assert len(tags) > 5
        assert any("truck" in t or "cdl" in t for t in tags)


class TestUnreachableMarking:
    def test_unreachable_error_kind(self):
        from src.browser.exceptions import UnreachableSourceError
        err = UnreachableSourceError("tiktok_web", "https://x", "conn timed out")
        assert err.kind == "unreachable"
        assert err.source == "tiktok_web"

    def test_disabled_source_marks_unknown_in_db(self, tmp_path):
        from src.database.db import Database
        from src.scoring.scoring import compute_scores
        from src.utils.config import load_settings
        db = Database(str(tmp_path / "unreach.sqlite3"))
        cid, _ = db.upsert_candidate_discovery("ghost", "https://www.tiktok.com/@ghost", run_id="r1")
        fields = {
            "activity_status": "UNKNOWN", "activity_source": "source_unavailable",
            "trucking_relevance": "UNKNOWN", "california_relevance": "UNKNOWN",
            "engagement_status": "UNKNOWN", "follower_status": "UNKNOWN",
            "verification_status": "done_no_data",
        }
        row = dict(fields); row["_source_count"] = 1
        m, c, b = compute_scores(row, load_settings())
        assert m == 0.0 and c < 40
        db.update_candidate(cid, **fields)
        check = db.one("SELECT verification_status FROM candidates WHERE id=?", (cid,))
        assert check["verification_status"] == "done_no_data"
        pending_after = db.pending_candidates("r1")
        assert len(pending_after) == 0
        db.close()
