"""Turn the shared master list into each person's own watch list, based on their preferences."""
from __future__ import annotations

import pandas as pd

from . import store
from .sponsors import ROLE_FAMILIES, families_for

MASTER_FILE = "companies_master.csv"


def master_path():
    return store.ROOT / "data" / MASTER_FILE  # shared by everyone; never personal


def load_master() -> pd.DataFrame | None:
    p = master_path()
    if not p.exists():
        return None
    df = pd.read_csv(p, dtype=str).fillna("")
    for c in ("ats", "token", "states", "cities"):
        if c not in df:
            df[c] = ""
    df = enrich(df)  # include job pages found since the master file was last saved
    # location data always comes from the newest ranked file, so an older list never needs rebuilding
    if ranked_path().exists():
        rk = pd.read_csv(ranked_path(), dtype=str, usecols=lambda c: c in ("company", "states", "cities")).fillna("")
        if "cities" in rk:
            by = rk.drop_duplicates("company").set_index("company")
            df["states"] = df["company"].map(by["states"]).fillna(df["states"])
            df["cities"] = df["company"].map(by["cities"]).fillna(df["cities"])
    df["watchable"] = (df["ats"].str.strip() != "") & (df["token"].str.strip() != "")
    for c in ["entry_level_filings", "all_h1b_filings"] + [f"fam_{f}" for f in ROLE_FAMILIES]:
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(int)
    if "sponsors" not in df:
        df["sponsors"] = (df.get("all_h1b_filings", 0) > 0) if "all_h1b_filings" in df else False
    else:
        df["sponsors"] = df["sponsors"].astype(str).str.lower().isin(["true", "1", "yes"])
    return df


def user_families(roles: list[str]) -> list[str]:
    out = []
    for r in roles:
        out += families_for(r) or (["analyst"] if "analyst" in r.lower() else [])
    return sorted(set(out))


def industries_in(df: pd.DataFrame) -> list[str]:
    return sorted({i.strip() for v in df["industry"] for i in str(v).split(";") if i.strip()})


US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming"}
_NAME_TO_CODE = {v.lower(): k for k, v in US_STATES.items()}


def _counts(field: str) -> dict:
    out = {}
    for part in str(field or "").split(";"):
        if ":" in part:
            k, v = part.rsplit(":", 1)
            try:
                out[k.strip()] = int(v)
            except ValueError:
                pass
    return out


METROS = {
    "Seattle area": ["Seattle, WA", "Redmond, WA", "Bellevue, WA", "Kirkland, WA", "Bothell, WA", "Tacoma, WA",
                     "Renton, WA", "Issaquah, WA", "Everett, WA"],
    "Bay Area": ["San Francisco, CA", "San Jose, CA", "Mountain View, CA", "Palo Alto, CA", "Menlo Park, CA",
                 "Sunnyvale, CA", "Santa Clara, CA", "Cupertino, CA", "Oakland, CA", "Redwood City, CA",
                 "San Mateo, CA", "Fremont, CA", "Milpitas, CA", "South San Francisco, CA", "Emeryville, CA",
                 "Berkeley, CA", "Pleasanton, CA", "San Bruno, CA", "Foster City, CA", "Los Gatos, CA"],
    "Los Angeles area": ["Los Angeles, CA", "Santa Monica, CA", "Culver City, CA", "Irvine, CA", "Pasadena, CA",
                         "Burbank, CA", "Long Beach, CA", "Torrance, CA", "El Segundo, CA", "Glendale, CA",
                         "Hawthorne, CA", "Costa Mesa, CA", "Newport Beach, CA", "Playa Vista, CA", "Anaheim, CA"],
    "San Diego area": ["San Diego, CA", "Carlsbad, CA", "La Jolla, CA"],
    "New York City area": ["New York, NY", "Brooklyn, NY", "Jersey City, NJ", "Hoboken, NJ", "Newark, NJ",
                           "Stamford, CT", "White Plains, NY", "Long Island City, NY"],
    "Boston area": ["Boston, MA", "Cambridge, MA", "Waltham, MA", "Burlington, MA", "Somerville, MA",
                    "Lexington, MA", "Woburn, MA", "Bedford, MA"],
    "Chicago area": ["Chicago, IL", "Evanston, IL", "Naperville, IL", "Schaumburg, IL", "Deerfield, IL",
                     "Oak Brook, IL", "Northbrook, IL"],
    "Washington, D.C. area": ["Washington, DC", "Arlington, VA", "McLean, VA", "Reston, VA", "Herndon, VA",
                              "Alexandria, VA", "Tysons, VA", "Bethesda, MD", "Rockville, MD", "Chantilly, VA"],
    "Austin area": ["Austin, TX", "Round Rock, TX"],
    "Dallas-Fort Worth area": ["Dallas, TX", "Irving, TX", "Plano, TX", "Richardson, TX", "Fort Worth, TX",
                               "Frisco, TX", "Addison, TX"],
    "Houston area": ["Houston, TX", "The Woodlands, TX", "Sugar Land, TX"],
    "Atlanta area": ["Atlanta, GA", "Alpharetta, GA", "Duluth, GA", "Marietta, GA"],
    "Denver area": ["Denver, CO", "Boulder, CO", "Englewood, CO", "Broomfield, CO"],
    "Phoenix area": ["Phoenix, AZ", "Tempe, AZ", "Scottsdale, AZ", "Chandler, AZ"],
    "Miami area": ["Miami, FL", "Fort Lauderdale, FL", "Boca Raton, FL"],
    "Salt Lake City area": ["Salt Lake City, UT", "Lehi, UT", "Draper, UT", "South Jordan, UT"],
    "Philadelphia area": ["Philadelphia, PA", "King Of Prussia, PA", "Malvern, PA", "Wayne, PA"],
    "Pittsburgh area": ["Pittsburgh, PA"],
    "Raleigh-Durham area": ["Raleigh, NC", "Durham, NC", "Cary, NC", "Morrisville, NC", "Research Triangle Park, NC"],
    "Minneapolis area": ["Minneapolis, MN", "Saint Paul, MN", "St. Paul, MN", "Eden Prairie, MN"],
}


