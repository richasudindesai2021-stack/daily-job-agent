"""Rule-based filters. These run before any AI call, so they cost nothing and are predictable."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

# Phrases that mean the employer will not support F-1 OPT / STEM OPT / H-1B candidates.
NO_SPONSOR_PATTERNS = [
    r"(will|do|does|can|are|is)\s*(not|n't)\s*(be\s*)?(able\s*to\s*)?(offer\s*|provide\s*)?(visa\s*)?sponsor",
    r"unable\s+to\s+(offer\s+|provide\s+)?(visa\s+)?sponsor",
    r"\bno\s+(visa\s+)?sponsorship",
    r"not\s+(eligible|available)\s+for\s+(visa\s+)?sponsorship",
    r"sponsorship\s+(is\s+)?not\s+(available|offered|provided)",
    r"without\s+(the\s+need\s+for\s+)?(current\s+or\s+future\s+|future\s+)?(visa\s+)?sponsorship",
    r"must\s+be\s+(a\s+)?u\.?\s?s\.?\s+citizen",
    r"u\.?s\.?\s+citizenship\s+(is\s+)?required",
    r"(active|current)\s+(security\s+)?clearance",
    r"security\s+clearance\s+(is\s+)?required",
    r"green\s+card\s+holders?\s+only",
    r"(not|n't)\s+(accept|consider)\s+(candidates\s+on\s+)?(opt|cpt|f-?1)",
]
SPONSOR_FRIENDLY_PATTERNS = [
    r"visa\s+sponsorship\s+(is\s+)?available",
    r"(will|can|able\s+to)\s+sponsor",
    r"sponsorship\s+(is\s+)?(available|offered|provided)",
    r"\bh-?1b\b",
    r"\bstem\s+opt\b",
]

DEFAULT_EXCLUDE_TITLE_WORDS = [
    "senior", "sr.", "sr ", "staff", "principal", "director", "head of", "vp", "vice president",
    "lead", "manager ii", "manager iii", "phd", "intern",
]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def title_matches(title: str, roles: list[str], exclude_words: list[str]) -> bool:
    t = " " + _norm(title) + " "
    if any((" " + w.lower().strip()) in t for w in exclude_words if w.strip()):
        return False
    for role in roles:
        words = [w for w in re.split(r"[\s/]+", _norm(role)) if w]
        if words and all(w in t for w in words):
            return True
    return False


def location_matches(job: dict, locations: list[str], allow_remote: bool) -> bool:
    loc = _norm(job.get("location", ""))
    remote_only = (job.get("remote") is True or "remote" in loc) and not any(
        _norm(l) in loc for l in locations if _norm(l) not in ("remote",)
    )
    if remote_only:
        return allow_remote
    if not locations:
        return True
    for l in locations:
        n = _norm(l)
        if not n:
            continue
        # match short state codes as whole words only ("CA", "NY", "WA")
        if len(n) <= 3:
            if re.search(rf"(^|[\s,(/-]){re.escape(n)}($|[\s,)/-])", loc):
                return True
        elif n in loc:
            return True
    return False


MAX_AGE_HOURS = 336  # hard limit: never show a role first posted more than 2 weeks ago
AGE_TIERS = (24, 36, 48, 72, 168, 336)  # newest first: older roles only fill spots the newer ones can't


def age_tier(job: dict, now: datetime | None = None) -> int:
    ts = job.get("posted_at")
    if ts is None:
        return len(AGE_TIERS)
    hours = ((now or datetime.now(timezone.utc)) - ts).total_seconds() / 3600
    return next((i for i, h in enumerate(AGE_TIERS) if hours <= h), len(AGE_TIERS))


def is_fresh(job: dict, max_age_hours: int, now: datetime | None = None) -> bool:
    max_age_hours = min(int(max_age_hours or MAX_AGE_HOURS), MAX_AGE_HOURS)
    ts = job.get("posted_at")
    if ts is None:
        return False
    now = now or datetime.now(timezone.utc)
    return now - ts <= timedelta(hours=max_age_hours)


def sponsorship_signal(text: str) -> str:
    """'blocked' | 'friendly' | 'unknown' based on the job description wording."""
    t = _norm(text)
    if any(re.search(p, t) for p in NO_SPONSOR_PATTERNS):
        return "blocked"
    if any(re.search(p, t) for p in SPONSOR_FRIENDLY_PATTERNS):
        return "friendly"
    return "unknown"


_MONEY = re.compile(r"\$\s?(\d{2,3}(?:,\d{3})+|\d{2,3}(?:\.\d+)?\s?[kK])")


def max_salary(text: str) -> int | None:
    """Highest annual dollar figure mentioned (ignores hourly-looking amounts)."""
    vals = []
    for m in _MONEY.findall(text or ""):
        m = m.replace(",", "").replace(" ", "")
        v = float(m[:-1]) * 1000 if m.lower().endswith("k") else float(m)
        if 20000 <= v <= 1_000_000:
            vals.append(int(v))
    return max(vals) if vals else None


def apply_filters(jobs: list[dict], s: dict, skip_urls: set[str], skip_companies: set[str], h1b_names: set[str] | None):
    """Returns (kept_jobs, counts_of_why_dropped)."""
    dropped = {"title": 0, "location": 0, "too old": 0, "no sponsorship": 0, "salary": 0, "industry": 0, "already seen": 0}
    kept = []
    seen = set()
    inds = [i.lower() for i in s.get("industries", []) if i]
    bad_inds = [i.lower() for i in s.get("exclude_industries", []) if i]
    for j in jobs:
        key = (j["company"].lower(), _norm(j["title"]), _norm(j["location"]))
        if key in seen:
            continue
        seen.add(key)
        if j["url"] in skip_urls or j["company"].lower() in skip_companies:
            dropped["already seen"] += 1
            continue
        ind = (j.get("industry") or "").lower()
        # companies you added by hand have no industry label: never drop them for that
        if (inds and ind and not any(i in ind for i in inds)) or (ind and any(b in ind for b in bad_inds)):
            dropped["industry"] += 1
            continue
        if not title_matches(j["title"], s["roles"], s.get("exclude_title_words", DEFAULT_EXCLUDE_TITLE_WORDS)):
            dropped["title"] += 1
            continue
        if not location_matches(j, s.get("locations", []), s.get("allow_remote", False)):
            dropped["location"] += 1
            continue
        if not is_fresh(j, int(s.get("max_age_hours", 24))):
            dropped["too old"] += 1
            continue
        sig = sponsorship_signal(j["description"])
        if s.get("needs_sponsorship", True) and sig == "blocked":
            dropped["no sponsorship"] += 1
            continue
        sal = max_salary(j.get("salary_text", "") + " " + j["description"])
        if sal and s.get("min_salary") and sal < int(s["min_salary"]):
            dropped["salary"] += 1
            continue
        j["sponsorship"] = sig
        j["salary_max"] = sal
        j["h1b_history"] = (_norm_company(j["company"]) in h1b_names) if h1b_names else None
        kept.append(j)
    return kept, dropped


def _norm_company(name: str) -> str:
    n = _norm(name)
    n = re.sub(r"[.,&']", " ", n)
    n = re.sub(r"\b(inc|llc|corp|corporation|co|ltd|technologies|technology|labs|holdings|usa|us)\b", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def load_h1b_names(csv_path: str) -> set[str]:
    """Reads the USCIS H-1B Employer Data Hub CSV (any year) and returns normalized employer names."""
    import pandas as pd

    df = pd.read_csv(csv_path, dtype=str, encoding_errors="ignore", sep=None, engine="python")
    col = next((c for c in df.columns if "employer" in c.lower() and "name" in c.lower()), None) or next(
        (c for c in df.columns if "employer" in c.lower()), df.columns[0]
    )
    names = set()
    for v in df[col].dropna():
        n = _norm_company(v)
        if n:
            names.add(n)
            names.add(n.split(" ")[0]) if len(n.split(" ")[0]) > 4 else None
    return names
