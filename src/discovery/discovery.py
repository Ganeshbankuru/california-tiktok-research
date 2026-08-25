import re

from ..browser.exceptions import BlockedSourceError, RateLimitedError, SourceError, UnreachableSourceError
from ..sources.youtube import extract_tiktok_handles_from_results
from ..utils.helpers import normalize_username


METHOD_SEED_RELATED = "seed_related"
METHOD_VIDEO_RELATED = "video_related"
METHOD_HASHTAG = "hashtag"
METHOD_SEARCH_ENGINE = "search_engine"
METHOD_PUBLIC_MENTION = "public_mention"
METHOD_COLLABORATION = "collaboration"
METHOD_EXTERNAL_SOURCE = "external_source"

YT_CHANNEL_RE = re.compile(r"https?://(?:www\.)?youtube\.com/(@[A-Za-z0-9._\-]+|channel/[A-Za-z0-9._\-]+)", re.IGNORECASE)


class DiscoveryEngine:
    def __init__(self, settings, keywords, seeds, search_source, logger=None, youtube=None):
        self.settings = settings
        self.keywords = keywords
        self.seeds = seeds
        self.search = search_source
        self.yt = youtube
        self.log = logger

    def _queries_for_seed(self, seed):
        names = [seed["name"]] + list(seed.get("aliases") or [])
        queries = []
        for n in names[:2]:
            queries.append(f'"{n}"')
            queries.append(f"{n} tiktok")
        return queries

    @staticmethod
    def _channels_in(results):
        out = []
        seen = set()
        for r in results or []:
            m = YT_CHANNEL_RE.search(r.get("url") or "")
            if m:
                url = f"https://www.youtube.com/{m.group(1)}"
                if url.lower() not in seen:
                    seen.add(url.lower())
                    out.append({"url": url, "title": r.get("title", "")})
        return out

    def _socials_to_records(self, socials, seed_name, method, source_url, query_used):
        records = []
        for link in socials.get("tiktok") or []:
            u = normalize_username(link)
            if not u:
                continue
            records.append({
                "username": u,
                "seed_account": seed_name,
                "discovery_method": method,
                "source_url": socials.get("url") or source_url,
                "query_used": query_used,
            })
        return records

    def discover_seed_related_via_youtube(self, stop_when=None):
        """Resolve each seed's own TikTok handle via their public YouTube channel."""
        found = []
        if self.yt is None or self.yt.disabled():
            return found
        for seed in self.seeds:
            if stop_when and stop_when(len({r["username"] for r in found if r.get("username")})):
                break
            seed_name = seed["name"]
            for q in self._queries_for_seed(seed)[:2]:
                if self.yt.disabled():
                    break
                try:
                    results = self.yt.search(q)
                except (BlockedSourceError, RateLimitedError, SourceError) as e:
                    found.append({"_error": e})
                    break
                except (UnreachableSourceError, RuntimeError) as e:
                    found.append({"_error": e})
                    continue
                channels = self._channels_in(results)[:2]
                matched_self = None
                low_name = seed_name.lower()
                for ch in channels:
                    if low_name.split()[0] in ch["title"].lower() or any(
                        a.lower() in ch["title"].lower() for a in seed.get("aliases") or []
                    ):
                        matched_self = ch
                        break
                targets = [matched_self] if matched_self else channels[:1]
                for ch in targets:
                    if self.yt.disabled():
                        break
                    try:
                        socials = self.yt.channel_socials(ch["url"])
                    except (BlockedSourceError, RateLimitedError, SourceError) as e:
                        found.append({"_error": e})
                        break
                    except (UnreachableSourceError, RuntimeError) as e:
                        found.append({"_error": e})
                        continue
                    is_seed_channel = bool(matched_self) or low_name in ch["title"].lower()
                    found.extend(self._socials_to_records(
                        socials,
                        seed_name=seed_name if is_seed_channel else None,
                        method=METHOD_SEED_RELATED,
                        source_url=socials.get("url"),
                        query_used=q,
                    ))
                if found and any(r.get("seed_account") == seed_name for r in found):
                    break
        return found

    def keyword_queries(self, limit=8):
        terms = self.keywords.get("core_terms", [])
        ca_terms = self.keywords.get("california_terms_strong", [])[:6]
        queries = []
        for t in terms[:limit]:
            queries.append(f"site:tiktok.com {t}")
        for c in ca_terms[:4]:
            queries.append(f'site:tiktok.com trucking "{c}"')
        return queries

    def youtube_keyword_queries(self, limit=6):
        combos = [
            "trucking tiktok",
            "california trucker tiktok",
            "cdl tiktok",
            "owner operator tiktok",
            "big rig tiktok california",
            "trucker wife tiktok",
        ]
        extra = [f"{t} tiktok" for t in self.keywords.get("core_terms", [])[:limit]]
        return (combos + extra)[:limit + 6]

    def hashtag_queries(self, limit=8):
        tags = self.keywords.get("hashtag_seeds", [])
        ca_tags = self.keywords.get("california_hashtags", [])
        queries = [f"site:tiktok.com/tag/{t}" for t in tags[:limit]]
        for t in tags[:3]:
            for c in ca_tags[:2]:
                queries.append(f"site:tiktok.com/tag/{t}{c}")
        return queries

    def discover_by_youtube_keywords(self, stop_when=None):
        """Find trucking creators who publicly list their TikTok on YouTube."""
        found = []
        if self.yt is None or self.yt.disabled():
            return found
        for q in self.youtube_keyword_queries():
            if stop_when and stop_when(len({r["username"] for r in found if r.get("username")})):
                break
            if self.yt.disabled():
                break
            try:
                results = self.yt.search(q)
            except (BlockedSourceError, RateLimitedError, SourceError) as e:
                found.append({"_error": e})
                break
            except (UnreachableSourceError, RuntimeError) as e:
                found.append({"_error": e})
                continue
            for ch in self._channels_in(results)[:2]:
                if self.yt.disabled():
                    break
                try:
                    socials = self.yt.channel_socials(ch["url"])
                except (BlockedSourceError, RateLimitedError, SourceError) as e:
                    found.append({"_error": e})
                    break
                except (UnreachableSourceError, RuntimeError) as e:
                    found.append({"_error": e})
                    continue
                found.extend(self._socials_to_records(
                    socials,
                    seed_name=None,
                    method=METHOD_PUBLIC_MENTION,
                    source_url=socials.get("url"),
                    query_used=q,
                ))
        return found

    def discover_seed_related(self, stop_when=None):
        """Fallback: general web search for seed TikTok accounts."""
        found = []
        for seed in self.seeds:
            if self.search.disabled():
                if self.log:
                    self.log.warning("search source disabled; skipping remaining seed discovery")
                break
            seed_name = seed["name"]
            for q in self._queries_for_seed(seed)[:2]:
                if stop_when and stop_when(len({r.get("username") for r in found if r.get("username")})):
                    return found
                try:
                    results = self.search.search(q)
                except BlockedSourceError as e:
                    found.append({"_error": e})
                    break
                except (RateLimitedError, RuntimeError) as e:
                    found.append({"_error": e})
                    continue
                handles = extract_tiktok_handles_from_results(results)
                for h in handles:
                    u = normalize_username(h)
                    if not u:
                        continue
                    top = next((r for r in results if f"/@{u}" in (r.get("url") or "").lower()), None)
                    found.append({
                        "username": u,
                        "seed_account": seed_name,
                        "discovery_method": METHOD_SEED_RELATED,
                        "source_url": (top or {}).get("url"),
                        "query_used": q,
                    })
        return found

    def keyword_queries(self, limit=8):
        terms = self.keywords.get("core_terms", [])
        ca_terms = self.keywords.get("california_terms_strong", [])[:6]
        queries = []
        for t in terms[:limit]:
            queries.append(f"site:tiktok.com {t}")
        for c in ca_terms[:4]:
            queries.append(f'site:tiktok.com trucking "{c}"')
        return queries

    def hashtag_queries(self, limit=8):
        tags = self.keywords.get("hashtag_seeds", [])
        ca_tags = self.keywords.get("california_hashtags", [])
        queries = [f"site:tiktok.com/tag/{t}" for t in tags[:limit]]
        for t in tags[:3]:
            for c in ca_tags[:2]:
                queries.append(f"site:tiktok.com/tag/{t}{c}")
        return queries

    def discover_by_search(self, kind="keyword", max_queries=None, stop_when=None):
        """kind: 'keyword' or 'hashtag'. Returns discovered username records."""
        assert kind in ("keyword", "hashtag")
        queries = self.keyword_queries() if kind == "keyword" else self.hashtag_queries()
        if max_queries:
            queries = queries[:max_queries]
        method = METHOD_SEARCH_ENGINE if kind == "keyword" else METHOD_HASHTAG
        found = []
        for q in queries:
            if self.search.disabled():
                break
            if stop_when and stop_when(len({r.get("username") for r in found if r.get("username")})):
                break
            try:
                results = self.search.search(q)
            except BlockedSourceError as e:
                found.append({"_error": e})
                break
            except (RateLimitedError, RuntimeError) as e:
                found.append({"_error": e})
                continue
            handles = extract_tiktok_handles_from_results(results)
            for h in handles:
                u = normalize_username(h)
                if not u:
                    continue
                top = next((r for r in results if f"/@{u}" in (r.get("url") or "").lower()), None)
                found.append({
                    "username": u,
                    "seed_account": None,
                    "discovery_method": method,
                    "source_url": (top or {}).get("url"),
                    "query_used": q,
                })
        return found