def location_options(master: pd.DataFrame, top_cities: int = 3000) -> list[str]:
    """Metro areas, then every state, then the cities where these companies hire (e.g. 'Seattle, WA')."""
    tally = {}
    for v in master.get("cities", pd.Series(dtype=str)):
        for city, n in _counts(v).items():
            if city and not city.startswith(","):
                tally[city] = tally.get(city, 0) + n
    cities = [c for c, _ in sorted(tally.items(), key=lambda x: -x[1])[:top_cities]]
    return list(METROS) + sorted(US_STATES.values()) + sorted(cities)


def default_locations(settings_locations: list[str], options: list[str]) -> list[str]:
    """Turn Step 4 answers like 'California', 'CA', 'San Francisco' into filter choices."""
    picked = []
    for loc in settings_locations or []:
        l = loc.strip().lower()
        code = l.upper() if l.upper() in US_STATES else _NAME_TO_CODE.get(l)
        if code and US_STATES[code] not in picked:
            picked.append(US_STATES[code])
            continue
        l = {"nyc": "new york city", "sf": "bay area", "la": "los angeles", "dc": "washington, d.c."}.get(l, l)
        metro = next((m for m in METROS if m.lower().replace(" area", "") == l.replace(" area", "")
                      or METROS[m][0].split(",")[0].lower() == l), None)
        if metro:
            if metro not in picked:
                picked.append(metro)
            continue
        for o in options:
            if "," in o and o.split(",")[0].strip().lower() == l and o not in picked:
                picked.append(o)
    return picked


def location_score(row, locations: list[str]) -> int:
    """How many entry-level filings this company has in the chosen states or cities."""
    if not locations:
        return 1
    st, ct = _counts(row.get("states", "")), _counts(row.get("cities", ""))
    score = 0
    for loc in locations:
        if loc in METROS:
            score += sum(ct.get(c, 0) for c in METROS[loc])
            continue
        code = _NAME_TO_CODE.get(loc.lower())
        score += st.get(code, 0) if code else ct.get(loc, 0)
    return score


def _filtered(master: pd.DataFrame, needs_sponsorship: bool, industries: list[str], exclude_industries: list[str],
              locations: list[str]) -> pd.DataFrame:
    df = master.copy()
    if needs_sponsorship:
        df = df[df["sponsors"]]
    ind = df["industry"].str.lower()
    if industries:
        df = df[ind.apply(lambda v: any(i.lower() in v for i in industries))]
        ind = df["industry"].str.lower()
    if exclude_industries:
        df = df[~ind.apply(lambda v: any(b.lower() in v for b in exclude_industries if b))]
    # location ranks, it doesn't cut: companies that hired in your places come first, the list still fills up,
    # and the daily search only keeps job posts located where you said (Step 4)
    df["loc"] = df.apply(lambda r: location_score(r, locations), axis=1) if locations else 0
    return df


