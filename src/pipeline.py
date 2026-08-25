import uuid

from .database.db import Database
from .discovery.discovery import DiscoveryEngine
from .filters.filters import (
    CaliforniaClassifier,
    TruckingClassifier,
    classify_activity,
    classify_followers,
)
from .scoring.scoring import compute_engagement, compute_scores
from .sources.search_engine import SearchEngineSource
from .sources.tiktok import TikTokWebSource
from .sources.youtube import YouTubeBrowserSearch
from .utils.helpers import iso, now_utc


class ResearchPipeline:
    def __init__(self, db: Database, settings, keywords, seeds, logger=None):
        self.db = db
        self.settings = settings
        self.keywords = keywords
        self.seeds = seeds
        self.log = logger
        self.search = SearchEngineSource(settings, logger=logger)
        self.tiktok = TikTokWebSource(settings, logger=logger)
        self.youtube = YouTubeBrowserSearch(settings, logger=logger)
        self.discovery = DiscoveryEngine(
            settings, keywords, seeds, self.search, logger=logger, youtube=self.youtube
        )
        ca_strong = keywords.get("california_terms_strong", [])
        ca_weak = {k: v for k, v in (keywords.get("california_terms_weak") or {}).items()}
        self.ca_classifier = CaliforniaClassifier(ca_strong, ca_weak)
        rel = settings.get("relevance", {})
        self.trucking_classifier = TruckingClassifier(
            topics=keywords.get("topics", {}),
            core_terms=keywords.get("core_terms", []),
            strong_terms=keywords.get("strong_topic_terms", []),
            high_min_strong=rel.get("trucking_high_min_strong_terms", 2),
            medium_min_terms=rel.get("trucking_medium_min_terms", 1),
        )
        self.db.ensure_seeds(seeds)

    # ---------- run management ----------

    def new_run(self, limit=None, seed_filter=None, reverify=False):
        run_id = f"run_{now_utc().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        hard_cap = self.settings["limits"].get("max_candidates_hard_cap", 1000)
        if limit is None:
            limit = self.settings["limits"].get("default_test_limit", 10)
        limit = min(int(limit), hard_cap)
        self.db.create_run(run_id, limit_candidates=limit, seed_filter=seed_filter)
        if self.log:
            self.log.info(f"created run {run_id} limit={limit} seed={seed_filter}")
        if reverify:
            n = self.db.execute(
                "UPDATE candidates SET verification_status='pending' WHERE verification_status IN ('done_no_data','blocked','failed')"
            ).rowcount
            if self.log:
                self.log.info(f"reverify: reset {n} unverified/blocked candidate(s) to pending")
        matched_seeds = self._match_seed(seed_filter) if seed_filter else None
        if seed_filter and not matched_seeds:
            raise ValueError(f"no seed account matches '{seed_filter}'")
        self._discovery_phase(run_id, limit, seed_filter, matched_seeds)
        self._verification_phase(run_id, limit)
        self.db.finish_run(run_id, "completed")
        return run_id

    def resume(self, run_id=None, extra_limit=None):
        row = self.db.get_run(run_id) if run_id else self.db.latest_running_run()
        if row is None:
            raise ValueError("no running run found to resume; use --new-run first")
        run = dict(row)
        rid = run["run_id"]
        if self.log:
            self.log.info(f"resuming run {rid}")
        pending = self.db.pending_candidates(rid)
        remaining_limit = None
        if run["limit_candidates"]:
            verified = len(self.db.query(
                "SELECT id FROM candidates WHERE verification_status IN ('done','blocked') AND (first_run_id=? OR last_run_id=?)",
                (rid, rid),
            ))
            remaining_limit = max(0, int(run["limit_candidates"]) - verified)
            if extra_limit is not None:
                remaining_limit = min(remaining_limit, int(extra_limit))
        if not pending or remaining_limit == 0:
            if self.log:
                self.log.info(f"run {rid}: nothing pending; re-running discovery for any shortfall")
            self._discovery_phase(rid, run["limit_candidates"], run["seed_filter"], None, top_up=True)
        self._verification_phase(rid, remaining_limit if remaining_limit else run["limit_candidates"])
        left = len(self.db.pending_candidates(rid))
        self.db.finish_run(rid, "completed" if left == 0 else "incomplete")
        return rid

    def _match_seed(self, name):
        low = name.lower().strip()
        return [s for s in self.seeds if low in s["name"].lower() or any(low in a.lower() for a in s.get("aliases") or [])]

    # ---------- phases ----------

    def _discovery_phase(self, run_id, limit, seed_filter, matched_seeds, top_up=False):
        records = []
        need = max(limit * self.settings["limits"].get("discovery_multiplier", 3), limit * 2)
        stop = lambda n: n >= need  # noqa: E731
        if matched_seeds is not None:
            subset_engine = DiscoveryEngine(
                self.settings, self.keywords, matched_seeds, self.search,
                logger=self.log, youtube=self.youtube,
            )
            if self.log:
                self.log.info("discovery: youtube seed-related pass")
            records.extend(subset_engine.discover_seed_related_via_youtube(stop_when=stop))
            current = len({r.get("username") for r in records if r.get("username")})
            if current < need and not self.search.disabled():
                if self.log:
                    self.log.info(f"discovery: web-search seed pass ({current}/{need})")
                records.extend(subset_engine.discover_seed_related(stop_when=stop))
        elif seed_filter is None:
            if self.log:
                self.log.info("discovery: youtube seed-related pass")
            records.extend(self.discovery.discover_seed_related_via_youtube(stop_when=stop))
            current = len({r.get("username") for r in records if r.get("username")})
            if current < need and not self.youtube.disabled():
                if self.log:
                    self.log.info(f"discovery: youtube keyword pass ({current}/{need})")
                records.extend(self.discovery.discover_by_youtube_keywords(stop_when=stop))
                current = len({r.get("username") for r in records if r.get("username")})
            if current < need and not self.search.disabled():
                if self.log:
                    self.log.info(f"discovery: web-search seed pass ({current}/{need})")
                records.extend(self.discovery.discover_seed_related(stop_when=stop))
                current = len({r.get("username") for r in records if r.get("username")})
            if current < need and not self.search.disabled():
                if self.log:
                    self.log.info("discovery: hashtag search")
                records.extend(self.discovery.discover_by_search("hashtag", stop_when=stop))
        self._persist_discoveries(records, run_id)

    def _persist_discoveries(self, records, run_id):
        for rec in records:
            if "_error" in rec:
                err = rec["_error"]
                kind = getattr(err, "kind", "error")
                src = getattr(err, "source", "search")
                url = getattr(err, "url", "") or ""
                self.db.record_error(run_id, src, url, kind, str(err))
                if self.log:
                    self.log.warning(f"{kind}: {src} {url} — recorded, moving on")
                continue
            username = rec.get("username")
            if not username:
                continue
            profile_url = f"https://www.tiktok.com/@{username}"
            cid, created = self.db.upsert_candidate_discovery(
                username, profile_url, platform="tiktok",
                display_name=None, run_id=run_id,
            )
            if not created:
                prev = self.db.one("SELECT verification_status FROM candidates WHERE id=?", (cid,))
                if prev and prev["verification_status"] == "failed":
                    self.db.update_candidate(cid, verification_status="pending", last_run_id=run_id)
                    if self.log:
                        self.log.info(f"re-queued @{username} for retry this run")
            self.db.add_source(
                cid,
                seed_account=rec.get("seed_account"),
                discovery_method=rec.get("discovery_method", "external_source"),
                source_url=rec.get("source_url"),
                query_used=rec.get("query_used"),
            )
            if self.log and created:
                self.log.info(f"discovered @{username} via {rec.get('discovery_method')}")

    # ---------- verification ----------

    def _verification_phase(self, run_id, limit):
        pending = self.db.pending_candidates(run_id, limit=limit)
        if self.log:
            self.log.info(f"verification: {len(pending)} candidate(s) queued (cap={limit})")
        verified_count = 0
        for cand in pending:
            if limit is not None and verified_count >= int(limit):
                break
            profile_url = cand["profile_url"]
            if self.db.was_processed_ok(profile_url):
                self.db.update_candidate(cand["id"], verification_status="done")
                continue
            if self.tiktok.disabled():
                reason = "tiktok source disabled after repeated failures; marking UNKNOWN per policy"
                fields = {
                    "activity_status": "UNKNOWN", "activity_source": "source_unavailable",
                    "trucking_relevance": "UNKNOWN", "california_relevance": "UNKNOWN",
                    "engagement_status": "UNKNOWN", "follower_status": "UNKNOWN",
                    "date_checked": iso(now_utc()), "verification_status": "done_no_data",
                }
                row = dict(fields); row["_source_count"] = len(self.db.sources_for(cand["id"]))
                m, c, b = compute_scores(row, self.settings)
                self.db.update_candidate(cand["id"], **fields)
                self.db.save_score(cand["id"], m, c, b)
                self.db.record_processed(profile_url, "profile", "skipped_source_down", run_id)
                self.db.record_error(run_id, "tiktok_web", profile_url, "unreachable", reason)
                continue
            try:
                self._verify_candidate(cand, run_id)
            except Exception as e:
                from .browser.exceptions import BlockedSourceError, SourceError, UnreachableSourceError
                if isinstance(e, (BlockedSourceError, UnreachableSourceError)):
                    kind = getattr(e, "kind", "error")
                    self.db.update_candidate(cand["id"], verification_status="blocked" if kind == "blocked" else "done_no_data")
                    self.db.record_processed(profile_url, "profile", kind, run_id)
                    self.db.record_error(run_id, e.source, e.url or profile_url, kind, str(e))
                    if self.log:
                        self.log.warning(f"@{cand['username']}: {kind.upper()} ({e}); recorded, moving on")
                elif isinstance(e, SourceError):
                    self.db.update_candidate(cand["id"], verification_status="failed")
                    self.db.record_processed(profile_url, "profile", "error", run_id)
                    self.db.record_error(run_id, e.source, e.url or profile_url, "error", str(e))
                    if self.log:
                        self.log.error(f"@{cand['username']}: source error: {e}")
                else:
                    self.db.record_error(run_id, "tiktok_web", profile_url, "error", str(e))
                    self.db.update_candidate(cand["id"], verification_status="failed")
                    self.db.record_processed(profile_url, "profile", "failed", run_id)
                    if self.log:
                        self.log.error(f"@{cand['username']}: verification error: {e}")
                continue
            verified_count += 1

    def _verify_candidate(self, cand, run_id):
        username = cand["username"]
        profile_url = cand["profile_url"]
        data, status = self.tiktok.fetch_profile(username)
        sources = self.db.sources_for(cand["id"])
        texts_for_ca = []
        texts_for_tr = []
        base = {
            "_source_count": len(sources),
        }
        if status == "ok" and data is not None:
            bio = data.bio or ""
            texts_for_ca = [bio]
            texts_for_tr = [bio]
            if data.display_name:
                texts_for_ca.append(data.display_name)
                texts_for_tr.append(data.display_name)
            follower_status = classify_followers(
                data.followers,
                self.settings["follower_filter"]["min_followers"],
                self.settings["follower_filter"]["max_followers"],
            )
            act_status, act_src, last_dt = self.tiktok.activity_for(data)
            ca_level, ca_evidence, _ = self.ca_classifier.classify(texts_for_ca + ([data.bio_link] if data.bio_link else []))
            tr_level, tr_topics, tr_hits = self.trucking_classifier.classify(texts_for_tr + [username.replace("_", " ").replace(".", " ")])
            ratio, eng_status, eng_detail = compute_engagement(
                data.sample_likes, data.sample_comments, data.sample_views
            )
            engagement_indicator = None
            if eng_status == "KNOWN":
                engagement_indicator = (
                    f"L{data.sample_likes}/C{data.sample_comments}/V{data.sample_views}"
                    f" over {data.recent_post_count} recent videos (engagement {eng_detail})"
                )
            other_links = [u for u in [data.instagram, data.youtube, data.bio_link] if u]
            fields = {
                "display_name": data.display_name,
                "followers": data.followers,
                "following": data.following,
                "video_count": data.video_count,
                "bio": bio[:1000],
                "recent_post_date": iso(data.last_post_dt),
                "recent_post_count": data.recent_post_count,
                "activity_status": act_status,
                "activity_source": act_src,
                "last_post_date": iso(last_dt),
                "trucking_relevance": tr_level,
                "trucking_topics": tr_topics,
                "california_relevance": ca_level,
                "california_evidence": ca_evidence or ("no california signals found in public bio/name" if bio else ""),
                "engagement_status": eng_status,
                "engagement_indicator": engagement_indicator,
                "public_contact": None,
                "other_social_links": other_links,
                "date_checked": iso(now_utc()),
                "follower_status": follower_status,
                "verification_status": "done",
                **base,
            }
            candidate_row = dict(fields)
            candidate_row["_engagement_ratio"] = ratio if eng_status == "KNOWN" else None
            m_score, c_score, breakdown = compute_scores(candidate_row, self.settings)
            self.db.update_candidate(cand["id"], **{k: v for k, v in fields.items() if k != "_source_count"})
            self.db.save_activity(
                cand["id"], iso(now_utc()), iso(last_dt), data.recent_post_count,
                act_status, act_src, json_safe(tr_hits),
            )
            self.db.save_score(cand["id"], m_score, c_score, breakdown)
            self.db.record_processed(profile_url, "profile", "ok", run_id)
            if self.log:
                self.log.info(
                    f"@{username}: followers={data.followers} [{follower_status}] "
                    f"activity={act_status} trucking={tr_level} ca={ca_level} "
                    f"score={m_score:.0f} conf={c_score:.0f}"
                )
        else:
            fields = {
                "activity_status": "UNKNOWN",
                "activity_source": status,
                "trucking_relevance": "UNKNOWN",
                "california_relevance": "UNKNOWN",
                "engagement_status": "UNKNOWN",
                "date_checked": iso(now_utc()),
                "follower_status": "UNKNOWN",
                "verification_status": "done_no_data",
                **base,
            }
            candidate_row = dict(fields)
            m_score, c_score, breakdown = compute_scores(candidate_row, self.settings)
            self.db.update_candidate(cand["id"], **{k: v for k, v in fields.items() if k != "_source_count"})
            self.db.save_score(cand["id"], m_score, c_score, breakdown)
            self.db.record_processed(profile_url, "profile", status or "no_data", run_id)
            if self.log:
                self.log.warning(f"@{username}: no public data extractable ({status}); marked UNKNOWN")

    def close(self):
        self.tiktok.close()
        self.search.close()
        self.youtube.close()


def json_safe(x):
    import json
    try:
        return json.dumps(x)
    except TypeError:
        return None
