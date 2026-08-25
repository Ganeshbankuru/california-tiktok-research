import re

from ..browser.agent_browser import AgentBrowser
from ..browser.exceptions import BlockedSourceError, RateLimitedError, SourceError, UnreachableSourceError
from ..browser.http_fetcher import HttpFetcher
from ..utils.helpers import RateLimiter, safe_int


CHANNEL_URL_RE = re.compile(r"https?://(?:www\.)?youtube\.com/(?:c/|channel/|@|user/)?([A-Za-z0-9._\-]+)", re.IGNORECASE)
SUBS_RE = re.compile(r'"subscriberCountText":\{"simpleText":"([^"]+)"', re.IGNORECASE)
TITLE_RE = re.compile(r'<meta property="og:title" content="([^"]+)"')
TIKTOK_LINK_RE = re.compile(r"https?://(?:www\.)?tiktok\.com/@([A-Za-z0-9._\-]+)", re.IGNORECASE)


class YouTubeSource:
    name = "youtube_web"

    def __init__(self, settings, logger=None):
        self.fetcher = HttpFetcher(
            user_agent=settings["network"].get("user_agent"),
            timeout=settings["network"].get("timeout_seconds", 20),
        )
        self.limiter = RateLimiter(settings["network"].get("request_delay_seconds", 2.5))
        self.log = logger
        self.failures = 0
        self.max_failures = settings["network"].get("blocked_source_max_failures", 2)

    def disabled(self):
        return self.failures >= self.max_failures

    def note_block(self):
        self.failures += 1

    def fetch_channel_about(self, channel_url):
        """Light public fetch of a YouTube channel page.

        Returns dict with title/subscribers/tiktok_handle if present; None if
        page structure not parseable. Raises BlockedSourceError on blocks.
        """
        if self.disabled():
            raise SourceError(self.name, channel_url, "source disabled after repeated blocks")
        url = channel_url.rstrip("/") + "/about"
        try:
            html = self.fetcher.get(url)
        except (BlockedSourceError, RateLimitedError):
            self.note_block()
            raise
        except RuntimeError:
            return None
        out = {
            "url": channel_url,
            "title": None,
            "subscribers_text": None,
            "tiktok_handles": [],
        }
        m = TITLE_RE.search(html)
        if m:
            out["title"] = m.group(1)
        m = SUBS_RE.search(html)
        if m:
            out["subscribers_text"] = safe_int(m.group(1))
        handles = list(dict.fromkeys(TIKTOK_LINK_RE.findall(html)))
        out["tiktok_handles"] = handles[:5]
        return out


def extract_youtube_links(results):
    urls = []
    seen = set()
    for r in results or []:
        u = r.get("url") or ""
        if "youtube.com" in u.lower() and u.lower() not in seen:
            m = CHANNEL_URL_RE.search(u)
            if m:
                seen.add(u.lower())
                urls.append(f"https://www.youtube.com/@{m.group(1)}")
    return urls


def extract_tiktok_handles_from_results(results):
    from ..browser.http_fetcher import extract_tiktok_usernames
    handles = []
    for r in results or []:
        blob = f"{r.get('url','')} {r.get('title','')}"
        handles.extend(extract_tiktok_usernames(blob))
    return list(dict.fromkeys(handles))


SOCIAL_LINK_RES = {
    "tiktok": re.compile(r"(https?://(?:www\.)?tiktok\.com/@[A-Za-z0-9._\-]+)", re.IGNORECASE),
    "instagram": re.compile(r"(https?://(?:www\.)?instagram\.com/[A-Za-z0-9._\-]+/?)", re.IGNORECASE),
}