def build_user_list(master: pd.DataFrame, needs_sponsorship: bool, industries: list[str], exclude_industries: list[str],
                    roles: list[str], size_mix: dict, limit: int = 3000, locations: list[str] | None = None) -> pd.DataFrame:
    df = _filtered(master, needs_sponsorship, industries, exclude_industries, locations or [])
    df = df[df["watchable"]]  # every company in the list is one the agent can actually scan
    fams = user_families(roles)
    fam_cols = [f"fam_{f}" for f in fams if f"fam_{f}" in df]
    # rank: companies the agent can watch today first, then hiring for *your* kind of role,
    # then hiring in *your* places, then overall entry-level sponsorship
    df["match"] = (df["watchable"].astype(int) * 2_000_000 + (df["loc"] > 0).astype(int) * 1_000_000
                   + (df[fam_cols].sum(axis=1) if fam_cols else 0) * 10 + df["loc"] * 5
                   + df.get("entry_level_filings", 0))
    df = df.sort_values(["match", "company"], ascending=[False, True])
    total = sum(size_mix.values()) or 1
    picked = []
    for size, n in size_mix.items():
        quota = round(limit * n / total)
        picked.append(df[df["size"] == size].head(quota))
    out = pd.concat(picked) if picked else df.head(0)
    if len(out) < limit:  # fill from any size if a tier ran short
        rest = df[~df.index.isin(out.index)].head(limit - len(out))
        out = pd.concat([out, rest])
    out = out.sort_values("match", ascending=False)
    return out[["company", "ats", "token", "size", "industry"]].reset_index(drop=True)


def preview_counts(master: pd.DataFrame, needs_sponsorship: bool, industries: list[str], exclude_industries: list[str],
                   locations: list[str] | None = None) -> tuple[int, int, int]:
    """(companies available, how many hired in your places, how many the agent can read job posts from today)"""
    df = _filtered(master, needs_sponsorship, industries, exclude_industries, locations or [])
    return len(df), int((df["loc"] > 0).sum()) if locations else len(df), int(df["watchable"].sum())


def _norm(name: str) -> str:
    import re

    n = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower())
    n = re.sub(r"\b(inc|llc|corp|corporation|co|company|ltd|limited|lp|plc|na|the|technologies|technology|labs|holdings|usa)\b", " ", n)
    return re.sub(r"\s+", " ", n).strip()


# ---------- background build (the owner clicks once and keeps working) ----------
import threading as _threading

BUILD = {"running": False, "frac": 0.0, "msg": "", "error": "", "done": False}


def start_background_build() -> bool:
    """Start the job-page lookup in the background. Returns False if it's already running."""
    if BUILD["running"]:
        return False
    BUILD.update(running=True, frac=0.0, msg="Starting...", error="", done=False)

    def work():
        try:
            build_master([], progress=lambda f, m: BUILD.update(frac=min(max(f or 0.0, 0.0), 1.0), msg=m))
            BUILD.update(done=True, msg="Done")
        except Exception as e:  # noqa: BLE001
            BUILD["error"] = str(e)[:200]
        finally:
            BUILD["running"] = False

    _threading.Thread(target=work, daemon=True).start()
    return True


def _cache_file():
    """v3 = also checks Workday, SmartRecruiters, Workable and company websites. Pages found earlier are kept;
    companies with no page found earlier are checked again with the new methods."""
    import json

    v3 = store.ROOT / "data" / "board_cache_v3.json"
    if not v3.exists():
        old = {}
        for n in ("board_cache_v2.json", "board_cache.json"):
            f = store.ROOT / "data" / n
            if f.exists():
                old.update({k: v for k, v in json.loads(f.read_text()).items() if v})
        v3.write_text(json.dumps(old))
    return v3


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    """Fill in job pages found since this list was built (from the lookup's saved results), so every run uses
    every company it can read, with no rebuild needed."""
    import json

    try:
        cache = json.loads(_cache_file().read_text())
    except Exception:
        return df
    df = df.copy()
    for c in ("ats", "token"):
        if c not in df:
            df[c] = ""
    empty = (df["ats"].astype(str).str.strip() == "") | (df["token"].astype(str).str.strip() == "")
    for i in df.index[empty]:
        hit = cache.get(df.at[i, "company"])
        if hit:
            df.at[i, "ats"], df.at[i, "token"] = hit.get("ats", ""), hit.get("token", "")
    return df


