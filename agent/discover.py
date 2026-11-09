"""Find which public job board (Greenhouse, Lever, Ashby) a company uses, from its name alone.
Runs from your laptop. Results are cached, so you can stop and resume at any time."""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from pathlib import Path

import requests

import threading
import time

from requests.adapters import HTTPAdapter

HEADERS = {"User-Agent": "job-agent/1.0 (personal job search tool)"}
TIMEOUT = 6
_local = threading.local()


# A few requests at a time per job site (they push back when hit by dozens at once), many sites in parallel.
_HOST_LIMIT = {"boards-api.greenhouse.io": 16, "api.lever.co": 6, "api.ashbyhq.com": 6, "apply.workable.com": 4,
               "api.smartrecruiters.com": 4}
_host_sem: dict = {}
_host_pause: dict = {}  # site -> time until which we wait after it said "slow down"
_sem_lock = threading.Lock()
STATS = {"slow_down": 0, "errors": 0}


def _sem(host: str):
    with _sem_lock:
        if host not in _host_sem:
            _host_sem[host] = threading.Semaphore(_HOST_LIMIT.get(host, 8))
        return _host_sem[host]


def _get(url: str):
    """One open connection per thread and site; a polite number of requests per site; backs off when told to."""
    from urllib.parse import urlparse

    host = urlparse(url).netloc
    s = getattr(_local, "s", None)
    if s is None:
        s = _local.s = requests.Session()
        s.headers.update(HEADERS)
        s.mount("https://", HTTPAdapter(pool_connections=8, pool_maxsize=8, max_retries=0))
    for attempt in range(4):
        wait = _host_pause.get(host, 0) - time.time()
        if wait > 0:
            time.sleep(min(wait, 10))
        with _sem(host):
            try:
                r = s.get(url, timeout=TIMEOUT)
            except Exception:
                STATS["errors"] += 1
                if attempt == 3:
                    raise
                continue
        if r.status_code == 429:  # this site asked everyone to slow down: pause just this site briefly
            STATS["slow_down"] += 1
            _host_pause[host] = time.time() + 3 * (attempt + 1)
            continue
        if r.status_code in (500, 502, 503, 504):
            time.sleep(1)
            continue
        return r
    raise RuntimeError(f"no answer from {host}")  # don't record as "no job page"; checked again later
GENERIC = {"the", "group", "holdings", "technologies", "technology", "labs", "health", "services", "systems",
           "solutions", "global", "international", "america", "usa", "us", "company", "partners", "inc", "llc"}


def slug_candidates(name: str) -> list[str]:
    n = name.lower().replace("&", " and ")
    n = re.sub(r"\(.*?\)", " ", n)
    n = re.sub(r"[,.]\s*(inc|llc|corp|corporation|co|ltd|lp|plc)\b\.?", " ", n)
    words = [w for w in re.findall(r"[a-z0-9]+", n) if w not in ("inc", "llc", "corp", "corporation", "co", "ltd", "lp", "plc", "the")]
    if not words:
        return []
    core = [w for w in words if w not in GENERIC] or words
    cands = ["".join(words), "".join(core), "-".join(core), "".join(w for w in words if w != "and")]
    if len(core) > 1:
        cands.append(core[0] + core[1])
    if len(core[0]) >= 5:
        cands.append(core[0])  # single-word guesses are verified by name below
    out = []
    for c in cands:
        if len(c) >= 3 and c not in out:
            out.append(c)
    return out[:5]


def _similar(a: str, b: str) -> float:
    norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
    return SequenceMatcher(None, norm(a), norm(b)).ratio()


def _try_greenhouse(slug: str, name: str):
    r = _get(f"https://boards-api.greenhouse.io/v1/boards/{slug}")
    if r.status_code == 200:
        board_name = r.json().get("name", "")
        if _similar(board_name, name) >= 0.6 or board_name.lower() in name.lower() or name.lower() in board_name.lower():
            return {"ats": "greenhouse", "token": slug, "board_name": board_name}
    return None


