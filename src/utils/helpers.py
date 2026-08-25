import os
import logging
from datetime import datetime, timezone


def setup_logging(logs_dir="logs", run_id=None):
    os.makedirs(logs_dir, exist_ok=True)
    name = f"run_{run_id}.log" if run_id else "app.log"
    path = os.path.join(logs_dir, name)
    logger = logging.getLogger("ctr")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    fh = logging.FileHandler(path, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


def now_utc():
    return datetime.now(timezone.utc)


def iso(dt):
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    return dt.astimezone(timezone.utc).isoformat()


def parse_date(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e12:
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    s = str(value).strip()
    if s.isdigit():
        return parse_date(int(s))
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y", "%d-%m-%Y"):
        try:
            dt = datetime.strptime(s.replace("Z", "+0000") if fmt.endswith("%z") and s.endswith("Z") else s, fmt)
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
        except ValueError:
            continue
    return None


def days_since(dt, ref=None):
    if dt is None:
        return None
    ref = ref or now_utc()
    delta = ref - dt
    return max(0, round(delta.total_seconds() / 86400))


def normalize_username(handle_or_url):
    s = (handle_or_url or "").strip().lower()
    if not s:
        return None
    for prefix in ("https://www.tiktok.com/@", "https://tiktok.com/@", "www.tiktok.com/@", "tiktok.com/@"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    s = s.split("?")[0].split("/")[0]
    s = s.lstrip("@").strip()
    s = "".join(c for c in s if c.isalnum() or c in "._-")
    while ".." in s:
        s = s.replace("..", ".")
    s = s.strip(".-")
    return s or None


def tiktok_profile_url(username):
    u = normalize_username(username)
    return f"https://www.tiktok.com/@{u}" if u else None


def extract_tiktok_username(url):
    if not url or "tiktok.com" not in url.lower():
        return None
    return normalize_username(url)


def safe_int(value):
    try:
        if value is None:
            return None
        if isinstance(value, int):
            return value
        s = str(value).strip().lower().replace(",", "")
        mult = 1
        if s.endswith("k"):
            mult, s = 1000, s[:-1]
        elif s.endswith("m"):
            mult, s = 1000000, s[:-1]
        elif s.endswith("b"):
            mult, s = 1000000000, s[:-1]
        if "." in s:
            return int(round(float(s) * mult))
        return int(s) * mult
    except (ValueError, TypeError):
        return None


class RateLimiter:
    def __init__(self, delay_seconds=2.5):
        self.delay = max(0.0, float(delay_seconds))
        import time as _time
        self._time = _time
        self._last = {}

    def wait(self, key="global"):
        t = self._time.monotonic()
        elapsed = t - self._last.get(key, 0)
        remaining = self.delay - elapsed
        if remaining > 0:
            self._time.sleep(remaining)
        self._last[key] = self._time.monotonic()
