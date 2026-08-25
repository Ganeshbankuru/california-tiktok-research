import json

from ..browser.agent_browser import AgentBrowser, with_retries
from ..browser.exceptions import BlockedSourceError, SourceError
from ..filters.filters import classify_activity
from ..utils.helpers import parse_date, safe_int


EXTRACT_JS = """
(() => {
  const ids = ['__UNIVERSAL_DATA_FOR_REHYDRATION__', 'SIGI_STATE'];
  for (const id of ids) {
    const el = document.getElementById(id);
    if (!el) continue;
    try {
      const data = JSON.parse(el.textContent);
      const scope = data.__DEFAULT_SCOPE__ || {};
      const ud = scope['webapp.user-detail'] || {};
      if (ud.userInfo || data.ItemModule) {
        return JSON.stringify({key: id, data});
      }
    } catch (e) {}
  }
  return JSON.stringify(null);
})()
"""


class TikTokProfileData:
    def __init__(self, username, display_name=None, followers=None, following=None,
                 video_count=None, bio=None, bio_link=None, instagram=None,
                 youtube=None, last_post_dt=None, recent_post_count=0,
                 sample_likes=0, sample_comments=0, sample_views=0, raw=None):
        self.username = username
        self.display_name = display_name
        self.followers = followers
        self.following = following
        self.video_count = video_count
        self.bio = bio
        self.bio_link = bio_link
        self.instagram = instagram
        self.youtube = youtube
        self.last_post_dt = last_post_dt
        self.recent_post_count = recent_post_count
        self.sample_likes = sample_likes
        self.sample_comments = sample_comments
        self.sample_views = sample_views
        self.raw = raw or {}

    def to_dict(self):
        return self.__dict__.copy()


def _parse_payload(payload):
    if not payload or not isinstance(payload, dict):
        return None
    data = payload.get("data") or {}
    user_info = None
    item_list = []
    if isinstance(data, dict):
        scope = data.get("__DEFAULT_SCOPE__") or {}
        ud = scope.get("webapp.user-detail") or {}
        user_info = ud.get("userInfo")
        detail = ud.get("userDetail") or {}
        item_list = detail.get("itemList") or []
        if not user_info and "ItemModule" in data:
            mods = data["ItemModule"]
            if mods:
                first = next(iter(mods.values()))
                user_info = {"user": first, "stats": first.get("author") or {}}
                item_list = [first] if first.get("createTime") else []
    if not user_info:
        return None
    user = user_info.get("user") or {}
    stats = user_info.get("stats") or {}
    last_dt = None
    likes = comments = views = 0
    count = 0
    for it in item_list[:12]:
        ct = it.get("createTime")
        dt = parse_date(int(ct)) if ct else None
        if dt and (last_dt is None or dt > last_dt):
            last_dt = dt
        st = it.get("stats") or {}
        likes += safe_int(st.get("diggCount")) or 0
        comments += safe_int(st.get("commentCount")) or 0
        views += safe_int(st.get("playCount")) or 0
        count += 1
    bio_link_obj = user.get("bioLink")
    links = []
    ig = yt = None
    for bl in ([bio_link_obj] if bio_link_obj else []) + (user.get("bioLinkList") or []):
        u = (bl or {}).get("link") or ""
        low = u.lower()
        if "instagram.com" in low and not ig:
            ig = u
        elif ("youtube.com" in low or "youtu.be" in low) and not yt:
            yt = u
        if u:
            links.append(u)
    return TikTokProfileData(
        username=user.get("uniqueId"),
        display_name=user.get("nickname"),
        followers=safe_int(stats.get("followerCount")),
        following=safe_int(stats.get("followingCount")),
        video_count=safe_int(stats.get("videoCount")),
        bio=user.get("signature") or "",
        bio_link=", ".join(links) if links else None,
        instagram=ig,
        youtube=yt,
        last_post_dt=last_dt,
        recent_post_count=count,
        sample_likes=likes,
        sample_comments=comments,
        sample_views=views,
        raw={"heartCount": stats.get("heartCount")},
    )


class TikTokWebSource:
    name = "tiktok_web"

    def __init__(self, settings, logger=None):
        self.browser = AgentBrowser(session="research", timeout=settings["network"].get("timeout_seconds", 25), logger=logger)
        self.active_days = settings["activity"].get("active_days", 60)
        self.recent_days = settings["activity"].get("recent_days", 90)
        self.log = logger
        self.failures = 0
        self.max_failures = settings["network"].get("blocked_source_max_failures", 2)
        self._available = self.browser.available()

    def available(self):
        return self._available

    def disabled(self):
        return self.failures >= self.max_failures

    def note_block(self):
        self.failures += 1

    def fetch_profile(self, username):
        """Fetch a public TikTok profile. Returns (TikTokProfileData|None, status_str).

        Never retries a blocked source more than policy allows; raises
        BlockedSourceError when protection is detected.
        """
        if self.disabled():
            raise SourceError(self.name, "", "source disabled after repeated blocks")
        if not self.available():
            raise SourceError(self.name, "", "agent-browser unavailable")

        from ..utils.helpers import tiktok_profile_url
        url = tiktok_profile_url(username)
        try:
            with_retries(lambda: self.browser.visit(url), max_retries=1, backoff_s=5, logger=self.log)
        except BlockedSourceError as e:
            self.note_block()
            raise
        except RuntimeError as e:
            # navigation-level failure (DNS/connection timeout etc.)
            self.note_block()
            from ..browser.exceptions import UnreachableSourceError
            raise UnreachableSourceError(self.name, url, f"cannot reach tiktok.com: {str(e)[:160]}")

        payload_raw = self.browser.eval(EXTRACT_JS)
        try:
            payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
        except (json.JSONDecodeError, TypeError):
            payload = None
        parsed = _parse_payload(payload) if payload else None
        if parsed is None:
            block = self.browser.detect_block()
            if block:
                self.note_block()
                raise BlockedSourceError(self.name, url, f"profile gated by protection ({block})")
            return None, "no_public_data"
        return parsed, "ok"

    def activity_for(self, profile_data):
        if profile_data.last_post_dt is None:
            return "UNKNOWN", "no_date_available", None
        status, src = classify_activity(
            profile_data.last_post_dt, active_days=self.active_days, recent_days=self.recent_days
        )
        return status, src, profile_data.last_post_dt

    def close(self):
        self.browser.close()
