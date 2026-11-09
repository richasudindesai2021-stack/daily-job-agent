"""Fill short size groups with Gemini + Google Search, then check every link ourselves.

The regular search reads company job boards directly. When a size group (big / mid / small) comes up short,
this asks Gemini to search the web for real postings, and keeps only links that:
  * open on the company's own site or its hiring system (never LinkedIn, Indeed or other job boards),
  * still load, and
  * have a posting date within the 2-week limit (read from the page itself when the page states it).
Uses the person's own Gemini key. Google gives a limited number of free searches a day; if they run out, the
rest of the run continues without this step.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/130.0 Safari/537.36"}
SIZE_WORDS = {"big": "large companies (5,000+ employees)", "mid": "mid-size companies (500 to 5,000 employees)",
              "small": "startups and small companies (under 500 employees)"}


def _gemini_search(api_key: str, model: str, prompt: str) -> tuple[str, list[str]]:
    from . import quota

    svc = f"gemini_search:Gemini key …{api_key[-4:]}"
    ok, msg = quota.check(svc)
    if not ok:
        raise RuntimeError(msg)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    r = requests.post(url, params={"key": api_key}, timeout=120, json={
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 0.2}})
    if r.status_code == 429:
        quota.mark_out(svc, "day")
        raise RuntimeError(quota.check(svc)[1])
    quota.record(svc)
    r.raise_for_status()
    cand = (r.json().get("candidates") or [{}])[0]
    text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []))
    sources = [c.get("web", {}).get("uri", "") for c in (cand.get("groundingMetadata") or {}).get("groundingChunks", [])]
    return text, [s for s in sources if s]


def _json_list(text: str) -> list[dict]:
    m = re.search(r"\[.*\]", text or "", re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
        return [d for d in data if isinstance(d, dict)]
    except Exception:
        return []


def _job_ld(html: str) -> dict:
    """The schema.org JobPosting most company job pages include for Google."""
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html or "", re.S | re.I):
        try:
            data = json.loads(block.strip())
        except Exception:
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for it in items:
            if isinstance(it, dict) and "JobPosting" in str(it.get("@type", "")):
                return it
    return {}


def _parse_date(s) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _company_from_url(url: str) -> str:
    m = re.search(r"(?:greenhouse\.io|lever\.co|ashbyhq\.com|smartrecruiters\.com|workable\.com)/([A-Za-z0-9_-]+)", url)
    if m:
        return m.group(1).replace("-", " ").title()
    m = re.match(r"https?://([a-z0-9-]+)\.wd\d+\.myworkdayjobs\.com", url)
    return m.group(1).title() if m else ""


def _verify(item: dict, aggregators: tuple, max_age_days: int) -> dict | None:
    url = str(item.get("url", "")).strip()
    if not url.startswith("http") or any(a in url.lower() for a in aggregators):
        return None
    try:
        r = requests.get(url, headers=UA, timeout=15, allow_redirects=True)
    except Exception:
        return None
    final = r.url
    if r.status_code != 200 or any(a in final.lower() for a in aggregators):
        return None
    ld = _job_ld(r.text)
    title = (ld.get("title") or item.get("title") or "").strip()
    page = r.text.lower()
    words = [w for w in re.findall(r"[a-z]{4,}", title.lower())][:4]
    if not ld and (not words or sum(w in page for w in words) < max(1, len(words) - 1)):
        return None  # the page doesn't look like this job: probably an invented or expired link
    posted = _parse_date(ld.get("datePosted")) or _parse_date(item.get("posted_date"))
    if not posted or datetime.now(timezone.utc) - posted > timedelta(days=max_age_days):
        return None
    if ld.get("validThrough"):
        end = _parse_date(ld["validThrough"])
        if end and end < datetime.now(timezone.utc):
            return None
    org = ld.get("hiringOrganization") or {}
    loc = ld.get("jobLocation") or {}
    loc = loc[0] if isinstance(loc, list) and loc else loc
    addr = (loc.get("address") or {}) if isinstance(loc, dict) else {}
    loc_txt = ", ".join(x for x in (addr.get("addressLocality"), addr.get("addressRegion")) if isinstance(x, str) and x)
    desc = re.sub(r"<[^>]+>", " ", str(ld.get("description") or ""))
    company = (org.get("name") if isinstance(org, dict) else "") or item.get("company", "") or _company_from_url(final)
    return {
        "company": company,
        "title": title,
        "location": loc_txt or item.get("location", ""),
        "remote": "TELECOMMUTE" in str(ld.get("jobLocationType", "")).upper() or "remote" in str(item.get("location", "")).lower(),
        "url": final,
        "posted_at": posted,
        "description": re.sub(r"\s+", " ", desc)[:6000],
        "salary_text": "",
        "source": "web search",
    }


ATS_SITES = ("boards.greenhouse.io", "job-boards.greenhouse.io", "jobs.lever.co", "jobs.ashbyhq.com",
             "myworkdayjobs.com", "jobs.smartrecruiters.com", "apply.workable.com", "icims.com")


def _tavily(key: str, query: str) -> list[dict]:
    from . import quota

    ok, msg = quota.check(f"tavily:Tavily key …{key[-4:]}")
    if not ok:
        raise RuntimeError(msg)
    r = requests.post("https://api.tavily.com/search", timeout=60, headers={"Authorization": f"Bearer {key}"},
                      json={"query": query, "search_depth": "basic", "max_results": 20, "time_range": "month",
                            "include_domains": list(ATS_SITES)})
    if r.status_code in (429, 432, 433):
        quota.mark_out(f"tavily:Tavily key …{key[-4:]}", "month" if r.status_code == 432 else "day")
        raise RuntimeError(quota.check(f"tavily:Tavily key …{key[-4:]}")[1])
    if r.status_code == 401:
        raise RuntimeError("Tavily didn't accept your key. Check it in Setup → 1.")
    r.raise_for_status()
    quota.record(f"tavily:Tavily key …{key[-4:]}")
    return [{"company": "", "title": x.get("title", ""), "url": x.get("url", "")} for x in r.json().get("results", [])]


def _jsearch(key: str, query: str) -> list[dict]:
    from . import quota

    ok, msg = quota.check(f"jsearch:JSearch key …{key[-4:]}")
    if not ok:
        raise RuntimeError(msg)
    r = requests.get("https://jsearch.p.rapidapi.com/search", timeout=60,
                     headers={"X-RapidAPI-Key": key, "X-RapidAPI-Host": "jsearch.p.rapidapi.com"},
                     params={"query": query, "page": 1, "num_pages": 1, "country": "us", "date_posted": "week"})
    if r.status_code == 429:
        quota.mark_out(f"jsearch:JSearch key …{key[-4:]}", "month")
        raise RuntimeError(quota.check(f"jsearch:JSearch key …{key[-4:]}")[1])
    if r.status_code in (401, 403):
        raise RuntimeError("JSearch didn't accept your key. Check it in Setup → 1.")
    r.raise_for_status()
    quota.record(f"jsearch:JSearch key …{key[-4:]}")
    out = []
    for j in r.json().get("data", []):
        links = [o.get("apply_link", "") for o in (j.get("apply_options") or []) if o.get("is_direct")]
        links += [j.get("job_apply_link", "")] if j.get("job_apply_is_direct") else []
        if not links:
            continue
        out.append({"company": j.get("employer_name", ""), "title": j.get("job_title", ""), "url": links[0],
                    "location": ", ".join(x for x in (j.get("job_city"), j.get("job_state")) if x),
                    "posted_date": (j.get("job_posted_at_datetime_utc") or "")[:10]})
    return out


def find_jobs(api_key: str, model: str, settings: dict, size: str, need: int, aggregators: tuple,
              exclude_urls: set, progress=None, engine: str = "gemini", size_of=None) -> list[dict]:
    """Up to `need` verified postings at companies of this size.
    engine: gemini (Gemini + Google Search), tavily or jsearch; api_key is that service's key."""
    roles = ", ".join(settings.get("roles", [])[:6]) or "analyst"
    places = ", ".join((settings.get("list_locations") or settings.get("locations") or [])[:6]) or "the United States"
    max_days = max(1, int(settings.get("max_age_hours", 336)) // 24)
    sponsor = (" Prefer companies known to sponsor work visas (H-1B / OPT)."
               if settings.get("needs_sponsorship", True) else "")
    prompt = (
        f"Use Google Search to find {need * 2} real, currently open job postings for entry-level or early-career "
        f"roles like: {roles}. Location: {places} (or remote in the U.S.). Companies: {SIZE_WORDS[size]} in the U.S."
        f"{sponsor} Only postings published in the last {max_days} days. Only give links on the company's own careers "
        "site or its hiring system (Greenhouse, Lever, Ashby, Workday, SmartRecruiters, iCIMS and similar); never "
        "LinkedIn, Indeed, Glassdoor, ZipRecruiter or any other job board. One posting per company. Reply with only a "
        'JSON array: [{"company": "", "title": "", "location": "", "url": "", "posted_date": "YYYY-MM-DD"}]')
    if engine == "gemini":
        text, sources = _gemini_search(api_key, model, prompt)
        items = _json_list(text)
    else:
        q = f"{roles.split(',')[0]} entry level {places.split(',')[0]}"
        items = _tavily(api_key, q) if engine == "tavily" else _jsearch(api_key, q)
    with ThreadPoolExecutor(max_workers=8) as ex:
        checked = list(ex.map(lambda it: _verify(it, aggregators, max_days), items))
    out, seen_co = [], set()
    for j in checked:
        if j and j["url"] not in exclude_urls and j["company"] and j["company"].lower() not in seen_co:
            known = size_of(j["company"]) if size_of else None
            if known and known != size:  # we know this company's size, and it's not the group being filled
                continue
            j["size"] = size
            j["industry"] = ""
            out.append(j)
            seen_co.add(j["company"].lower())
        if len(out) >= need:
            break
    if progress:
        progress(f"{engine.title()} search: {len(items)} {size} postings suggested, {len(out)} checked and kept")
    return out


# ---------------------------------------------------------------------------------------------
# Search the web for YOUR kind of role, then keep only postings from companies on YOUR list.
# This is how the whole 3,000-company list gets used, including companies whose job page the
# agent couldn't find. Uses JSearch (Google for Jobs listings) and Gemini web search.
# ---------------------------------------------------------------------------------------------
def _norm_co(name: str) -> str:
    n = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower())
    n = re.sub(r"\b(inc|llc|l l c|corp|corporation|co|company|ltd|limited|lp|llp|plc|na|n a|the|technologies|technology|"
               r"labs|holdings|usa|us|group|services|solutions)\b", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def list_matcher(companies) -> callable:
    """Returns match(employer_name) -> (company name on the list, size) or None."""
    index = {}
    for c, sz in companies:
        k = _norm_co(c)
        if k and k not in index:
            index[k] = (c, sz)

    def match(name: str):
        k = _norm_co(name)
        if not k:
            return None
        if k in index:
            return index[k]
        first = k.split()[0]  # "Goldman Sachs & Co" vs "Goldman Sachs": same first words
        for key, val in index.items():
            if key.startswith(k) or k.startswith(key):
                if len(min(key, k, key=len)) >= 5 and key.split()[0] == first:
                    return val
        return None
    return match


def _jsearch_pages(key: str, query: str, pages: int) -> list[dict]:
    from . import quota

    ok, msg = quota.check(f"jsearch:JSearch key …{key[-4:]}", pages)
    if not ok:
        raise RuntimeError(msg)
    r = requests.get("https://jsearch.p.rapidapi.com/search", timeout=90,
                     headers={"X-RapidAPI-Key": key, "X-RapidAPI-Host": "jsearch.p.rapidapi.com"},
                     params={"query": query, "page": 1, "num_pages": pages, "country": "us", "date_posted": "week"})
    if r.status_code == 429:
        quota.mark_out(f"jsearch:JSearch key …{key[-4:]}", "month")
        raise RuntimeError(quota.check(f"jsearch:JSearch key …{key[-4:]}")[1])
    if r.status_code in (401, 403):
        raise RuntimeError("JSearch didn't accept your key. Check it in Setup → 1.")
    r.raise_for_status()
    quota.record(f"jsearch:JSearch key …{key[-4:]}", pages)
    return r.json().get("data", [])


def search_list_companies(settings: dict, companies, aggregators: tuple, exclude_urls: set,
                          jsearch_key: str = "", gemini_key: str = "", gemini_model: str = "", progress=None):
    """Find roles that suit the person at ANY company on their list. Returns (jobs, notes)."""
    match = list_matcher(companies)
    roles = (settings.get("roles") or ["Data Analyst"])[:3]
    places = (settings.get("list_locations") or settings.get("locations") or ["United States"])[:2]
    say = progress or (lambda m: None)
    jobs, notes, seen = [], [], set(exclude_urls)

    def keep(company, size, title, location, url, posted, desc="", remote=False):
        if not url or url in seen or any(a in url.lower() for a in aggregators):
            return
        seen.add(url)
        jobs.append({"company": company, "size": size or "mid", "industry": "", "title": title, "location": location,
                     "remote": remote, "url": url, "posted_at": posted, "description": desc[:6000],
                     "salary_text": "", "source": "web search"})

    # 1) JSearch: Google for Jobs listings, filtered to your list
    if jsearch_key:
        for role in roles:
            for place in places:
                try:
                    data = _jsearch_pages(jsearch_key, f"{role} in {place}", 1)
                except Exception as e:
                    if str(e) not in notes:
                        notes.append(str(e))
                    break
                hits = 0
                for j in data:
                    m = match(j.get("employer_name", ""))
                    if not m:
                        continue
                    links = [o.get("apply_link", "") for o in (j.get("apply_options") or []) if o.get("is_direct")]
                    if j.get("job_apply_is_direct"):
                        links.append(j.get("job_apply_link", ""))
                    links = [u for u in links if u and not any(a in u.lower() for a in aggregators)]
                    if not links:
                        continue
                    keep(m[0], m[1], j.get("job_title", ""),
                         ", ".join(x for x in (j.get("job_city"), j.get("job_state")) if x) or j.get("job_location", ""),
                         links[0], _parse_date(j.get("job_posted_at_datetime_utc")), j.get("job_description", ""),
                         bool(j.get("job_is_remote")))
                    hits += 1
                say(f"Web listings for '{role}' in {place}: {len(data)} found, {hits} at companies on your list")
            else:
                continue
            break

    # 2) Gemini web search, asked to stay within the list's kind of companies, then filtered to your list
    if gemini_key:
        try:
            prompt = (f"Use Google Search to find 25 real, currently open entry-level or early-career job postings for: "
                      f"{', '.join(roles)} in {', '.join(places)} (or U.S. remote), at U.S. companies that sponsor "
                      "work visas (H-1B). Posted in the last 14 days. Only links on the company's own careers site or "
                      "its hiring system, never LinkedIn, Indeed, Glassdoor or other job boards. Reply with only a JSON "
                      'array: [{"company": "", "title": "", "location": "", "url": "", "posted_date": "YYYY-MM-DD"}]')
            text, _ = _gemini_search(gemini_key, gemini_model or "gemini-flash-latest", prompt)
            items = [it for it in _json_list(text) if match(it.get("company", ""))]
            with ThreadPoolExecutor(max_workers=8) as ex:
                checked = list(ex.map(lambda it: _verify(it, aggregators, 14), items))
            for it, v in zip(items, checked):
                if v:
                    m = match(it.get("company", "")) or match(v["company"])
                    keep(m[0], m[1], v["title"], v["location"], v["url"], v["posted_at"], v["description"], v["remote"])
            say(f"Gemini web search: {len(items)} postings at companies on your list, {sum(1 for c in checked if c)} checked and kept")
        except Exception as e:
            if str(e) not in notes:
                notes.append(str(e))
    return jobs, notes

