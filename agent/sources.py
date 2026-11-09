"""Pull job postings from public, free job-board feeds (no API keys needed).

Supported systems:
  greenhouse -> https://boards-api.greenhouse.io/v1/boards/<token>/jobs?content=true
  lever      -> https://api.lever.co/v0/postings/<token>?mode=json
  ashby      -> https://api.ashbyhq.com/posting-api/job-board/<token>?includeCompensation=true
"""
from __future__ import annotations

import html
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests

HEADERS = {"User-Agent": "job-agent/1.0 (personal job search tool)"}
TIMEOUT = 12


def _strip_html(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<(br|/p|/li|/h\d)[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n", text).strip()


def _parse_dt(value) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):  # Lever uses epoch milliseconds
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        v = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(v)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _job(company: dict, **kw) -> dict:
    return {
        "company": company["company"],
        "size": company.get("size", "mid"),
        "industry": company.get("industry", ""),
        "title": kw.get("title", "").strip(),
        "location": kw.get("location", "") or "",
        "remote": kw.get("remote"),
        "url": kw.get("url", ""),
        "posted_at": kw.get("posted_at"),
        "description": kw.get("description", ""),
        "salary_text": kw.get("salary_text", ""),
        "source": company["ats"],
    }


def fetch_greenhouse(company: dict) -> list[dict]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{company['token']}/jobs?content=true"
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        loc = (j.get("location") or {}).get("name", "")
        out.append(_job(
            company,
            title=j.get("title", ""),
            location=loc,
            remote=("remote" in loc.lower()) or None,
            url=j.get("absolute_url", ""),
            # first_published only: updated_at changes on every edit, so an old role can look new
            posted_at=_parse_dt(j.get("first_published")),
            description=_strip_html(j.get("content", "")),
        ))
    return out


def fetch_lever(company: dict) -> list[dict]:
    url = f"https://api.lever.co/v0/postings/{company['token']}?mode=json"
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for j in r.json():
        cats = j.get("categories") or {}
        locs = cats.get("allLocations") or [cats.get("location", "")]
        loc = " / ".join([l for l in locs if l])
        lists = "\n".join(
            f"{l.get('text','')}\n{_strip_html(l.get('content',''))}" for l in (j.get("lists") or [])
        )
        desc = "\n".join([j.get("descriptionPlain", ""), lists, j.get("additionalPlain", "")])
        sal = j.get("salaryRange") or {}
        sal_text = f"${sal.get('min')} - ${sal.get('max')}" if sal.get("min") else ""
        wt = (j.get("workplaceType") or "").lower()
        out.append(_job(
            company,
            title=j.get("text", ""),
            location=loc,
            remote=True if wt == "remote" else (False if wt in ("onsite", "on-site", "hybrid") else None),
            url=j.get("hostedUrl", ""),
            posted_at=_parse_dt(j.get("createdAt")),
            description=desc.strip(),
            salary_text=sal_text,
        ))
    return out


def fetch_ashby(company: dict) -> list[dict]:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{company['token']}?includeCompensation=true"
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location", "")] + [
            (s.get("location") if isinstance(s, dict) else str(s)) for s in (j.get("secondaryLocations") or [])
        ]
        comp = j.get("compensation") or {}
        sal_text = comp.get("compensationTierSummary") or comp.get("scrapeableCompensationSalarySummary") or ""
        wt = (j.get("workplaceType") or "").lower()
        out.append(_job(
            company,
            title=j.get("title", ""),
            location=" / ".join([l for l in locs if l]),
            remote=bool(j.get("isRemote")) if wt == "" else (wt == "remote"),
            url=j.get("jobUrl", "") or j.get("applyUrl", ""),
            posted_at=_parse_dt(j.get("publishedAt")),
            description=j.get("descriptionPlain") or _strip_html(j.get("descriptionHtml", "")),
            salary_text=sal_text,
        ))
    return out


# ---- Workday (used by many large sponsors). token = the careers page URL,
# e.g. https://gilead.wd1.myworkdayjobs.com/gileadcareers
SEARCH_TERMS = ["analyst", "product manager", "data scientist", "consultant", "strategy"]
_WD_MAX_AGE_DAYS = 14


def configure(roles: list[str]):
    """Turn your target roles into a few Workday search terms."""
    terms = []
    for r in roles:
        t = r.lower()
        t = "analyst" if "analyst" in t else ("product manager" if "product manager" in t else t)
        if t not in terms:
            terms.append(t)
    SEARCH_TERMS[:] = terms[:6] or SEARCH_TERMS


def _wd_parts(url: str):
    m = re.match(r"https?://([^/]+\.myworkdayjobs\.com)/(?:[a-z]{2}-[A-Z]{2}/)?([^/?#]+)", url.strip())
    if not m:
        raise ValueError("Workday token must look like https://NAME.wdN.myworkdayjobs.com/SITE")
    host, site = m.group(1), m.group(2)
    return host, host.split(".")[0], site


def _wd_age_days(posted_on: str):
    p = (posted_on or "").lower()
    if "today" in p:
        return 0
    if "yesterday" in p:
        return 1
    m = re.search(r"(\d+)\+?\s*day", p)
    return int(m.group(1)) if m else None