def auto_user_list(settings: dict, current: pd.DataFrame | None = None) -> pd.DataFrame | None:
    """Rebuild the person's list from everything found so far (up to their target, e.g. 3,000), keeping companies
    they added themselves. Runs before every search, so new job pages join the list automatically."""
    master = load_master()
    if master is None:
        return None
    lst = build_user_list(master, settings.get("needs_sponsorship", True), settings.get("industries", []),
                          settings.get("exclude_industries", []), settings.get("roles", []),
                          settings.get("size_mix", {"big": 10, "mid": 10, "small": 10}),
                          int(settings.get("list_limit", 2000)), settings.get("list_locations", []))
    if current is not None and len(current):
        cur = enrich(current)
        in_master = set(master["company"].str.lower())
        mine = cur[(cur["ats"].astype(str).str.strip() != "") & (cur["token"].astype(str).str.strip() != "")
                   & ~cur["company"].str.lower().isin(in_master)
                   & ~cur["company"].str.lower().isin(lst["company"].str.lower())]
        lst = pd.concat([mine[lst.columns.intersection(mine.columns)], lst], ignore_index=True)
    return lst


def auto_continue() -> bool:
    """Owner's laptop: keep finding job pages in the background whenever the app is open, until all are checked."""
    if BUILD["running"] or BUILD.get("auto_tried"):
        return BUILD["running"]
    BUILD["auto_tried"] = True
    chk, _, tot = lookup_status()
    if tot and chk < tot:
        return start_background_build()
    return False


def lookup_status(limit: int = 8000) -> tuple[int, int, int]:
    """(companies checked, job pages found, companies to check) for the owner's progress line."""
    import json

    total = 0
    if ranked_path().exists():
        total = min(limit, sum(1 for _ in open(ranked_path(), encoding="utf-8")) - 1)
    cf = _cache_file()
    cache = json.loads(cf.read_text()) if cf.exists() else {}
    return len(cache), sum(1 for v in cache.values() if v), total


def ranked_path():
    return store.ROOT / "data" / "sponsors_ranked.csv"


def build_master(lca_paths: list[str], max_companies: int = 20000, progress=None, lookup_limit: int = 20000) -> pd.DataFrame:
    """One-time job for whoever runs the app: DOL filings -> ranked sponsors -> find job pages -> master list."""
    from .discover import discover_all
    from .sponsors import build_sponsor_list

    say = progress or (lambda f, m: None)
    ranked = ranked_path()
    if not lca_paths and ranked.exists():  # government file already read and ranked (data/sponsors_ranked.csv)
        sp = pd.read_csv(ranked, dtype={"company": str, "size": str, "industry": str}).fillna("")
    else:
        say(0.0, "Reading DOL filings (a few minutes)...")
        sp = pd.DataFrame(build_sponsor_list(lca_paths, states=(), max_companies=max_companies,
                                             progress=lambda f, m: say(0.05, m)))
        sp.to_csv(ranked, index=False)
    sp["sponsors"] = True
    sp = sp.head(lookup_limit).copy()  # ranked best-first; the top ones cover what any user's list needs
    cache_file = _cache_file()
    # the person's own list first (that's what their daily search uses), then the rest of the sponsors
    try:
        mine = [c for c in store.load_companies()["company"].astype(str) if c]
    except Exception:
        mine = []
    in_master = set(sp["company"])
    mine = [c for c in dict.fromkeys(mine)]
    # next: sponsors that match this person's places, roles and industries (they're the ones that can join the list)
    try:
        s = store.load_settings()
        m = sp.copy()
        for col in ("states", "cities"):
            if col not in m:
                m[col] = ""
        m["watchable"] = False
        f = _filtered(m, s.get("needs_sponsorship", True), s.get("industries", []), s.get("exclude_industries", []),
                      s.get("list_locations", []))
        fams = [f"fam_{x}" for x in user_families(s.get("roles", [])) if f"fam_{x}" in f]
        for c in fams:
            f[c] = pd.to_numeric(f[c], errors="coerce").fillna(0)
        f["_m"] = (f["loc"] > 0).astype(int) * 1000 + (f[fams].sum(axis=1) if fams else 0)
        likely = [c for c in f.sort_values("_m", ascending=False)["company"] if c not in set(mine)]
    except Exception:
        likely = []
    rest = [c for c in dict.fromkeys(likely + list(sp["company"])) if c not in set(mine)]
    master = None
    stages = [
        (mine, False, "Your companies, quick pass"),
        (rest, False, "All other sponsors, quick pass"),
        (mine, True, "Your companies, checking their websites"),
        (rest, True, "All other sponsors, checking their websites"),
    ]
    for k, (group, deep, label) in enumerate(stages):
        if not group:
            continue
        lo, hi = k / len(stages), (k + 1) / len(stages)
        cache = discover_all(group, cache_file, deep_names=set(group) if deep else None, record_misses=deep,
                             progress=lambda f, m, lo=lo, hi=hi, label=label: say(
                                 lo + (hi - lo) * f, f"{label}: {m.replace('Checked ', 'checked ')}"))
        master = _assemble(sp, cache)  # saved after each stage
        say(hi, f"{label} done: {sum(1 for v in cache.values() if v):,} job pages found so far")
    say(1.0, "Done")
    return master


