"""Type a company name, any spelling or capitalization; get back everything the agent needs.

For each name we figure out:
  - the real company name, with "Did you mean...?" choices when unsure (Wikipedia + Wikidata, free, no key)
  - whether it's a U.S. company
  - its website, industry and size (from employee count, or from how many roles it has open)
  - its job page: Greenhouse, Lever, Ashby or Workday, by trying likely names and by reading its careers page
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from urllib.parse import urljoin, urlparse

import requests

from .discover import find_board, slug_candidates
from .sources import fetch_company

UA = {"User-Agent": "DailyJobAgent/1.0 (personal job search tool; contact via project page)"}
T = 12
COMPANY_WORDS = ("company", "corporation", "retailer", "startup", "bank", "manufacturer", "firm", "platform", "service",
                 "brand", "maker", "developer", "provider", "business", "conglomerate", "enterprise", "subsidiary",
                 "software", "pharmaceutical", "biotechnology", "insurer", "airline", "restaurant", "chain", "agency",
                 "consultancy", "consulting", "studio", "marketplace", "e-commerce", "fashion house", "label", "group")

ATS_PATTERNS = [
    ("greenhouse", re.compile(r"greenhouse\.io/embed/job_board(?:/js)?\?for=([a-z0-9_-]+)", re.I)),
    ("greenhouse", re.compile(r"(?:boards|job-boards)\.greenhouse\.io/(?!embed)([a-z0-9_-]+)", re.I)),
    ("lever", re.compile(r"jobs\.lever\.co/([a-z0-9_.-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([a-z0-9_.%-]+)", re.I)),
    ("smartrecruiters", re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_-]+)", re.I)),
    ("workable", re.compile(r"apply\.workable\.com/(?!api|j/)([a-z0-9_-]+)", re.I)),
    ("workday", re.compile(r"(https?://[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?[A-Za-z0-9_-]+)", re.I)),
]


def _similar(a: str, b: str) -> float:
    def n(s):
        s = re.sub(r"\(.*?\)", "", (s or "").lower())
        s = re.sub(r"\b(inc|llc|corp|corporation|co|company|ltd|plc|the|group|holdings)\b\.?", "", s)
        return re.sub(r"[^a-z0-9]", "", s)
    return SequenceMatcher(None, n(a), n(b)).ratio()


# ---------------- who is this company? ----------------
def wiki_candidates(name: str, limit: int = 5) -> list[dict]:
    """[{qid, label, description}] for companies matching the name. Handles typos via Wikipedia's suggestions."""
    def search(q):
        r = requests.get("https://en.wikipedia.org/w/api.php", headers=UA, timeout=T, params={
            "action": "query", "format": "json", "generator": "search", "gsrsearch": q, "gsrlimit": limit,
            "prop": "pageprops|description", "ppprop": "wikibase_item", "redirects": 1})
        pages = (r.json().get("query") or {}).get("pages") or {}
        return sorted(pages.values(), key=lambda p: p.get("index", 99))

    pages = search(name)
    if not pages:  # likely a typo: ask Wikipedia what it thinks was meant
        r = requests.get("https://en.wikipedia.org/w/api.php", headers=UA, timeout=T, params={
            "action": "query", "format": "json", "list": "search", "srsearch": name, "srinfo": "suggestion", "srlimit": 1})
        sugg = ((r.json().get("query") or {}).get("searchinfo") or {}).get("suggestion")
        if sugg:
            pages = search(sugg)
    out = []
    for p in pages:
        qid = (p.get("pageprops") or {}).get("wikibase_item")
        desc = (p.get("description") or "").lower()
        if qid and any(w in desc for w in COMPANY_WORDS):
            out.append({"qid": qid, "label": re.sub(r"\s*\(.*?\)$", "", p.get("title", "")), "description": p.get("description", "")})
    return out


def wiki_facts(qid: str) -> dict:
    r = requests.get("https://www.wikidata.org/w/api.php", headers=UA, timeout=T, params={
        "action": "wbgetentities", "ids": qid, "props": "claims|labels", "languages": "en", "format": "json"})
    ent = r.json()["entities"][qid]
    c = ent.get("claims", {})

    def vals(p):
        out = []
        for x in c.get(p, []):
            v = ((x.get("mainsnak") or {}).get("datavalue") or {}).get("value")
            out.append(v.get("id") or v.get("amount") if isinstance(v, dict) else v)
        return [v for v in out if v]

    employees = [abs(float(v)) for v in vals("P1128") if str(v).lstrip("+-").replace(".", "", 1).isdigit()]
    ind_ids = vals("P452")[:3]
    industries = []
    if ind_ids:
        r2 = requests.get("https://www.wikidata.org/w/api.php", headers=UA, timeout=T, params={
            "action": "wbgetentities", "ids": "|".join(ind_ids), "props": "labels", "languages": "en", "format": "json"})
        industries = [e.get("labels", {}).get("en", {}).get("value", "") for e in r2.json().get("entities", {}).values()]
    countries = vals("P17")
    return {
        "label": ent.get("labels", {}).get("en", {}).get("value", ""),
        "website": (vals("P856") or [""])[0],
        "employees": int(max(employees)) if employees else None,
        "industry": "; ".join(i for i in industries if i),
        "us": ("Q30" in countries) if countries else None,
    }


