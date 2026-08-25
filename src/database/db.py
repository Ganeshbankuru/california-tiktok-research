import sqlite3
import json
from contextlib import contextmanager

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS research_runs (
    run_id TEXT PRIMARY KEY,
    start_time TEXT NOT NULL,
    end_time TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    limit_candidates INTEGER,
    seed_filter TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS seed_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT,
    name TEXT UNIQUE NOT NULL,
    aliases TEXT,
    platforms TEXT,
    approx_youtube_subscribers INTEGER,
    approx_tiktok_followers INTEGER,
    tiktok_handle TEXT,
    urls TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    display_name TEXT,
    platform TEXT NOT NULL DEFAULT 'tiktok',
    profile_url TEXT UNIQUE NOT NULL,
    followers INTEGER,
    following INTEGER,
    video_count INTEGER,
    bio TEXT,
    recent_post_date TEXT,
    recent_post_count INTEGER,
    activity_status TEXT DEFAULT 'UNKNOWN',
    activity_source TEXT,
    last_post_date TEXT,
    trucking_relevance TEXT DEFAULT 'UNKNOWN',
    trucking_topics TEXT,
    california_relevance TEXT DEFAULT 'UNKNOWN',
    california_evidence TEXT,
    engagement_status TEXT DEFAULT 'UNKNOWN',
    engagement_indicator TEXT,
    public_contact TEXT,
    other_social_links TEXT,
    source_urls TEXT,
    date_checked TEXT,
    follower_status TEXT DEFAULT 'UNKNOWN',
    verification_status TEXT NOT NULL DEFAULT 'pending',
    first_run_id TEXT,
    last_run_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(username, platform)
);
CREATE INDEX IF NOT EXISTS idx_candidates_username ON candidates(username);
CREATE INDEX IF NOT EXISTS idx_candidates_follower_status ON candidates(follower_status);
CREATE INDEX IF NOT EXISTS idx_candidates_verification ON candidates(verification_status);

CREATE TABLE IF NOT EXISTS candidate_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL REFERENCES candidates(id),
    seed_account TEXT,
    discovery_method TEXT NOT NULL,
    source_url TEXT,
    query_used TEXT,
    discovered_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_candidate_sources_candidate ON candidate_sources(candidate_id);
CREATE INDEX IF NOT EXISTS idx_candidate_sources_seed ON candidate_sources(seed_account);

CREATE TABLE IF NOT EXISTS candidate_activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL REFERENCES candidates(id),
    checked_at TEXT NOT NULL,
    last_post_date TEXT,
    post_count_sampled INTEGER,
    activity_status TEXT,
    activity_source TEXT,
    raw_evidence TEXT
);
CREATE INDEX IF NOT EXISTS idx_candidate_activity_candidate ON candidate_activity(candidate_id);

CREATE TABLE IF NOT EXISTS candidate_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL REFERENCES candidates(id),
    marketing_score REAL,
    confidence_score REAL,
    breakdown TEXT,
    scored_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_candidate_scores_candidate ON candidate_scores(candidate_id);

