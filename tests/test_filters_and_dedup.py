import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from src.filters.filters import (
    CaliforniaClassifier,
    TruckingClassifier,
    classify_activity,
    classify_followers,
)
from src.utils.config import load_keywords, load_settings
from src.utils.helpers import parse_date


@pytest.fixture(scope="module")
def keywords():
    return load_keywords()


@pytest.fixture(scope="module")
def settings():
    return load_settings()


@pytest.fixture(scope="module")
def ca_classifier(keywords):
    return CaliforniaClassifier(
        keywords["california_terms_strong"],
        keywords.get("california_terms_weak") or {},
        operational_patterns=keywords.get("california_operational_patterns") or {},
    )


@pytest.fixture(scope="module")
def tr_classifier(keywords, settings):
    rel = settings.get("relevance", {})
    return TruckingClassifier(
        topics=keywords.get("topics", {}),
        core_terms=keywords.get("core_terms", []),
        strong_terms=keywords.get("strong_topic_terms", []),
        high_min_strong=rel.get("trucking_high_min_strong_terms", 2),
        medium_min_terms=rel.get("trucking_medium_min_terms", 1),
    )


class TestFollowerFilter:
    def test_qualified_lower_bound(self):
        assert classify_followers(2000) == "QUALIFIED"

    def test_qualified_upper_bound(self):
        assert classify_followers(9999) == "QUALIFIED"

    def test_too_small(self):
        assert classify_followers(1999) == "TOO_SMALL"

    def test_too_large(self):
        assert classify_followers(10000) == "TOO_LARGE"

    def test_unknown_when_none(self):
        assert classify_followers(None) == "UNKNOWN"

    def test_unknown_when_garbage(self):
        assert classify_followers("abc") == "UNKNOWN"


class TestActivityClassification:
    def _dt(self, days_ago):
        from datetime import datetime, timedelta, timezone
        return datetime.now(timezone.utc) - timedelta(days=days_ago)

    def test_active(self):
        status, src = classify_activity(self._dt(30))
        assert status == "ACTIVE"
        assert "30" in src

    def test_recent(self):
        assert classify_activity(self._dt(75))[0] == "RECENT"

    def test_inactive(self):
        assert classify_activity(self._dt(120))[0] == "INACTIVE"

    def test_boundary_active(self):
        assert classify_activity(self._dt(60))[0] == "ACTIVE"

    def test_boundary_recent(self):
        assert classify_activity(self._dt(90))[0] == "RECENT"

    def test_unknown_no_date(self):
        status, src = classify_activity(None)
        assert status == "UNKNOWN"

    def test_parse_date_never_invented(self):
        assert parse_date("") is None
        assert parse_date("not a date") is None
        d = parse_date("2026-08-01")
        assert d is not None and d.year == 2026


class TestCaliforniaClassification:
    def test_high_city_mention(self, ca_classifier):
        level, evidence, hits = ca_classifier.classify(["Trucking out of Fresno CA. Central Valley runs daily."])
        assert level == "HIGH"
        assert "Fresno" in evidence

    def test_medium_single_weak_ca(self, ca_classifier):
        level, evidence, hits = ca_classifier.classify(["Driver living in CA"])
        assert level in ("HIGH", "MEDIUM")
        assert level != "UNKNOWN"

    def test_ca_word_boundary_not_canada(self, ca_classifier):
        level, _, hits = ca_classifier.classify(["I live in Canada driving trucks every day on the road hauling freight loads"])
        assert level == "LOW"
        assert not any(h.upper() == "CA" for h in hits)

    def test_ca_abbreviation_matches(self, ca_classifier):
        level, evidence, _ = ca_classifier.classify(["Truck driver based in Sacramento, CA hauling loads"])
        assert level == "HIGH"
        assert "CA" in evidence

    def test_socal_is_evidence(self, ca_classifier):
        level, evidence, _ = ca_classifier.classify(["SoCal based trucking content weekly"])
        assert level == "HIGH"
        assert "SoCal" in evidence

    def test_no_california_general_us(self, ca_classifier):
        level, evidence, hits = ca_classifier.classify([
            "Just a trucker hauling freight across America. Texas born and raised, love the open road and diesel engines."
        ])
        assert level == "LOW"
        assert evidence == ""

    def test_insufficient_data_unknown(self, ca_classifier):
        level, evidence, hits = ca_classifier.classify([None, ""])
        assert level == "UNKNOWN"

    def test_area_code_is_evidence(self, ca_classifier):
        level, evidence, _ = ca_classifier.classify(["Hauling loads all week. 510 born n raised"])
        assert level == "HIGH"
        assert "510" in evidence

    def test_bayarea_weak_term(self, ca_classifier):
        level, _, _ = ca_classifier.classify(["Trucking content from the Bay, haulin every day on the road"])
        assert level in ("HIGH", "MEDIUM")

    def test_short_text_stays_unknown(self, ca_classifier):
        level, _, _ = ca_classifier.classify(["hi"])
        assert level == "UNKNOWN"