def size_from(employees: int | None, open_roles: int | None) -> str:
    if employees:
        return "big" if employees >= 5000 else ("mid" if employees >= 500 else "small")
    if open_roles is not None:  # no headcount on record: how much they're hiring is a fair stand-in
        return "big" if open_roles >= 300 else ("mid" if open_roles >= 40 else "small")
    return "mid"


# ---------------- where are their jobs? ----------------
def board_from_url(url: str) -> dict | None:
    """Recognize a job page link from any of the supported systems."""
    for ats, pat in ATS_PATTERNS:
        m = pat.search(url or "")
        if m:
            token = m.group(1)
            if ats in ("workday", "smartrecruiters"):  # these keep their exact spelling
                return {"ats": ats, "token": token}
            if token.lower() not in ("embed", "jobs", "v1"):
                return {"ats": ats, "token": token.lower()}
    return None


def board_from_website(site: str) -> dict | None:
    """Read the company's homepage and careers page, and find which job system they use."""
    if not site:
        return None
    site = site if site.startswith("http") else "https://" + site
    host = urlparse(site).netloc.replace("www.", "")
    pages = [urljoin(site, "/careers"), site, f"https://careers.{host}", urljoin(site, "/jobs")]
    seen = set()
    for url in pages:
        if url in seen:
            continue
        seen.add(url)
        try:
            r = requests.get(url, headers={**UA, "Accept": "text/html"}, timeout=6, allow_redirects=True)
        except Exception:
            continue
        found = board_from_url(r.url) or board_from_url(r.text)
        if found:
            return found
        # follow one level of "Careers"/"Jobs"/"Open roles" links on the page
        for href in re.findall(r'href="([^"]+)"[^>]*>\s*(?:[^<]{0,40})(?:careers|jobs|open roles|join us|work with us)', r.text, re.I)[:2]:
            nxt = urljoin(r.url, href)
            if nxt in seen:
                continue
            seen.add(nxt)
            try:
                r2 = requests.get(nxt, headers={**UA, "Accept": "text/html"}, timeout=6)
                found = board_from_url(r2.url) or board_from_url(r2.text)
                if found:
                    return found
            except Exception:
                continue
    return None


def _verify(board: dict, name: str) -> int | None:
    try:
        return len(fetch_company({"company": name, "ats": board["ats"], "token": board["token"], "size": "", "industry": ""}))
    except Exception:
        return None


# ---------------- the whole lookup ----------------
def lookup(name: str, chosen_qid: str | None = None) -> dict:
    """status: found | choose | not_us | no_jobs_page | unknown"""
    name = re.sub(r"\s+", " ", name).strip()
    res = {"input": name, "status": "unknown", "company": name.title() if name.islower() else name,
           "ats": "", "token": "", "size": "", "industry": "", "website": "", "open_roles": None, "candidates": []}
    facts = {}
    try:
        cands = wiki_candidates(name)
        if chosen_qid:
            cands = [c for c in cands if c["qid"] == chosen_qid] or [{"qid": chosen_qid, "label": name, "description": ""}]
        if cands:
            best = cands[0]
            sure = chosen_qid or _similar(best["label"], name) >= 0.85 or (
                len(cands) == 1 and _similar(best["label"], name) >= 0.6)
            if not sure:
                res["status"], res["candidates"] = "choose", cands[:4]
                return res
            facts = wiki_facts(best["qid"])
            res["company"] = facts.get("label") or best["label"]
            res["website"] = facts.get("website", "")
            res["industry"] = facts.get("industry") or best.get("description", "")
            if facts.get("us") is False:
                res["status"] = "not_us"
                return res
    except Exception:
        pass  # no internet to Wikipedia: fall back to guessing from the name

    board = find_board(res["company"]) or (find_board(name) if name.lower() != res["company"].lower() else None)
    if not board and res["website"]:
        stem = urlparse(res["website"] if res["website"].startswith("http") else "https://" + res["website"]).netloc
        stem = stem.replace("www.", "").split(".")[0]
        board = find_board(stem) or board_from_website(res["website"])
    if board:
        n = _verify(board, res["company"])
        if n is not None:
            res.update(ats=board["ats"], token=board["token"], open_roles=n, status="found")
    if res["status"] != "found":
        res["status"] = "no_jobs_page"
    res["size"] = size_from(facts.get("employees"), res["open_roles"])
    return res