CREATE TABLE IF NOT EXISTS processed_urls (
    url TEXT PRIMARY KEY,
    kind TEXT,
    status TEXT NOT NULL,
    run_id TEXT,
    timestamp TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_processed_urls_run ON processed_urls(run_id);

CREATE TABLE IF NOT EXISTS errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    source TEXT,
    url TEXT,
    error_type TEXT,
    message TEXT,
    timestamp TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_errors_run ON errors(run_id);
"""


class Database:
    def __init__(self, path):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    @contextmanager
    def tx(self):
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def close(self):
        try:
            self.conn.commit()
        except Exception:
            pass
        self.conn.close()

    def execute(self, sql, params=()):
        cur = self.conn.execute(sql, params)
        self.conn.commit()
        return cur

    def query(self, sql, params=()):
        return self.conn.execute(sql, params).fetchall()

    def one(self, sql, params=()):
        return self.conn.execute(sql, params).fetchone()

    def create_run(self, run_id, limit_candidates=None, seed_filter=None, notes=None):
        from ..utils.helpers import iso, now_utc
        self.execute(
            "INSERT INTO research_runs (run_id, start_time, status, limit_candidates, seed_filter, notes) VALUES (?,?,?,?,?,?)",
            (run_id, iso(now_utc()), "running", limit_candidates, seed_filter, notes),
        )

    def finish_run(self, run_id, status="completed"):
        from ..utils.helpers import iso, now_utc
        self.execute(
            "UPDATE research_runs SET end_time=?, status=? WHERE run_id=?",
            (iso(now_utc()), status, run_id),
        )

    def get_run(self, run_id):
        return self.one("SELECT * FROM research_runs WHERE run_id=?", (run_id,))

    def latest_running_run(self):
        return self.one(
            "SELECT * FROM research_runs WHERE status='running' ORDER BY start_time DESC LIMIT 1"
        )

    def latest_run(self):
        return self.one("SELECT * FROM research_runs ORDER BY start_time DESC LIMIT 1")

    def ensure_seeds(self, seed_list):
        from ..utils.helpers import iso, now_utc
        now = iso(now_utc())
        for s in seed_list:
            existing = self.one("SELECT id FROM seed_accounts WHERE name=?", (s["name"],))
            if existing:
                continue
            self.execute(
                "INSERT INTO seed_accounts (name, aliases, platforms, approx_youtube_subscribers, approx_tiktok_followers, tiktok_handle, urls, notes, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    s["name"],
                    json.dumps(s.get("aliases", [])),
                    json.dumps(s.get("platforms", [])),
                    s.get("approx_youtube_subscribers"),
                    s.get("approx_tiktok_followers"),
                    s.get("tiktok_handle"),
                    json.dumps(s.get("urls") or {}),
                    s.get("notes"),
                )
                + (now,),
            )

    def get_candidate_by_url(self, profile_url):
        return self.one("SELECT * FROM candidates WHERE profile_url=?", (profile_url,))

    def get_candidate_by_username(self, username, platform="tiktok"):
        return self.one(
            "SELECT * FROM candidates WHERE username=? AND platform=?", (username, platform)
        )

    def upsert_candidate_discovery(self, username, profile_url, platform="tiktok", display_name=None, run_id=None):
        from ..utils.helpers import iso, now_utc
        now = iso(now_utc())
        row = self.get_candidate_by_url(profile_url)
        with self.tx():
            if row is None:
                cur = self.conn.execute(
                    "INSERT INTO candidates (username, display_name, platform, profile_url, verification_status, first_run_id, last_run_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (username, display_name, platform, profile_url, "pending", run_id, run_id, now, now),
                )
                return cur.lastrowid, True
            self.conn.execute(
                "UPDATE candidates SET last_run_id=?, updated_at=? WHERE id=?",
                (run_id, now, row["id"]),
            )
            return row["id"], False

    def add_source(self, candidate_id, seed_account, discovery_method, source_url=None, query_used=None):
        from ..utils.helpers import iso, now_utc
        dup = self.one(
            "SELECT id FROM candidate_sources WHERE candidate_id=? AND COALESCE(seed_account,'')=? AND discovery_method=? AND COALESCE(source_url,'')=COALESCE(?, '')",
            (candidate_id, seed_account or "", discovery_method, source_url),
        )
        if dup:
            return dup["id"]
        cur = self.execute(
            "INSERT INTO candidate_sources (candidate_id, seed_account, discovery_method, source_url, query_used, discovered_at) VALUES (?,?,?,?,?,?)",
            (candidate_id, seed_account, discovery_method, source_url, query_used, iso(now_utc())),
        )
        return cur.lastrowid

    def update_candidate(self, candidate_id, **fields):
        from ..utils.helpers import iso, now_utc
        allowed = [
            "display_name", "followers", "following", "video_count", "bio",
            "recent_post_date", "recent_post_count", "activity_status", "activity_source",
            "last_post_date", "trucking_relevance", "trucking_topics", "california_relevance",
            "california_evidence", "engagement_status", "engagement_indicator", "public_contact",
            "other_social_links", "source_urls", "date_checked", "follower_status",
            "verification_status",
        ]
        sets, vals = [], []
        for k, v in fields.items():
            if k in allowed:
                if isinstance(v, (list, dict)):
                    v = json.dumps(v, ensure_ascii=False)
                sets.append(f"{k}=?")
                vals.append(v)
        sets.append("updated_at=?")
        vals.append(iso(now_utc()))
        vals.append(candidate_id)
        self.execute(f"UPDATE candidates SET {', '.join(sets)} WHERE id=?", tuple(vals))

    def save_activity(self, candidate_id, checked_at, last_post_date, post_count_sampled, activity_status, activity_source, raw_evidence=None):
        self.execute(
            "INSERT INTO candidate_activity (candidate_id, checked_at, last_post_date, post_count_sampled, activity_status, activity_source, raw_evidence) VALUES (?,?,?,?,?,?,?)",
            (candidate_id, checked_at, last_post_date, post_count_sampled, activity_status, activity_source, raw_evidence),
        )

    def save_score(self, candidate_id, marketing_score, confidence_score, breakdown):
        from ..utils.helpers import iso, now_utc
        self.execute(
            "INSERT INTO candidate_scores (candidate_id, marketing_score, confidence_score, breakdown, scored_at) VALUES (?,?,?,?,?)",
            (candidate_id, marketing_score, confidence_score, json.dumps(breakdown), iso(now_utc())),
        )

    def record_processed(self, url, kind, status, run_id=None):
        from ..utils.helpers import iso, now_utc
        self.execute(
            "INSERT OR REPLACE INTO processed_urls (url, kind, status, run_id, timestamp) VALUES (?,?,?,?,?)",
            (url, kind, status, run_id, iso(now_utc())),
        )

    def was_processed_ok(self, url):
        row = self.one("SELECT status FROM processed_urls WHERE url=?", (url,))
        return bool(row and row["status"] == "ok")

    def record_error(self, run_id, source, url, error_type, message):
        from ..utils.helpers import iso, now_utc
        self.execute(
            "INSERT INTO errors (run_id, source, url, error_type, message, timestamp) VALUES (?,?,?,?,?,?)",
            (run_id, source, url, error_type, str(message)[:2000], iso(now_utc())),
        )

    def pending_candidates(self, run_id=None, limit=None):
        sql = "SELECT * FROM candidates WHERE verification_status='pending'"
        params = []
        if run_id:
            sql += " AND (first_run_id=? OR last_run_id=?)"
            params += [run_id, run_id]
        sql += " ORDER BY id ASC"
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        return self.query(sql, tuple(params))

    def qualified_rows(self):
        return self.query(
            """SELECT c.*, s.marketing_score, s.confidence_score, s.breakdown AS score_breakdown
               FROM candidates c
               LEFT JOIN candidate_scores s ON s.id = (
                   SELECT id FROM candidate_scores WHERE candidate_id=c.id ORDER BY scored_at DESC LIMIT 1)
               WHERE c.follower_status='QUALIFIED' AND c.verification_status='done'
               ORDER BY s.marketing_score DESC, 
                        CASE c.california_relevance WHEN 'HIGH' THEN 3 WHEN 'MEDIUM' THEN 2 WHEN 'LOW' THEN 1 ELSE 0 END DESC,
                        CASE c.trucking_relevance WHEN 'HIGH' THEN 3 WHEN 'MEDIUM' THEN 2 WHEN 'LOW' THEN 1 ELSE 0 END DESC,
                        c.followers DESC"""
        )

    def all_verified_rows(self):
        return self.query(
            """SELECT c.*, s.marketing_score, s.confidence_score FROM candidates c
               LEFT JOIN candidate_scores s ON s.id = (
                   SELECT id FROM candidate_scores WHERE candidate_id=c.id ORDER BY scored_at DESC LIMIT 1)
               WHERE c.verification_status='done'
               ORDER BY c.followers DESC"""
        )

    def sources_for(self, candidate_id):
        return self.query("SELECT * FROM candidate_sources WHERE candidate_id=? ORDER BY id", (candidate_id,))

    def stats(self, run_id=None):
        if run_id:
            total = self.one(
                "SELECT COUNT(*) AS n FROM candidates WHERE first_run_id=? OR last_run_id=?", (run_id, run_id)
            )["n"]
            verified = self.one(
                "SELECT COUNT(*) AS n FROM candidates WHERE verification_status='done' AND (first_run_id=? OR last_run_id=?)",
                (run_id, run_id),
            )["n"]
        else:
            total = self.one("SELECT COUNT(*) AS n FROM candidates")["n"]
            verified = self.one(
                "SELECT COUNT(*) AS n FROM candidates WHERE verification_status='done'"
            )["n"]
        return {"total": total, "verified": verified}
