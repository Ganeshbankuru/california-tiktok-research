import csv
import json
import os

from ..utils.config import project_path


EXPORT_COLUMNS = [
    "rank", "username", "display_name", "platform", "followers", "following",
    "video_count", "bio", "recent_post_date", "recent_post_count",
    "activity_status", "trucking_relevance", "california_relevance",
    "california_evidence", "trucking_topics", "seed_account", "all_seed_sources",
    "discovery_method", "engagement_indicator", "marketing_score",
    "confidence_score", "profile_url", "public_contact", "other_social_links",
    "source_urls", "date_checked",
]


def _parse_json_field(value):
    if not value:
        return []
    try:
        v = json.loads(value)
        return v if isinstance(v, list) else [str(v)]
    except (TypeError, json.JSONDecodeError):
        return [str(value)]


def build_rows(db):
    rows = []
    for i, r in enumerate(db.qualified_rows(), start=1):
        c = dict(r)
        sources = db.sources_for(c["id"])
        seeds = list(dict.fromkeys([s["seed_account"] for s in sources if s["seed_account"]]))
        methods = list(dict.fromkeys([s["discovery_method"] for s in sources]))
        source_urls = list(dict.fromkeys([s["source_url"] for s in sources if s["source_url"]]))
        other_links = _parse_json_field(c.get("other_social_links"))
        topics = _parse_json_field(c.get("trucking_topics"))
        rows.append({
            "rank": i,
            "username": f"@{c['username']}" if not str(c["username"]).startswith("@") else c["username"],
            "display_name": c.get("display_name") or "",
            "platform": c.get("platform") or "tiktok",
            "followers": c.get("followers"),
            "following": c.get("following"),
            "video_count": c.get("video_count"),
            "bio": (c.get("bio") or "")[:500],
            "recent_post_date": (c.get("recent_post_date") or "")[:10],
            "recent_post_count": c.get("recent_post_count"),
            "activity_status": c.get("activity_status") or "UNKNOWN",
            "trucking_relevance": c.get("trucking_relevance") or "UNKNOWN",
            "california_relevance": c.get("california_relevance") or "UNKNOWN",
            "california_evidence": c.get("california_evidence") or "",
            "trucking_topics": "; ".join(topics),
            "seed_account": "; ".join(seeds[:1]) if seeds else "",
            "all_seed_sources": "; ".join(seeds),
            "discovery_method": "; ".join(methods),
            "engagement_indicator": c.get("engagement_indicator") or "",
            "marketing_score": c.get("marketing_score"),
            "confidence_score": c.get("confidence_score"),
            "profile_url": c.get("profile_url"),
            "public_contact": c.get("public_contact") or "",
            "other_social_links": "; ".join(other_links),
            "source_urls": "; ".join(source_urls),
            "date_checked": (c.get("date_checked") or "")[:10],
        })
    return rows


def out_path(settings, filename):
    path = project_path(settings["app"].get("output_dir", "output"), filename)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def export_csv(rows, settings):
    path = out_path(settings, settings["export"]["csv_file"])
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=EXPORT_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return path


def export_xlsx(rows, settings):
    import pandas as pd
    path = out_path(settings, settings["export"]["xlsx_file"])
    df = pd.DataFrame(rows, columns=EXPORT_COLUMNS)
    df.to_excel(path, index=False, sheet_name="qualified_creators")
    return path