def fetch_workday(company: dict) -> list[dict]:
    from datetime import timedelta

    host, tenant, site = _wd_parts(company["token"])
    api = f"https://{host}/wday/cxs/{tenant}/{site}"
    seen, out = set(), []
    for term in SEARCH_TERMS:
        r = requests.post(f"{api}/jobs", headers={**HEADERS, "Content-Type": "application/json", "Accept": "application/json"},
                          json={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": term}, timeout=TIMEOUT)
        r.raise_for_status()
        for p in r.json().get("jobPostings", []):
            path = p.get("externalPath", "")
            age = _wd_age_days(p.get("postedOn", ""))
            if not path or path in seen or age is None or age > _WD_MAX_AGE_DAYS:
                continue
            seen.add(path)
            desc, loc = "", p.get("locationsText", "")
            try:  # full description, needed for the sponsorship check
                d = requests.get(f"{api}{path}", headers={**HEADERS, "Accept": "application/json"}, timeout=TIMEOUT).json()
                info = d.get("jobPostingInfo", {})
                desc = _strip_html(info.get("jobDescription", ""))
                loc = info.get("location", loc) or loc
                extra = [l.get("descriptor", "") for l in (info.get("additionalLocations") or []) if isinstance(l, dict)]
                loc = " / ".join([loc] + [x for x in extra if x])
            except Exception:
                pass
            out.append(_job(
                company,
                title=p.get("title", ""),
                location=loc,
                remote=("remote" in loc.lower()) or None,
                url=f"https://{host}/{site}{path}",
                posted_at=datetime.now(timezone.utc) - timedelta(days=age),
                description=desc,
            ))
    return out


def fetch_smartrecruiters(company: dict) -> list[dict]:
    """SmartRecruiters public postings API (U.S. roles only). token = the company identifier, e.g. BoschGroup."""
    out, offset = [], 0
    while offset < 1000:
        r = requests.get(f"https://api.smartrecruiters.com/v1/companies/{company['token']}/postings",
                         params={"limit": 100, "offset": offset, "country": "us"}, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        for j in data.get("content", []):
            loc = j.get("location") or {}
            out.append(_job(
                company,
                title=j.get("name", ""),
                location=loc.get("fullLocation") or ", ".join(x for x in (loc.get("city"), loc.get("region")) if x),
                remote=bool(loc.get("remote")),
                url=f"https://jobs.smartrecruiters.com/{company['token']}/{j.get('id', '')}",
                posted_at=_parse_dt(j.get("releasedDate")),
                description=" ".join(x for x in ((j.get("function") or {}).get("label", ""),
                                                 (j.get("experienceLevel") or {}).get("label", "")) if x),
            ))
        offset += 100
        if offset >= int(data.get("totalFound") or 0):
            break
    return out


def fetch_workable(company: dict) -> list[dict]:
    """Workable public job widget. token = the account name in apply.workable.com/NAME."""
    r = requests.get(f"https://apply.workable.com/api/v1/widget/accounts/{company['token']}",
                     params={"details": "true"}, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        locs = j.get("locations") or [{"city": j.get("city"), "region": j.get("state"), "countryCode": j.get("country")}]
        loc_txt = " / ".join(", ".join(x for x in (l.get("city"), l.get("region"), l.get("countryCode") or l.get("country")) if x)
                             for l in locs)
        out.append(_job(
            company,
            title=j.get("title", ""),
            location=loc_txt,
            remote=bool(j.get("telecommuting")),
            url=j.get("url") or j.get("shortlink", ""),
            posted_at=_parse_dt(j.get("published_on") or j.get("created_at")),
            description=_strip_html(j.get("description", "")),
        ))
    return out


FETCHERS = {"greenhouse": fetch_greenhouse, "lever": fetch_lever, "ashby": fetch_ashby, "workday": fetch_workday,
            "smartrecruiters": fetch_smartrecruiters, "workable": fetch_workable}


def fetch_company(company: dict) -> list[dict]:
    fn = FETCHERS.get(str(company.get("ats", "")).lower().strip())
    if not fn:
        raise ValueError(f"Unknown job board system: {company.get('ats')}")
    return fn(company)


def fetch_all(companies: list[dict], workers: int = 64, progress=None) -> tuple[list[dict], dict]:
    """Fetch every company in parallel. Returns (jobs, errors_by_company)."""
    jobs, errors = [], {}
    companies = [c for c in companies if str(c.get("ats", "")).strip() and str(c.get("token", "")).strip()]
    if not companies:
        return jobs, errors
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch_company, c): c for c in companies}
        for i, f in enumerate(as_completed(futs), 1):
            c = futs[f]
            try:
                jobs.extend(f.result())
            except Exception as e:  # dead token, network error, etc.
                errors[c["company"]] = str(e)[:150]
            if progress:
                progress(i / len(futs), f"Checked {i:,}/{len(futs):,} companies")
    if errors:  # second try for the ones that timed out or hiccuped, so no company is skipped for a blip
        retry = [c for c in companies if c["company"] in errors]
        with ThreadPoolExecutor(max_workers=min(workers, 32)) as ex:
            futs = {ex.submit(fetch_company, c): c for c in retry}
            for f in as_completed(futs):
                c = futs[f]
                try:
                    jobs.extend(f.result())
                    errors.pop(c["company"], None)
                except Exception as e:
                    errors[c["company"]] = str(e)[:150]
        if progress:
            progress(1.0, f"Checked {len(companies):,}/{len(companies):,} companies "
                          f"({len(retry) - len(errors)} retried successfully)")
    return jobs, errors