class YouTubeBrowserSearch:
    """YouTube public search + channel social-link extraction via agent-browser.

    YouTube renders search results and channel pages server-side, so this path
    works even when general search engines are unusable.
    """

    name = "youtube_search"

    def __init__(self, settings, logger=None):
        self.browser = AgentBrowser(session="youtube", timeout=settings["network"].get("timeout_seconds", 25), logger=logger)
        self.limiter = RateLimiter(settings["network"].get("request_delay_seconds", 2.5))
        self.log = logger
        self.failures = 0
        self.max_failures = settings["network"].get("blocked_source_max_failures", 2)
        self._available = self.browser.available()

    def available(self):
        return self._available

    def disabled(self):
        return self.failures >= self.max_failures

    def note_failure(self):
        self.failures += 1

    SEARCH_LINKS_JS = """
    (() => {
      const out = [];
      document.querySelectorAll('a').forEach(a => {
        const h = a.href || '';
        const t = (a.title || a.textContent || '').trim().slice(0, 120);
        if (h.includes('/watch?v=') && t) out.push({url: h.split('&')[0], title: t});
      });
      document.querySelectorAll('ytd-channel-renderer a, ytd-video-renderer ytd-channel-name a').forEach(a => {
        const h = a.href || '';
        if (h.includes('/@') || h.includes('/channel/')) out.push({url: h.split('?')[0], title: (a.textContent||'').trim().slice(0,120)});
      });
      return JSON.stringify(out.slice(0, 30));
    })()
    """

    CHANNEL_SOCIALS_JS = """
    (() => {
      const links = new Set();
      document.querySelectorAll('a').forEach(a => {
        const h = a.href || '';
        if (/tiktok\\.com|instagram\\.com|facebook\\.com|x\\.com|twitter\\.com/.test(h) && !h.includes('youtube.com')) {
          links.add(h);
        }
      });
      const html = document.documentElement.innerHTML;
      for (const m of html.matchAll(/https?:\\/\\/(?:www\\.)?tiktok\\.com\\/@[A-Za-z0-9._\\-]+/g)) links.add(m[0]);
      for (const m of html.matchAll(/https?:\\/\\/(?:www\\.)?instagram\\.com\\/[A-Za-z0-9._\\-]+/g)) links.add(m[0].replace(/\\/$/, ''));
      return JSON.stringify({
        title: document.title,
        links: Array.from(links).slice(0, 20),
      });
    })()
    """

    def search(self, query):
        if not self._available:
            raise SourceError(self.name, "", "agent-browser unavailable")
        if self.disabled():
            raise RuntimeError(f"source {self.name} disabled after repeated failures")
        import urllib.parse
        url = "https://www.youtube.com/results?" + urllib.parse.urlencode({"search_query": query})
        self.limiter.wait(self.name)
        try:
            self.browser.open(url, extra_wait_ms=2500)
        except RuntimeError as e:
            self.note_failure()
            raise UnreachableSourceError(self.name, url, f"youtube unreachable: {str(e)[:160]}")
        block = self.browser.detect_block()
        if block:
            self.note_failure()
            raise BlockedSourceError(self.name, url, f"page presented protection ({block})")
        raw = self.browser.eval(self.SEARCH_LINKS_JS)
        import json as _json
        try:
            items = _json.loads(raw) if isinstance(raw, str) else raw
        except (ValueError, TypeError):
            items = []
        seen = set()
        out = []
        for it in items or []:
            u = it.get("url") or ""
            if u.lower() in seen:
                continue
            seen.add(u.lower())
            out.append({"url": u, "title": it.get("title", "")})
        return out

    def channel_socials(self, channel_url):
        """Open a channel page; return {title, tiktok:[], instagram:[], other:[]}."""
        if self.disabled():
            raise RuntimeError(f"source {self.name} disabled after repeated failures")
        url = channel_url.rstrip("/")
        self.limiter.wait(self.name)
        try:
            self.browser.open(url, extra_wait_ms=2500)
        except RuntimeError as e:
            self.note_failure()
            raise UnreachableSourceError(self.name, url, f"youtube unreachable: {str(e)[:160]}")
        block = self.browser.detect_block()
        if block:
            self.note_failure()
            raise BlockedSourceError(self.name, url, f"page presented protection ({block})")
        raw = self.browser.eval(self.CHANNEL_SOCIALS_JS)
        import json as _json
        data = {}
        try:
            data = _json.loads(raw) if isinstance(raw, str) else (raw or {})
        except (ValueError, TypeError):
            data = {}
        links = data.get("links") or []
        out = {"url": url, "title": data.get("title"), "tiktok": [], "instagram": [], "other": []}
        seen = set()
        for l in links:
            low = l.lower().split("?")[0]
            if low in seen:
                continue
            seen.add(low)
            if "tiktok.com/@" in low:
                out["tiktok"].append(l.split("?")[0])
            elif "instagram.com/" in low:
                out["instagram"].append(l.split("?")[0])
            elif "youtube.com" not in low:
                out["other"].append(l)
        return out

    def close(self):
        if self._available:
            self.browser.close()
