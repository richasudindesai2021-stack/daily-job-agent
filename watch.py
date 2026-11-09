"""Job watcher: checks your companies every few minutes and pings your phone the moment a matching role appears.

    python watch.py              # keep running, check every 10 minutes
    python watch.py --every 5    # check every 5 minutes
    python watch.py --once       # one check (used by the GitHub Actions schedule)

Alerts go to the free ntfy app (set "ntfy_topic" in the app's My targets tab or the NTFY_TOPIC env var),
and optionally to email (through the app's email sender).
The first check only records what's already posted, so you won't get flooded with old jobs.
"""
from __future__ import annotations

import argparse
import json
import os
import smtplib
import time
from datetime import datetime
from email.message import EmailMessage

import requests

from agent import store
from agent.filters import apply_filters, load_h1b_names
from agent.sources import fetch_all

SEEN_FILE = "seen.json"


def _seen_path():
    return store.DATA / SEEN_FILE


def load_seen() -> set[str] | None:
    p = _seen_path()
    return set(json.loads(p.read_text())) if p.exists() else None


def save_seen(urls: set[str]):
    _seen_path().write_text(json.dumps(sorted(urls)[-50000:]))


def notify_phone(topic: str, jobs: list[dict]):
    if not topic:
        return
    batch = jobs if len(jobs) <= 5 else []
    for j in batch:  # one tap-to-open notification per job
        requests.post(f"https://ntfy.sh/{topic}", timeout=15,
                      data=f"{j['title']}\n{j['location']}".encode(),
                      headers={"Title": f"New: {j['company']} ({j.get('size','')})", "Click": j["url"],
                               "Tags": "briefcase", "Priority": "high"})
    if not batch:  # many at once: one summary
        lines = "\n".join(f"• {j['company']}: {j['title']}" for j in jobs[:15])
        requests.post(f"https://ntfy.sh/{topic}", timeout=15, data=lines.encode(),
                      headers={"Title": f"{len(jobs)} new matching roles", "Tags": "briefcase", "Priority": "high"})


def notify_email(jobs: list[dict]):
    from agent.mailer import email_ready, send

    to = store.load_settings().get("daily_email_to") or ""
    if not (email_ready() and to):
        return
    send(f"{len(jobs)} new role(s): " + ", ".join(sorted({j['company'] for j in jobs}))[:120],
         "\n\n".join(f"{j['company']} ({j.get('size','')}) · {j['title']}\n{j['location']}\n{j['url']}" for j in jobs), to=to)


def _llm_from_settings(settings: dict):
    from agent.llm import LLM, key_name_for, local_secret

    key = local_secret(key_name_for(settings.get("provider", "Google Gemini (free)")))
    if not key:
        return None
    try:
        llm = LLM(settings["provider"], key, settings.get("model") or None, base_url=settings.get("base_url") or None)
        llm.fast_model = settings.get("fast_model") or ("gemini-flash-lite-latest" if "Gemini" in settings["provider"] else None)
        from agent.llm import with_backup
        return with_backup(llm, settings["provider"])
    except Exception:
        return None


def _local_now(settings: dict) -> datetime:
    tz = settings.get("daily_email_tz") or ""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(tz)).replace(tzinfo=None) if tz else datetime.now()
    except Exception:
        return datetime.now()


def daily_due(settings: dict, now: datetime | None = None) -> bool:
    if not settings.get("daily_email") or not settings.get("daily_email_time"):
        return False  # each person picks their own time; nothing is sent until they do
    if now is None:
        now = _local_now(settings)
    try:
        hh, mm = [int(x) for x in str(settings["daily_email_time"]).split(":")[:2]]
    except ValueError:
        return False
    last = store.DATA / "last_daily_email.txt"
    done_today = last.exists() and last.read_text().strip() == now.date().isoformat()
    return (now.hour, now.minute) >= (hh, mm) and not done_today


