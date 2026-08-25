"""Diagnose what TikTok actually returns for a tag page.

Usage:  .venv\\Scripts\\python tools\\diag_tag.py [tag]
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.sources.tiktok import parse_tag_users_from_html  # noqa: E402
from src.browser.agent_browser import AgentBrowser  # noqa: E402

tag = sys.argv[1] if len(sys.argv) > 1 else "trucking"
b = AgentBrowser(session="diagtag")

url = f"https://www.tiktok.com/tag/{tag}"
print(f"opening {url}", flush=True)
b.open(url, extra_wait_ms=4000)
print("title:", b.title(), flush=True)
print("url:", b.current_url(), flush=True)
print("block_detected:", b.detect_block(), flush=True)

for i in range(3):
    b.scroll_bottom(pause_ms=2000)
    html = str(b.eval("document.documentElement.outerHTML") or "")
    users = parse_tag_users_from_html(html)
    print(f"round {i+1}: html_len={len(html)} users_found={len(users)}", flush=True)
    if users:
        print("sample:", users[:15], flush=True)
        break

body = b.eval("document.body ? document.body.innerText.slice(0, 600) : ''")
print("--- visible text preview ---", flush=True)
print(str(body)[:600], flush=True)

has_login = b.eval("/log in|log in to/i.test(document.body.innerText.slice(0,2000))")
print("login_wall_hint:", has_login, flush=True)
b.close()
print("done", flush=True)