def _assemble(sp: pd.DataFrame, cache: dict) -> pd.DataFrame:
    sp = sp.copy()
    sp["ats"] = sp["company"].map(lambda c: (cache.get(c) or {}).get("ats", ""))
    sp["token"] = sp["company"].map(lambda c: (cache.get(c) or {}).get("token", ""))
    # keep the hand-picked starter companies too, marking the ones that also appear in the DOL data
    starter = pd.read_csv(store.STARTER, dtype=str).fillna("")  # the shared starter, never anyone's own list
    sp_names = {_norm(c): i for i, c in sp["company"].items()}
    extra = []
    for _, r in starter.iterrows():
        i = sp_names.get(_norm(r["company"]))
        if i is not None:
            if not sp.at[i, "ats"]:
                sp.at[i, "ats"], sp.at[i, "token"] = r.get("ats", ""), r.get("token", "")
            if r.get("industry"):
                sp.at[i, "industry"] = r["industry"]
        else:
            extra.append({**r.to_dict(), "sponsors": False, "entry_level_filings": 0, "all_h1b_filings": 0})
    master = pd.concat([sp, pd.DataFrame(extra)], ignore_index=True).fillna("")
    master.to_csv(master_path(), index=False)
    return master


DOL_PAGE = "https://www.dol.gov/agencies/eta/foreign-labor/performance"
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def latest_dol_file_url() -> str:
    """Newest H-1B (LCA) disclosure file listed on the Department of Labor's data page."""
    import re
    import requests

    html = requests.get(DOL_PAGE, headers=BROWSER_HEADERS, timeout=30).text
    links = re.findall(r'href="([^"]*LCA_Disclosure_Data_FY(\d{4})_Q(\d)\.xlsx)"', html, re.I)
    if not links:
        raise RuntimeError("couldn't find the data file on the Department of Labor page")
    href, fy, q = max(links, key=lambda x: (int(x[1]), int(x[2])))
    return href if href.startswith("http") else "https://www.dol.gov/" + href.lstrip("/")


def download_dol_file(dest_dir, progress=None) -> str:
    """Download the newest file (the current fiscal year so far, ~250 MB) to dest_dir. Returns its path."""
    import requests
    from pathlib import Path

    say = progress or (lambda f, m: None)
    url = latest_dol_file_url()
    name = url.rsplit("/", 1)[-1]
    dest = Path(dest_dir) / name
    if dest.exists() and dest.stat().st_size > 10_000_000:
        return str(dest)
    with requests.get(url, headers={**BROWSER_HEADERS, "Referer": DOL_PAGE}, stream=True, timeout=60) as r:
        r.raise_for_status()
        if "html" in r.headers.get("content-type", ""):
            raise RuntimeError("the Department of Labor site blocked the automatic download")
        total = int(r.headers.get("content-length") or 0)
        got = 0
        tmp = dest.with_suffix(".part")
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
                got += len(chunk)
                if total:
                    say(got / total, f"Downloading government data: {got // 1_000_000} of {total // 1_000_000} MB")
        tmp.rename(dest)
    return str(dest)
