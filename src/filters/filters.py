import re


def classify_followers(followers, min_followers=2000, max_followers=10000):
    if followers is None:
        return "UNKNOWN"
    try:
        f = int(followers)
    except (ValueError, TypeError):
        return "UNKNOWN"
    if f >= max_followers:
        return "TOO_LARGE"
    if f >= min_followers:
        return "QUALIFIED"
    return "TOO_SMALL"


def classify_activity(last_post_date, active_days=60, recent_days=90, days=None):
    if days is None and last_post_date is None:
        return "UNKNOWN", "no_date_available"
    from ..utils.helpers import days_since
    d = days if days is not None else days_since(last_post_date)
    if d is None:
        return "UNKNOWN", "no_date_available"
    if d <= active_days:
        return "ACTIVE", f"last_relevant_post_{d}_days_ago"
    if d <= recent_days:
        return "RECENT", f"last_relevant_post_{d}_days_ago"
    return "INACTIVE", f"last_relevant_post_{d}_days_ago"


def _compile_terms(terms):
    out = []
    for t in terms:
        t = t.strip()
        if not t:
            continue
        escaped = re.escape(t)
        if re.fullmatch(r"[a-z0-9 ]+", t, flags=re.IGNORECASE):
            pattern = rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])"
        else:
            pattern = escaped
        out.append((t, re.compile(pattern)))
    return out


class CaliforniaClassifier:
    def __init__(self, strong_terms, weak_terms):
        self.strong = _compile_terms(strong_terms)
        self.weak = [
            (label, re.compile(spec["pattern"], 0 if spec.get("case_sensitive") else re.IGNORECASE))
            for label, spec in weak_terms.items()
        ]

    def classify(self, texts, max_bio_chars=600):
        evidence = []
        strong_hits = set()
        weak_hits = set()
        total_chars = 0
        for text in texts or []:
            if not text:
                continue
            total_chars += len(text)
            snippet = text[:2000]
            for term, rx in self.strong:
                if rx.search(snippet):
                    strong_hits.add(term)
                    evidence.append(term)
            for label, rx in self.weak:
                m = rx.search(snippet[:max_bio_chars] if label == "CA" else snippet)
                if m:
                    weak_hits.add(m.group(0) if label == "CA" else label)
                    evidence.append(m.group(0))
        all_hits = sorted(set(evidence), key=str.lower)
        if not all_hits:
            level = "LOW" if total_chars >= 60 else "UNKNOWN"
            return level, "", []
        if strong_hits:
            level = "HIGH"
        elif len(all_hits) >= 2 or any(h.upper() != "CA" for h in all_hits):
            level = "HIGH"
        else:
            level = "MEDIUM"
        return level, "; ".join(all_hits[:10]), all_hits

    def classify_with_context(self, texts, username_hint=None):
        combined = list(texts or [])
        if username_hint:
            combined.insert(0, username_hint.replace("_", " ").replace(".", " "))
        return self.classify(combined)


class TruckingClassifier:
    def __init__(self, topics, core_terms, strong_terms,
                 high_min_strong=2, medium_min_terms=1):
        self.topics = {name: _compile_terms(terms) for name, terms in topics.items()}
        self.core = _compile_terms(core_terms)
        self.strong = _compile_terms(strong_terms)
        self.high_min_strong = high_min_strong
        self.medium_min_terms = medium_min_terms

    def classify(self, texts):
        blob_parts = [t for t in (texts or []) if t]
        if not blob_parts:
            return "EXCLUDE", [], ""
        blob = " \n ".join(blob_parts)[:6000].lower()
        matched_core = sorted({t for t, rx in self.core if rx.search(blob)}, key=len, reverse=True)
        strong_matched = sorted({t for t, rx in self.strong if rx.search(blob)})
        topics_found = []
        for topic in ["CDL", "owner_operator", "fleet", "freight", "dispatch",
                      "diesel", "otr", "reefer", "dry_van", "flatbed", "tanker",
                      "truck_reviews", "truck_life", "truck_driving"]:
            terms = self.topics.get(topic, [])
            hits = [t for t, rx in terms if rx.search(blob)]
            if hits:
                topics_found.append(topic)
        n_topics = len(topics_found)
        n_strong = len(strong_matched)
        total_signals = n_topics + len(matched_core)
        if n_strong >= self.high_min_strong or n_topics >= 3:
            level = "HIGH"
        elif total_signals >= self.medium_min_terms:
            level = "MEDIUM"
        else:
            level = "EXCLUDE"
        return level, topics_found, ", ".join(sorted(set(matched_core + strong_matched))[:12])


def classify_california(texts, classifier=None, **kwargs):
    if classifier is None:
        raise ValueError("classifier instance required")
    return classifier.classify(texts)


def classify_trucking(texts, classifier=None, **kwargs):
    if classifier is None:
        raise ValueError("classifier instance required")
    return classifier.classify(texts)