def _try_lever(slug: str, name: str):
    r = _get(f"https://api.lever.co/v0/postings/{slug}?mode=json&limit=1")
    if r.status_code == 200 and isinstance(r.json(), list):
        return {"ats": "lever", "token": slug, "board_name": ""}
    return None


def _try_ashby(slug: str, name: str):
    r = _get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    if r.status_code == 200 and "jobs" in r.json():
        return {"ats": "ashby", "token": slug, "board_name": ""}
    return None


def _try_workable(slug: str, name: str):
    r = _get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}")
    if r.status_code == 200:
        board_name = (r.json() or {}).get("name", "")
        if _similar(board_name, name) >= 0.6 or board_name.lower() in name.lower() or name.lower() in board_name.lower():
            return {"ats": "workable", "token": slug, "board_name": board_name}
    return None


def _try_smartrecruiters(slug: str, name: str):
    r = _get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=1")
    if r.status_code == 200:
        d = r.json() or {}
        if int(d.get("totalFound") or 0) > 0:
            board_name = ((d.get("content") or [{}])[0].get("company") or {}).get("name", "")
            if _similar(board_name, name) >= 0.6 or board_name.lower() in name.lower() or name.lower() in board_name.lower():
                return {"ats": "smartrecruiters", "token": ((d["content"][0].get("company") or {}).get("identifier") or slug),
                        "board_name": board_name}
    return None


def from_website(name: str):
    """Find the company's real website (Wikipedia/Wikidata), open its careers page, see which job system it links to.
    Catches Workday and every other system, and odd company names the address guessing misses."""
    from .company_lookup import _similar as sim, board_from_website, wiki_candidates, wiki_facts

    cands = wiki_candidates(name, limit=3)
    best = max(cands, key=lambda c: sim(c["label"], name), default=None)
    if not best or sim(best["label"], name) < 0.6:
        return None
    facts = wiki_facts(best["qid"])
    if facts.get("us") is False or not facts.get("website"):
        return None
    hit = board_from_website(facts["website"])
    if hit:
        hit["board_name"] = best["label"]
    return hit


class Unsure(Exception):
    """A check failed (timeout, slow-down, network); try this company again next time."""


def find_board(name: str, deep: bool = False) -> dict | None:
    cands = slug_candidates(name)
    if not deep:  # quick pass: the most likely addresses only (keeps job sites from throttling us)
        cands = cands[:3]
    full = cands[:4]
    failed = False
    for slug in cands:
        for fn in (_try_greenhouse, _try_lever, _try_ashby, _try_workable, _try_smartrecruiters):
            # single-word guesses only trusted on Greenhouse, where the board name can be checked
            if slug not in full and fn is not _try_greenhouse:
                continue
            if fn in (_try_workable, _try_smartrecruiters) and (slug != cands[0] or not deep):
                continue
            if fn in (_try_lever, _try_ashby) and slug not in cands[:(2 if deep else 1)]:
                continue
            try:
                hit = fn(slug, name)
                if hit:
                    return hit
            except Exception:
                failed = True
    if deep:
        try:
            hit = from_website(name)
            if hit:
                return hit
        except Exception:
            failed = True
    if failed:
        raise Unsure(name)
    return None


def discover_all(names: list[str], cache_path: Path, workers: int = 48, progress=None, stop_flag=None,
                 deep_names: set | None = None, record_misses: bool = True) -> dict:
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    todo = [n for n in names if n not in cache]
    done = len(names) - len(todo)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(find_board, n, bool(deep_names and n in deep_names)): n for n in todo}
        for i, f in enumerate(as_completed(futs), 1):
            n = futs[f]
            try:
                res = f.result()
                if res or record_misses:  # quick pass: keep hits only, so the deeper pass still checks the rest
                    cache[n] = res
            except Exception:
                pass  # not saved, so it's checked again next time
            if i % 25 == 0:
                cache_path.write_text(json.dumps(cache))
            if progress:
                found = sum(1 for v in cache.values() if v)
                progress((done + i) / max(len(names), 1), f"Checked {done + i:,}/{len(names):,} companies · {found:,} job pages found"
                         )
            if stop_flag and stop_flag():
                break
    cache_path.write_text(json.dumps(cache))
    return cache
