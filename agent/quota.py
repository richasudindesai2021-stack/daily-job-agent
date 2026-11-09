"""Free-service usage limits: counts what each person's own keys have used, stops before a limit,
and gives a plain message when something is used up ("for today" or "for this month") and when it resets.

Counts live in the person's own data folder (data/usage.json), never anywhere shared.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from . import store

# Our own safety budgets, kept a little under each service's free plan.
LIMITS = {
    "gemini_search": {"name": "Gemini web search", "day": 20, "month": None,
                      "get": "Google sets this limit and doesn't publish it; it resets daily."},
    "tavily": {"name": "Tavily web search", "day": 30, "month": 950,
               "get": "Free plan: 1,000 searches a month (app.tavily.com)."},
    "jsearch": {"name": "JSearch job listings", "day": 6, "month": 190,
                "get": "Free plan: 200 requests a month (rapidapi.com, JSearch)."},
    "groq": {"name": "Groq backup AI", "day": None, "month": None,
             "get": "Google- and Groq-set daily limits; they reset daily."},
    "gemini": {"name": "Gemini AI", "day": None, "month": None,
               "get": "Your own free Gemini key's daily limit; it resets daily."},
}


def _path():
    return store.DATA / "usage.json"


def _load() -> dict:
    try:
        return json.loads(_path().read_text())
    except Exception:
        return {}


def _save(d: dict):
    store.DATA.mkdir(parents=True, exist_ok=True)
    _path().write_text(json.dumps(d, indent=1))


def _row(d: dict, svc: str) -> dict:
    today, month = date.today().isoformat(), date.today().strftime("%Y-%m")
    r = d.setdefault(svc, {})
    if r.get("day") != today:
        r.update(day=today, day_count=0, out_day=False)
    if r.get("month") != month:
        r.update(month=month, month_count=0, out_month=False)
    return r


def _resets(period: str) -> str:
    t = date.today()
    if period == "day":
        return "tomorrow"
    first = (t.replace(day=1) + timedelta(days=32)).replace(day=1)
    return f"{first:%B} {first.day}"


def _lim(svc: str) -> dict:
    base, _, who = svc.partition(":")
    if base in LIMITS:
        lim = dict(LIMITS[base])
        if who:
            lim["name"] = f"{lim['name']} ({who})"
        return lim
    return {"name": who or svc, "day": None, "month": None, "get": ""}


def check(svc: str, need: int = 1) -> tuple[bool, str]:
    """(allowed, message). Message is empty when allowed."""
    d = _load()
    r = _row(d, svc)
    lim = _lim(svc)
    if r.get("out_month") or (lim["month"] and r["month_count"] + need > lim["month"]):
        return False, f"{lim['name']}: this month's free allowance is used up. It resets on {_resets('month')}."
    if r.get("out_day") or (lim["day"] and r["day_count"] + need > lim["day"]):
        return False, f"{lim['name']}: today's free allowance is used up. It resets {_resets('day')}."
    return True, ""


def record(svc: str, n: int = 1):
    d = _load()
    r = _row(d, svc)
    r["day_count"] += n
    r["month_count"] += n
    _save(d)


def mark_out(svc: str, period: str = "day"):
    """The service itself said 'limit reached' (HTTP 429 and similar)."""
    d = _load()
    r = _row(d, svc)
    r["out_day" if period == "day" else "out_month"] = True
    _save(d)


def status_lines(present: dict) -> list[str]:
    """One line per service the person has a key for, for the settings page."""
    d = _load()
    out = []
    for svc, has in present.items():
        if not has:
            continue
        r = _row(d, svc)
        lim = _lim(svc)
        ok, msg = check(svc)
        parts = []
        if lim["day"]:
            parts.append(f"{r['day_count']} of {lim['day']} today")
        if lim["month"]:
            parts.append(f"{r['month_count']} of {lim['month']} this month")
        out.append(f"{lim['name']}: " + (", ".join(parts) or "ready") + ("" if ok else f" ({msg.split(': ', 1)[1]})"))
    return out