def run_daily_email(verbose=True) -> bool:
    """Build today's list (with tailored resumes) and email it. Runs once per day."""
    from agent.mailer import send_daily_list
    from agent.pipeline import run_today

    settings = store.load_settings()
    resume = store.load_resume()
    if not resume:
        print("Daily email skipped: upload your resume in the app first.")
        return False
    llm = _llm_from_settings(settings)
    if verbose:
        print(f"{datetime.now():%H:%M} building today's {settings.get('daily_count', 30)} roles"
              + ("" if llm else " (no saved AI key, so no scoring or tailoring)") + "...")
    result = run_today(settings, resume, llm, progress=lambda f, m: None)
    if len(result["picks"]) == 0:
        print("No matching roles today; no email sent.")
    else:
        send_daily_list(result["picks"], result["folder"], bool(settings.get("daily_email_attach_pdfs", True)), result,
                        to=settings.get("daily_email_to") or None)
        if verbose:
            print(f"   📧 emailed {len(result['picks'])} roles")
    (store.DATA / "last_daily_email.txt").write_text(_local_now(settings).date().isoformat())
    return True


def check_once(verbose=True) -> int:
    settings = store.load_settings()
    from agent.company_lists import enrich
    companies = enrich(store.load_companies()).to_dict("records")
    from agent.sources import configure
    configure(settings["roles"])
    jobs, errors = fetch_all(companies, workers=64)
    current = {j["url"] for j in jobs}
    seen = load_seen()
    if seen is None:  # first run: baseline, no alerts
        save_seen(current)
        if verbose:
            print(f"{datetime.now():%H:%M} baseline saved ({len(current)} postings). Alerts start from the next check.")
        return 0
    fresh = [j for j in jobs if j["url"] not in seen]
    tracker = store.load_tracker()
    h1b = load_h1b_names(str(store.H1B)) if store.H1B.exists() else None
    watch_settings = dict(settings, max_age_hours=72)  # alerts: anything new to us, first posted within 3 days
    skip_cos = {r["company"].lower() for _, r in tracker.iterrows() if r.get("status") == "not interested in company"}
    matches, _ = apply_filters(fresh, watch_settings, set(tracker["url"]) if len(tracker) else set(), skip_cos, h1b)
    if matches:
        topic = os.environ.get("NTFY_TOPIC") or settings.get("ntfy_topic", "")
        notify_phone(topic, matches)
        if not os.environ.get("GITHUB_ACTIONS"):
            notify_email(matches)
        log = store.DATA / "alerts.csv"
        new_log = not log.exists()
        with open(log, "a") as f:
            if new_log:
                f.write("alerted_at,company,size,title,location,url\n")
            for j in matches:
                f.write(f"{datetime.now():%Y-%m-%d %H:%M},\"{j['company']}\",{j.get('size','')},\"{j['title']}\",\"{j['location']}\",{j['url']}\n")
    save_seen(seen | current)
    if verbose:
        print(f"{datetime.now():%H:%M} checked {len(jobs)} postings, {len(fresh)} new, {len(matches)} match"
              + (f" ({len(errors)} companies unreachable)" if errors else ""))
        for j in matches:
            print(f"   🔔 {j['company']}: {j['title']} · {j['location']}\n      {j['url']}")
    return len(matches)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--every", type=float, default=10, help="minutes between checks")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--daily-now", action="store_true", help="build and email today's list right now")
    ap.add_argument("--cloud", nargs="?", const="auto", help="GitHub run: 'daily' = email now, 'auto' = whatever is due")
    a = ap.parse_args()
    if a.cloud:
        s = store.load_settings()
        if a.cloud == "daily" or daily_due(s):
            run_daily_email()
        elif s.get("ntfy_topic"):
            check_once()
        else:
            print("Nothing due right now.")
        raise SystemExit(0)
    if store.load_settings().get("cloud_enabled") and not a.daily_now:
        print("Your agent now runs on GitHub (laptop can stay off), so this laptop watcher won't send anything.\n"
              "To run it here again, turn off 'Runs on GitHub' in the app's last setup step.")
        raise SystemExit(0)
    if a.daily_now:
        run_daily_email()
    elif a.once:
        check_once()
        if daily_due(store.load_settings()):
            run_daily_email()
    else:
        print(f"Watching every {a.every:g} minutes. Leave this window open (Ctrl+C to stop).")
        while True:
            try:
                check_once()
            except Exception as e:
                print(f"{datetime.now():%H:%M} check failed: {e}")
            try:
                if daily_due(store.load_settings()):
                    run_daily_email()
            except Exception as e:
                print(f"{datetime.now():%H:%M} daily email failed: {e}")
            time.sleep(a.every * 60)
