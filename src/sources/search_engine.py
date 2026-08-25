import base64
import html as html_mod
import re
import urllib.parse

from ..browser.agent_browser import AgentBrowser
from ..browser.exceptions import BlockedSourceError, RateLimitedError
from ..browser.http_fetcher import HttpFetcher, extract_results, extract_tiktok_usernames
from ..utils.helpers import RateLimiter


BING_RESULT_RE = re.compile(
    r'<li class="b_algo".*?<h2[^>]*><a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)


def decode_bing_redirect(url):
    """Bing wraps outbound links in /ck/a?...u=a1<base64url> redirects."""
    url = html_mod.unescape(url or "")
    if "bing.com/ck/" not in url:
        return url
    m = re.search(r"[?&]u=(?:a1)?([A-Za-z0-9_\-]+)", url)
    if not m:
        return url
    token = m.group(1)
    pad = "=" * (-len(token) % 4)
    try:
        decoded = base64.urlsafe_b64decode(token + pad).decode("latin-1")
    except Exception:
        return url
    idx = decoded.find("http")
    return decoded[idx:].split("#")[0].strip() if idx >= 0 else url


def _strip_tags(s):
    return re.sub(r"<[^>]+>", "", s or "").strip()


class SearchEngineSource:
    """Multi-engine public web search.

    Engine order is configurable via settings.network.search_engines:
      - bing_browser        (real Chrome via agent-browser, parses b_algo blocks)
      - duckduckgo_http     (lite.duckduckgo.com via polite httpx)
      - duckduckgo_browser  (real Chrome fallback)
    A failing/blocked engine is recorded and skipped; repeated blocks disable
    the whole source per policy.
    """

    name = "web_search"

    def __init__(self, settings, logger=None):
        self.ddg_endpoint = settings["network"].get("search_engine", "https://lite.duckduckgo.com/lite/")
        self.engine_order = settings["network"].get(
            "search_engines", ["bing_browser", "duckduckgo_http", "duckduckgo_browser"]
        )
        self.fetcher = HttpFetcher(
            user_agent=settings["network"].get("user_agent"),
            timeout=settings["network"].get("timeout_seconds", 20),
        )
        self.limiter = RateLimiter(settings["network"].get("request_delay_seconds", 2.5))
        self.log = logger
        self.failures = 0
        self.max_failures = settings["network"].get("blocked_source_max_failures", 2)
        self.browser = AgentBrowser(
            session="search", timeout=settings["network"].get("timeout_seconds", 25), logger=logger
        )
        self._browser_ok = self.browser.available()

    def disabled(self):
        return self.failures >= self.max_failures

    def note_block(self):
        self.failures += 1

    def _bing_url(self, query):
        return "https://www.bing.com/search?" + urllib.parse.urlencode({"q": query, "count": "30"})

    def _ddg_lite_url(self, query):
        return f"{self.ddg_endpoint}?q={urllib.parse.quote_plus(query)}"

    def _ddg_html_url(self, query):
        return "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})

    def _search_bing_browser(self, query):
        if not self._browser_ok:
            raise BlockedSourceError(self.name, self._bing_url(query), "browser unavailable")
        url = self._bing_url(query)
        try:
            self.browser.open(url)
        except RuntimeError as e:
            raise RuntimeError(f"browser open failed: {e}")
        block = self.browser.detect_block()
        if block:
            raise BlockedSourceError(self.name, url, f"page presented protection ({block})")
        html = str(self.browser.eval("document.documentElement.outerHTML") or "")
        out = []
        seen = set()
        for u, t in BING_RESULT_RE.findall(html):
            real = decode_bing_redirect(u)
            if not real.startswith("http"):
                continue
            if real.lower() in seen or "bing.com" in real.lower():
                continue
            seen.add(real.lower())
            out.append({"url": real, "title": _strip_tags(t)})
        for h in extract_tiktok_usernames(html_mod.unescape(html)):
            profile = f"https://www.tiktok.com/@{h}"
            if profile.lower() not in seen:
                seen.add(profile.lower())
                out.append({"url": profile, "title": f"@{h}"})
        return out

    def _search_ddg_http(self, query):
        url = self._ddg_lite_url(query)
        return extract_results(self.fetcher.get(url))

    def _search_ddg_browser(self, query):
        if not self._browser_ok:
            raise BlockedSourceError(self.name, self._ddg_html_url(query), "browser unavailable")
        url = self._ddg_html_url(query)
        self.browser.open(url)
        block = self.browser.detect_block()
        if block:
            raise BlockedSourceError(self.name, url, f"page presented protection ({block})")
        html = str(self.browser.eval("document.documentElement.outerHTML") or "")
        out = extract_results(html)
        seen = {r["url"].lower() for r in out}
        for h in extract_tiktok_usernames(html):
            profile = f"https://www.tiktok.com/@{h}"
            if profile.lower() not in seen:
                out.append({"url": profile, "title": f"@{h}"})
        return out

    def search(self, query):
        """Try engines in configured order. Raises only if ALL engines fail."""
        if self.disabled():
            raise RuntimeError(f"source {self.name} disabled after repeated blocks")
        errors = []
        for engine in self.engine_order:
            fn = {
                "bing_browser": self._search_bing_browser,
                "duckduckgo_http": self._search_ddg_http,
                "duckduckgo_browser": self._search_ddg_browser,
            }.get(engine)
            if fn is None:
                continue
            self.limiter.wait(engine)
            try:
                results = fn(query)
                if results:
                    return results
                errors.append(f"{engine}: 0 results")
            except (BlockedSourceError, RateLimitedError) as e:
                errors.append(f"{engine}: {getattr(e, 'kind', 'error')} ({e})")
                self.note_block()
            except RuntimeError as e:
                errors.append(f"{engine}: transient ({e})")
        if errors:
            self.note_block()
            raise RuntimeError("; ".join(errors)[:400])
        return []

    def close(self):
        if self._browser_ok:
            self.browser.close()