class TestCarbOperatesInCalifornia:
    def test_carb_bio_is_high(self, ca_classifier, tr_classifier):
        bio = "CARB compliant hauler. Truck life daily, OTR vlogs and diesel talk."
        ca_level, evidence, _ = ca_classifier.classify([bio])
        tr_level, topics, _ = tr_classifier.classify([bio])
        assert ca_level == "HIGH"
        assert "carb" in evidence.lower()
        assert "emissions_compliance" in topics

    def test_out_of_state_carrier_running_ca_loads(self, ca_classifier):
        bio = "Texas based owner operator running CA loads weekly. Flatbed and reefer."
        level, evidence, _ = ca_classifier.classify([bio])
        assert level == "HIGH"

    def test_drayage_port_signal(self, ca_classifier, tr_classifier):
        bio = "Drayage driver out of the port of Oakland. Day in the life vlogs."
        ca_level, _, _ = ca_classifier.classify([bio])
        _, topics, _ = tr_classifier.classify([bio])
        assert ca_level == "HIGH"
        assert "ports_drayage" in topics

    def test_west_coast_counts(self, ca_classifier):
        level, _, _ = ca_classifier.classify([
            "West coast trucker vlogging my runs every week on the road hauling freight"
        ])
        assert level == "HIGH"


class TestTruckingClassification:
    def test_high_multiple_strong(self, tr_classifier):
        level, topics, matched = tr_classifier.classify([
            "OTR truck driver, CDL training tips, owner operator life, big rig reviews"
        ])
        assert level == "HIGH"
        assert len(topics) >= 3

    def test_medium_single_topic(self, tr_classifier):
        level, topics, _ = tr_classifier.classify(["Weekend flatbed hauls and tarps"])
        assert level == "MEDIUM"
        assert "flatbed" in topics

    def test_exclude_no_relevance(self, tr_classifier):
        level, topics, _ = tr_classifier.classify(["Makeup tutorials and cooking recipes every day"])
        assert level == "EXCLUDE"
        assert topics == []

    def test_cdl_topic_detected(self, tr_classifier):
        level, topics, _ = tr_classifier.classify(["CDL school graduate sharing truck driving journey"])
        assert "CDL" in topics or "truck_driving" in topics
        assert level in ("HIGH", "MEDIUM")

    def test_empty_text_excluded(self, tr_classifier):
        level, topics, _ = tr_classifier.classify([])
        assert level == "EXCLUDE"


class TestDeduplicationLogic:
    def _fresh_db(self, tmp_path):
        from src.database.db import Database
        return Database(str(tmp_path / "dedup_test.sqlite3"))

    def test_same_username_one_row_two_sources(self, tmp_path):
        db = self._fresh_db(tmp_path)
        cid1, created1 = db.upsert_candidate_discovery(
            "truckerbob", "https://www.tiktok.com/@truckerbob", run_id="run_x"
        )
        cid2, created2 = db.upsert_candidate_discovery(
            "truckerbob", "https://www.tiktok.com/@truckerbob", run_id="run_y"
        )
        assert created1 is True
        assert created2 is False
        assert cid1 == cid2
        db.add_source(cid1, "Alex Nino", "seed_related")
        db.add_source(cid2, "Big Rig Videos", "seed_related")
        sources = db.sources_for(cid1)
        seeds = [s["seed_account"] for s in sources]
        assert len(sources) == 2
        assert set(seeds) == {"Alex Nino", "Big Rig Videos"}
        rows = db.query("SELECT * FROM candidates")
        assert len(rows) == 1
        db.close()

    def test_add_source_idempotent(self, tmp_path):
        db = self._fresh_db(tmp_path)
        cid, _ = db.upsert_candidate_discovery("x", "https://www.tiktok.com/@x")
        s1 = db.add_source(cid, None, "hashtag", source_url="u1")
        s2 = db.add_source(cid, None, "hashtag", source_url="u1")
        assert s1 == s2
        assert len(db.sources_for(cid)) == 1
        db.close()
