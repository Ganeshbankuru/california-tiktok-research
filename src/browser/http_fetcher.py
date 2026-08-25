import re

import httpx

from .exceptions import BlockedSourceError, RateLimitedError


class HttpFetcher:
    """Polite HTTP fetcher for static public pages (search engines).

    Respects pacing set by caller. Detects blocks/rate limits and raises so
    callers record the event and stop using that source.
    """

    def __init__(self, user_agent=None, timeout=20):
        self.headers = {
            "User-Agent": user_agent
            or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        }
        self.timeout = timeout
        self._client = None

    def client(self):
        if self._client is None:
            self._client = httpx.Client(
                headers=self.headers,
                timeout=self.timeout,
                follow_redirects=True,
            )
        return self._client

    def get(self, url):
        try:
            resp = self.client().get(url)
        except httpx.TimeoutException as e:
            raise RuntimeError(f"timeout fetching {url}") from e
        except httpx.HTTPError as e:
            raise RuntimeError(f"http error fetching {url}: {e}") from e
        if resp.status_code == 429:
            raise RateLimitedError("http", url, "HTTP 429")
        if resp.status_code in (403, 401):
            raise BlockedSourceError("http", url, f"access restricted (HTTP {resp.status_code})")
        resp.raise_for_status()
        text = resp.text
        low = text[:4000].lower()
        for ind in ("captcha", "unusual traffic", "verify you are human", "access denied"):
            if ind in low and len(text) < 20000:
                raise BlockedSourceError("http", url, f"page presented protection ({ind})")
        return resp.text


RESULT_LINK_RE = re.compile(r'href="(https?://[^"]+)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
TIKTOK_USER_RE = re.compile(r"https?://(?:www\.)?tiktok\.com/@([A-Za-z0-9._\-]+)/?", re.IGNORECASE)


def extract_tiktok_usernames(html_text, exclude=()):
    found = []
    seen = set()
    for m in TIKTOK_USER_RE.finditer(html_text or ""):
        u = m.group(1)
        u = u.split("?")[0].rstrip(".")
        low = u.lower()
        if low in seen or not u:
            continue
        seen.add(low)
        found.append(u)
    return [u for u in found if u.lower() not in {e.lower() for e in (exclude or ())}]


def extract_results(html_text):
    out = []
    for url, snippet in RESULT_LINK_RE.findall(html_text or ""):
        title = re.sub(r"<[^>]+>", "", snippet).strip()
        if "duckduckgo.com" in url:
            continue
        out.append({"url": url, "title": title})
    return out
