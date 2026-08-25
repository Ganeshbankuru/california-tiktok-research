LEVEL_ORDER = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "UNKNOWN": 0}


def _engagement_level(ratio, thresholds):
    if ratio is None:
        return None
    if ratio >= thresholds["high_min_ratio"]:
        return "HIGH"
    if ratio >= thresholds["medium_min_ratio"]:
        return "MEDIUM"
    if ratio >= thresholds["low_min_ratio"]:
        return "LOW"
    return None


def compute_engagement(likes, comments, views):
    if not views:
        return None, "UNKNOWN", "insufficient_data"
    try:
        views_n = float(views)
    except (TypeError, ValueError):
        return None, "UNKNOWN", "insufficient_data"
    if views_n <= 0:
        return None, "UNKNOWN", "insufficient_data"
    inter = 0.0
    known = False
    if likes:
        inter += float(likes)
        known = True
    if comments:
        inter += float(comments)
        known = True
    if not known:
        return None, "UNKNOWN", "insufficient_data"
    ratio = inter / views_n
    return round(ratio, 4), "KNOWN", f"{ratio:.2%}_of_views"


def compute_scores(candidate, settings):
    weights = settings["scoring_weights"]
    levels = settings["level_scores"]
    breakdown = {}

    ca = candidate.get("california_relevance") or "UNKNOWN"
    ca_pts = levels["california"].get(ca, 0)
    breakdown["california"] = {"level": ca, "points": ca_pts, "max": weights["california_relevance"]}

    tr = candidate.get("trucking_relevance") or "UNKNOWN"
    tr_pts = levels["trucking"].get(tr, 0)
    breakdown["trucking"] = {"level": tr, "points": tr_pts, "max": weights["trucking_relevance"]}

    act = candidate.get("activity_status") or "UNKNOWN"
    act_pts = levels["activity"].get(act, 0)
    breakdown["activity"] = {"level": act, "points": act_pts, "max": weights["activity"]}

    followers = candidate.get("followers")
    fit_pts = 0
    if followers is not None and 2000 <= followers < 10000:
        fit_pts = weights["audience_fit"]
    breakdown["audience_fit"] = {
        "points": fit_pts,
        "max": weights["audience_fit"],
        "followers": followers,
        "note": "10 only when follower count verified within 2k-10k band",
    }

    eng_ratio = candidate.get("_engagement_ratio")
    thr = levels["engagement_thresholds"]
    lvl = _engagement_level(eng_ratio, thr) if eng_ratio is not None else None
    eng_pts = levels["engagement"][lvl] if lvl else levels["engagement"]["UNKNOWN"]
    breakdown["engagement"] = {
        "points": eng_pts,
        "max": weights["engagement"],
        "ratio": eng_ratio,
        "status": candidate.get("engagement_status", "UNKNOWN"),
    }

    potential_pts = 0
    reasons = []
    bio = candidate.get("bio") or ""
    links = candidate.get("other_social_links") or []
    contact = candidate.get("public_contact")
    if contact:
        potential_pts += 5
        reasons.append("public contact available")
    if links:
        potential_pts += 2
        reasons.append(f"{len(links)} cross-platform link(s)")
    topics = candidate.get("trucking_topics") or []
    if len(topics) >= 3:
        potential_pts += 3
        reasons.append(f"{len(topics)} distinct trucking niches")
    elif len(topics) >= 1:
        potential_pts += 1
        reasons.append(f"{len(topics)} trucking niche(s)")
    potential_pts = min(potential_pts, weights["marketing_potential"])
    breakdown["marketing_potential"] = {"points": potential_pts, "max": weights["marketing_potential"], "reasons": reasons}

    marketing_score = (
        breakdown["california"]["points"]
        + breakdown["trucking"]["points"]
        + breakdown["activity"]["points"]
        + breakdown["audience_fit"]["points"]
        + breakdown["engagement"]["points"]
        + breakdown["marketing_potential"]["points"]
    )
    marketing_score = min(round(marketing_score, 1), 100.0)

    checks = []
    checks.append(("followers_verified", followers is not None))
    checks.append(("profile_bio_present", bool(bio)))
    checks.append(("activity_date_verified", candidate.get("last_post_date") is not None))
    checks.append(("relevance_assessed", LEVEL_ORDER.get(tr, 0) > 0))
    checks.append(("engagement_known", candidate.get("engagement_status") == "KNOWN"))
    checks.append(("discovery_source_recorded", bool(candidate.get("_source_count"))))
    passed = sum(1 for _, ok in checks if ok)
    confidence = round(passed / len(checks) * 100.0, 1)

    return marketing_score, confidence, {"breakdown": breakdown, "confidence_checks": dict(checks)}