def export_json(rows, settings, extra=None):
    path = out_path(settings, settings["export"]["json_file"])
    payload = {"generated_at": None, **(extra or {}), "count": len(rows), "creators": rows}
    import datetime
    payload["generated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False, default=str)
    return path


def build_summary(db, settings):
    runs = db.query("SELECT * FROM research_runs ORDER BY start_time DESC LIMIT 5")
    total_candidates = db.one("SELECT COUNT(*) n FROM candidates")["n"]
    verified = db.one("SELECT COUNT(*) n FROM candidates WHERE verification_status='done' OR verification_status='done_no_data'")["n"]
    blocked = db.one("SELECT COUNT(*) n FROM candidates WHERE verification_status='blocked'")["n"]
    failed = db.one("SELECT COUNT(*) n FROM candidates WHERE verification_status='failed'")["n"]
    pending = db.one("SELECT COUNT(*) n FROM candidates WHERE verification_status='pending'")["n"]
    qualified = db.one("SELECT COUNT(*) n FROM candidates WHERE follower_status='QUALIFIED' AND verification_status='done'")["n"]
    too_small = db.one("SELECT COUNT(*) n FROM candidates WHERE follower_status='TOO_SMALL'")["n"]
    too_large = db.one("SELECT COUNT(*) n FROM candidates WHERE follower_status='TOO_LARGE'")["n"]
    unknown_f = db.one("SELECT COUNT(*) n FROM candidates WHERE follower_status='UNKNOWN'")["n"]
    active = db.one("SELECT COUNT(*) n FROM candidates WHERE activity_status='ACTIVE'")["n"]
    ca_high = db.one("SELECT COUNT(*) n FROM candidates WHERE california_relevance='HIGH'")["n"]
    tr_high = db.one("SELECT COUNT(*) n FROM candidates WHERE trucking_relevance='HIGH'")["n"]

    dup_removed = db.one(
        """SELECT COUNT(*) n FROM candidate_sources WHERE candidate_id IN (
             SELECT candidate_id FROM candidate_sources GROUP BY candidate_id HAVING COUNT(*) > 1)"""
    )["n"]

    per_seed = {}
    for row in db.query("SELECT seed_account, COUNT(DISTINCT candidate_id) n FROM candidate_sources WHERE seed_account IS NOT NULL GROUP BY seed_account"):
        per_seed[row["seed_account"]] = row["n"]

    errors = db.query("SELECT * FROM errors ORDER BY timestamp DESC LIMIT 50")
    processed = db.one("SELECT COUNT(*) n FROM processed_urls")["n"]

    top_rows = build_rows(db)[:25]

    lines = []
    lines.append("# California Trucking TikTok Creator Research Summary")
    lines.append("")
    latest_run = runs[0] if runs else None
    if latest_run:
        lines.append(f"- **Latest run ID:** {latest_run['run_id']}")
        lines.append(f"- **Run status:** {latest_run['status']}")
        lines.append(f"- **Started:** {latest_run['start_time']}")
        lines.append(f"- **Ended:** {latest_run['end_time'] or 'in progress'}")
    lines.append(f"- **Report generated:** {latest_run['end_time'] or 'now'}")
    lines.append("")
    lines.append("## Totals")
    lines.append(f"- Candidates discovered: {total_candidates}")
    lines.append(f"- Verified (data extracted): {verified}")
    lines.append(f"- Qualified (2k-10k followers): {qualified}")
    lines.append(f"- Rejected: TOO_SMALL={too_small}, TOO_LARGE={too_large}, UNKNOWN={unknown_f}")
    lines.append(f"- Active creators: {active}")
    lines.append(f"- California HIGH relevance: {ca_high}")
    lines.append(f"- Trucking HIGH relevance: {tr_high}")
    lines.append(f"- Blocked sources (candidates): {blocked}")
    lines.append(f"- Failed verifications: {failed}")
    lines.append(f"- Pending verification: {pending}")
    lines.append(f"- Duplicate discovery relationships preserved: {dup_removed}")
    lines.append(f"- URLs processed overall: {processed}")
    lines.append("")
    lines.append("## Creators discovered per seed account")
    if per_seed:
        for k in sorted(per_seed):
            lines.append(f"- {k}: {per_seed[k]}")
    else:
        lines.append("- none yet")
    lines.append("")
    lines.append("## Top 25 Qualified Creators")
    if top_rows:
        lines.append("| rank | username | followers | activity | trucking | california | score | confidence |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for r in top_rows:
            lines.append(
                f"| {r['rank']} | {r['username']} | {r['followers']} | {r['activity_status']} "
                f"| {r['trucking_relevance']} | {r['california_relevance']} | {r['marketing_score']} | {r['confidence_score']} |"
            )
    else:
        lines.append("- no qualified creators yet")
    lines.append("")
    lines.append("## Errors / unavailable sources")
    if errors:
        lines.append("| time | source | type | url | message |")
        lines.append("|---|---|---|---|---|")
        for e in errors:
            msg = (e["message"] or "").replace("|", "/").replace("\n", " ")[:160]
            url = (e["url"] or "").replace("|", "/")[:100]
            lines.append(f"| {e['timestamp'][:19]} | {e['source']} | {e['error_type']} | {url} | {msg} |")
    else:
        lines.append("- none recorded")
    lines.append("")
    lines.append("## Data-quality warnings")
    warns = []
    if unknown_f:
        warns.append(f"{unknown_f} candidates have UNKNOWN follower counts (could not be verified publicly).")
    pending_warn = pending
    if pending_warn:
        warns.append(f"{pending_warn} candidates remain unverified.")
    if blocked:
        warns.append(f"{blocked} profile(s) were blocked by anti-bot protection and were skipped per policy.")
    if failed:
        warns.append(f"{failed} profile(s) failed technical extraction.")
    if not warns:
        warns.append("none")
    for w in warns:
        lines.append(f"- {w}")
    lines.append("")
    return "\n".join(lines)


def write_summary(db, settings):
    path = out_path(settings, settings["export"]["summary_file"])
    text = build_summary(db, settings)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    return path


def export_all(db, settings):
    rows = build_rows(db)
    paths = {
        "csv": export_csv(rows, settings),
        "xlsx": export_xlsx(rows, settings),
        "json": export_json(rows, settings),
        "summary": write_summary(db, settings),
    }
    return paths, rows
