"""The daily run: fetch -> filter -> score -> pick 20 -> tailor resume -> write PDFs + tracker."""
from __future__ import annotations

import json
from pathlib import Path
import re
from datetime import date, datetime, timedelta

import pandas as pd

from . import store
from .filters import apply_filters, load_h1b_names
from .pdf import build_pdf
from .filters import age_tier
from .ranker import _tokens, pick_balanced, quick_score, score_with_ai
from .resume import resume_as_text, tailor_resume
from .sources import fetch_all


def _ago(ts) -> str:
    if not ts:
        return ""
    from datetime import timezone
    h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
    return "posted under 1 hour ago" if h < 1 else (f"posted {int(h)} hours ago" if h < 48 else f"posted {int(h // 24)} days ago")


AGGREGATORS = ("indeed.", "linkedin.", "glassdoor.", "ziprecruiter.", "simplyhired.", "monster.", "careerbuilder.",
               "dice.com", "handshake", "wellfound.", "builtin.", "jooble.", "talent.com", "adzuna.")
ATS_HOSTS = {"jobs.lever.co": "Lever", "jobs.ashbyhq.com": "Ashby", "boards.greenhouse.io": "Greenhouse",
             "job-boards.greenhouse.io": "Greenhouse", "myworkdayjobs.com": "Workday",
             "jobs.smartrecruiters.com": "SmartRecruiters", "apply.workable.com": "Workable", "icims.com": "iCIMS"}


def apply_site(url: str) -> str:
    """Where the link opens: the company's careers site, or its official application page on its hiring system."""
    from urllib.parse import urlparse

    host = urlparse(url or "").netloc.lower()
    for k, v in ATS_HOSTS.items():
        if host.endswith(k):
            return f"company's official application page ({v})"
    return f"company careers site ({host})" if host else ""


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")[:50]


DONE_STATUSES = {"applied", "saved", "skipped", "not interested in company"}


def _skips(tracker: pd.DataFrame) -> tuple[set, set]:
    """Never show again: roles applied to, saved (they live in Saved), skipped. Roles left 'to apply' can return."""
    urls = set(tracker.loc[tracker["status"].isin(DONE_STATUSES), "url"]) if len(tracker) else set()
    skip_cos = set()
    cutoff = (date.today() - timedelta(days=14)).isoformat()
    for _, r in tracker.iterrows():
        st = r.get("status", "")
        if st == "not interested in company" or (st == "applied" and r.get("date", "") >= cutoff):
            skip_cos.add(r["company"].lower())
    return urls, skip_cos


def search_chain(llm) -> list[tuple[str, str, str]]:
    """Every search key the person has, in order: (engine, key, label). Gemini keys can search the web too."""
    from .keys import load, search_keys

    out, seen = [], set()
    gem = []
    if llm is not None:
        for m in llm.chain():
            if "generativelanguage" in str(getattr(m.client, "base_url", "")):
                gem.append(m.client.api_key)
    gem += [e["key"] for e in load() if e["provider"].startswith("Google Gemini")]
    for k in gem:
        if k and k not in seen:
            seen.add(k)
            out.append(("gemini", k, f"Gemini key …{k[-4:]}"))
    for engine, name in (("tavily", "Tavily"), ("jsearch", "JSearch")):
        for k in search_keys(engine):
            if k not in seen:
                seen.add(k)
                out.append((engine, k, f"{name} key …{k[-4:]}"))
    return out


def _web_fill(settings, llm, kept, mix, skip_urls, skip_cos, h1b, rtok, p, size_map=None) -> str:
    """If a size group is short, search the web to fill it: Gemini + Google Search first, then Tavily, then JSearch
    (whichever keys this person added). Every link is opened and checked first. Returns notes for the person,
    including any 'used up for today / this month' messages."""
    if not settings.get("web_fill", True):
        return ""
    from .websearch import find_jobs

    engines = search_chain(llm)
    if not engines:
        return ""
    size_of = (lambda c: (size_map or {}).get(str(c).lower()))
    on_list = None
    if settings.get("needs_sponsorship", True) and size_map:
        from .websearch import list_matcher
        on_list = list_matcher(list(size_map.items()))
    notes, dead, added = [], set(), {}
    have = {t: sum(1 for j in kept if j.get("size") == t) for t in mix}
    for tier, n in mix.items():
        short = int(n) - have.get(tier, 0)
        for engine, key, lab in engines:
            if short <= 0:
                break
            if lab in dead:
                continue
            p(0.42, f"Only {int(n) - short} {tier} roles so far; searching the web with {lab} for {short} more...")
            found = []
            models = [llm.model, "gemini-flash-latest", "gemini-flash-lite-latest"] if engine == "gemini" else [None]
            for model in dict.fromkeys(models):
                try:
                    found = find_jobs(key, model, settings, tier, short, AGGREGATORS,
                                      skip_urls | {j["url"] for j in kept}, progress=lambda m: p(0.44, m),
                                      engine=engine, size_of=size_of)
                    break
                except Exception as e:
                    msg = str(e)
                    if "used up" in msg or "didn't accept" in msg:
                        dead.add(lab)
                        nxt = next((l for _, _, l in engines if l not in dead), None)
                        note = (f"Web search: {lab} is used up for today. Switching to {nxt}." if nxt else
                                f"Web search: {lab} is used up for today, and there's no other search key left. "
                                "Add another key below.")
                        if note not in notes:
                            notes.append(note)
                        break
            if on_list:  # sponsorship needed: only companies on your sponsor list
                found = [f for f in found if on_list(f.get("company", ""))]
            ok, _ = apply_filters(found, settings, skip_urls, skip_cos, h1b)
            for j in ok:
                j["quick"] = quick_score(j, rtok)
                j["age_tier"] = age_tier(j)
                j["size"] = tier
            kept.extend(ok)
            short -= len(ok)
            if ok:
                added[lab] = added.get(lab, 0) + len(ok)
    if added:
        notes.append(f"Web search filled {sum(added.values())} roles the job boards didn't have ("
                     + ", ".join(f"{k}: {v}" for k, v in added.items()) + "), each link checked.")
    return " ".join(notes)


TAILOR = {"running": False, "done": 0, "total": 0, "folder": ""}
TAILOR_WORKERS = 4  # resumes tailored at the same time


def _wait_if_paused():
    """The person clicked Pause: hold new resumes until they click Resume (ones already started finish)."""
    import time as _t
    while TAILOR.get("paused") and TAILOR.get("running"):
        _t.sleep(0.5)


def pause_tailoring(on: bool):
    import time as _t
    if on and not TAILOR.get("paused"):
        TAILOR.update(paused=True, paused_at=_t.time())
    elif not on and TAILOR.get("paused"):
        TAILOR["started"] = TAILOR.get("started", _t.time()) + (_t.time() - TAILOR.get("paused_at", _t.time()))
        TAILOR["paused"] = False


def email_when_ready(folder) -> str:
    """Today's list and its resumes are done: email it right away (once a day), if the daily email is turned on."""
    from .mailer import email_ready, send_daily_list

    try:
        settings = store.load_settings()
        to = (settings.get("daily_email_to") or "").strip()
        if not (settings.get("daily_email") and to and email_ready()):
            return ""
        folder = Path(folder)
        if folder.name != date.today().isoformat():
            return ""
        last = store.DATA / "last_daily_email.txt"
        if last.exists() and last.read_text().strip() == date.today().isoformat():
            return ""  # already sent today
        picks = pd.read_csv(folder / "todays_list.csv").fillna("")
        if not len(picks):
            return ""
        send_daily_list(picks, str(folder), bool(settings.get("daily_email_attach_pdfs", True)), {"folder": str(folder)},
                        to=to)
        last.write_text(date.today().isoformat())
        TAILOR["emailed"] = to
        return to
    except Exception as e:  # noqa: BLE001
        TAILOR["email_error"] = str(e)[:200]
        return ""


def _workers(llm) -> int:
    """Up to 4 resumes in flight per AI key that has room."""
    if llm is None:
        return 1
    from . import quota
    live = sum(1 for m in llm.chain() if quota.check("ai:" + m.label)[0])
    return 30 if live else 1  # jobs share AI requests (5 per request) and each key paces its own requests  # requests on one key still start a few seconds apart (its free per-minute limit)


def _ai_left(llm) -> bool:
    """Does any AI key still have room today?"""
    if llm is None:
        return False
    from . import quota
    return any(quota.check("ai:" + m.label)[0] for m in llm.chain())


def _ai_for(llm, k: int):
    """Spread the work across every AI key that still has room today (each falls back down the chain if it runs out)."""
    if llm is None:
        return None
    from . import quota
    live = [m for m in llm.chain() if quota.check("ai:" + m.label)[0]] or [llm]
    return live[k % len(live)]


def _tailor_in_background(picks, rows, make_row, folder, llm=None):
    """Tailor each resume after the list is already on screen; update the list file as each one finishes."""
    import threading

    import time as _t
    TAILOR.update(running=True, done=0, total=len(picks), folder=str(folder), _llm=llm, started=_t.time(), edited=0,
                  ai_out=False, last_error="", email_error="", paused=False)

    import threading as _th
    from concurrent.futures import ThreadPoolExecutor
    lock = _th.Lock()

    def one(args):
        k, (i, j) = args
        _wait_if_paused()
        try:
            new = make_row(i, j, True, _ai_for(TAILOR.get("_llm"), k))
            with lock:
                rows[i - 1] = new
        except Exception:
            pass
        with lock:
            TAILOR["done"] += 1
            if rows[i - 1] and rows[i - 1].get("resume") == "tailored":
                TAILOR["edited"] += 1
            if rows[i - 1] and "AI limit" in str(rows[i - 1].get("resume", "")):
                TAILOR["ai_out"] = True
            pd.DataFrame(rows).to_csv(folder / "todays_list.csv", index=False)

    def work():
        try:
            with ThreadPoolExecutor(max_workers=_workers(TAILOR.get("_llm"))) as ex:
                _cx = __import__('contextvars').copy_context()
                list(ex.map(lambda a: _cx.copy().run(one, a), enumerate(enumerate(picks, 1))))
        finally:
            TAILOR["running"] = False
        email_when_ready(folder)

    import contextvars
    ctx = contextvars.copy_context()  # keep this person's private workspace inside the background thread
    threading.Thread(target=lambda: ctx.run(work), daemon=True).start()


RUN = {"running": False, "frac": 0.0, "msg": "", "result": None, "error": ""}


def retailor_background(folder: str, settings: dict, base_resume: dict, llm) -> int:
    """Edit just the resumes that weren't edited (e.g. after adding a new key), without redoing the search."""
    import contextvars
    import threading
    import time as _t
    from concurrent.futures import ThreadPoolExecutor

    from .render import save_rendered
    from .resume import BULLET_CHARS, merge_experience, merge_projects, tailor_resume

    folder = Path(folder)
    csv = folder / "todays_list.csv"
    if TAILOR["running"] or not csv.exists() or llm is None:
        return 0
    df = pd.read_csv(csv).fillna("")
    todo = [i for i, r in df.iterrows()  # also ones left half-way when the app was closed
            if str(r.get("resume", "")).startswith("not edited") or "tailoring" in str(r.get("resume", ""))]
    if not todo:
        return 0
    resume = merge_experience(merge_projects(base_resume, store.load_project_bank()), store.load_experience_bank())
    mp = int(settings.get("max_projects", 3))
    max_exp = int(settings.get("max_experience") or 0) or len(base_resume.get("experience", [])) or 99
    bchars = BULLET_CHARS["original" if settings.get("resume_format") == "original" else "clean"]
    rules = settings.get("resume_rules", "") + f"\n- The finished resume must fit on {int(settings.get('resume_pages', 1))} page(s)."
    for i in todo:
        df.at[i, "resume"] = "base (tailoring...)"
    df.to_csv(csv, index=False)
    TAILOR.update(running=True, done=0, total=len(todo), folder=str(folder), _llm=llm, started=_t.time(), edited=0,
                  ai_out=False, last_error="", email_error="", paused=False)
    lock = threading.Lock()

    def one(args):
        k, i = args
        _wait_if_paused()
        pdf = Path(str(df.at[i, "pdf"]))
        meta = pdf.with_suffix(".json")
        status = "not edited"
        try:
            job = json.loads(meta.read_text())["job"] if meta.exists() else {"company": df.at[i, "company"],
                                                                              "title": df.at[i, "title"], "description": ""}
            if not _ai_left(llm):
                status = "not edited (AI limit)"
            else:
                tailored, rep = tailor_resume(_ai_for(llm, k), resume, job, mp, rules, max_exp, bchars)
                tailored = dict(tailored, projects=tailored.get("projects", [])[:mp])
                save_rendered(pdf.with_suffix(""), tailored, settings, base_resume)
                meta.write_text(json.dumps({"resume": tailored, "job": job}, indent=2))
                status = "tailored"
                with lock:
                    df.at[i, "keywords mirrored"] = ", ".join(rep.get("keywords_used", [])[:8])
        except Exception as e:
            status = "not edited (AI limit)" if "used up" in str(e) else "not edited"
            TAILOR["last_error"] = f"{type(e).__name__}: {str(e)[:180]}"
        with lock:
            df.at[i, "resume"] = status
            TAILOR["done"] += 1
            TAILOR["edited"] += status == "tailored"
            TAILOR["ai_out"] = TAILOR["ai_out"] or "AI limit" in status
            df.to_csv(csv, index=False)

    ctx = contextvars.copy_context()

    def work():
        try:
            with ThreadPoolExecutor(max_workers=_workers(TAILOR.get("_llm"))) as ex:
                _cx = contextvars.copy_context()
                list(ex.map(lambda a: _cx.copy().run(one, a), enumerate(todo)))
        finally:
            TAILOR["running"] = False
        email_when_ready(folder)

    threading.Thread(target=lambda: ctx.run(work), daemon=True).start()
    return len(todo)


def start_run_background(settings: dict, resume: dict, llm) -> bool:
    """Build today's list in the background (the app does this by itself the first time it's opened each day)."""
    import contextvars
    import threading

    if RUN["running"] or TAILOR["running"]:
        return False
    RUN.update(running=True, frac=0.0, msg="Starting...", result=None, error="", paused=False)
    ctx = contextvars.copy_context()

    def work():
        try:
            def prog(f, m):
                RUN.update(frac=f, msg=m)
                import time as _t
                while RUN.get("paused"):  # Pause clicked: hold the search here until Resume
                    _t.sleep(0.5)
            RUN["result"] = run_today(settings, resume, llm, tailor_now=False, progress=prog)
        except Exception as e:  # noqa: BLE001
            RUN["error"] = str(e)[:200]
        finally:
            RUN["running"] = False

    threading.Thread(target=lambda: ctx.run(work), daemon=True).start()
    return True


def todays_list_path():
    return store.OUTPUT / date.today().isoformat() / "todays_list.csv"


def run_today(settings: dict, resume: dict, llm=None, progress=None, jobs_override=None, tailor_now: bool = True) -> dict:
    _raw = progress or (lambda f, m: None)
    _step = {"n": 1, "label": "Reading job boards"}

    def p(f, m):  # map the old overall fractions onto 4 clear steps, each with its own full bar
        n, label, lo, hi = ((1, "Reading job boards", 0.0, 0.35) if f < 0.36 else
                            (2, "Searching the web", 0.36, 0.45) if f < 0.45 else
                            (3, "Scoring roles for you", 0.45, 0.6) if f < 0.6 else
                            (4, "Tailoring resumes", 0.6, 1.0))
        _raw(min(max((f - lo) / (hi - lo), 0.0), 1.0) if f < 1.0 else 1.0, f"Step {n} of 4 · {label}: {m}")
    companies = store.load_companies()
    try:
        from .company_lists import auto_user_list, enrich
        if settings.get("auto_list"):
            fresh = auto_user_list(settings, companies)
            if fresh is not None and len(fresh):
                store.save_companies(fresh)
                companies = fresh
        companies = enrich(companies)
    except Exception:
        pass
    if jobs_override is None:
        p(0.0, "Pulling fresh postings...")
        from .sources import configure
        configure(settings["roles"])
        jobs, errors = fetch_all(companies.to_dict("records"), progress=lambda f, m: p(min(0.35 * f, 0.3599), m))
    else:
        jobs, errors = jobs_override, {}

    tracker = store.load_tracker()
    skip_urls, skip_cos = _skips(tracker)
    # also search the web for your roles and keep postings from ANY company on your list
    # (this covers list companies whose job page the agent hasn't found)
    list_notes = []
    if jobs_override is None and settings.get("web_fill", True):
        try:
            from . import quota
            from .websearch import search_list_companies
            chain = search_chain(llm)

            def first(engine, base):
                for e, k, lab in chain:
                    if e == engine and quota.check(f"{base}:{lab}")[0]:
                        return k
                return ""
            extra, list_notes = search_list_companies(
                settings, list(zip(companies["company"].astype(str), companies["size"].astype(str))), AGGREGATORS,
                {j.get("url") for j in jobs} | skip_urls, jsearch_key=first("jsearch", "jsearch"),
                gemini_key=first("gemini", "gemini_search"), gemini_model="gemini-flash-latest",
                progress=lambda m: p(0.37, m))
            jobs = jobs + extra
            if extra:
                list_notes.append(f"Web search found {len(extra)} roles at companies on your list whose job page "
                                  "isn't connected yet.")
        except Exception as e:
            list_notes.append(f"Web search skipped: {str(e)[:100]}")
    h1b = load_h1b_names(str(store.H1B)) if store.H1B.exists() else None
    jobs = [j for j in jobs if j.get("url") and not any(a in j["url"].lower() for a in AGGREGATORS)]
    kept, dropped = apply_filters(jobs, settings, skip_urls, skip_cos, h1b)
    p(0.44, f"{len(jobs):,} postings found, {len(kept):,} pass your filters")

    from .resume import BULLET_CHARS, merge_experience, merge_projects
    base_resume = resume
    original_format = settings.get("resume_format") == "original"
    n_exp_base = len(resume.get("experience", []))
    # both formats can now swap in the best projects and roles from your banks (your own file keeps its own style)
    resume = merge_experience(merge_projects(resume, store.load_project_bank()), store.load_experience_bank())
    max_exp = int(settings.get("max_experience") or 0) or n_exp_base or 99
    bchars = BULLET_CHARS["original" if original_format else "clean"]
    rtok = _tokens(resume_as_text(resume))
    for j in kept:
        j["quick"] = quick_score(j, rtok)
        j["age_tier"] = age_tier(j)
    kept.sort(key=lambda j: (j["age_tier"], -j["quick"]))  # newest first, then best match

    total = int(settings.get("daily_count", 20))
    mix = settings.get("size_mix", {"big": 7, "mid": 7, "small": 6})
    size_map = {str(c).lower(): sz for c, sz in zip(companies["company"], companies["size"])} if len(companies) else {}
    _all = list_notes + [_web_fill(settings, llm, kept, mix, skip_urls, skip_cos, h1b, rtok, p, size_map)]
    web_note = " ".join(dict.fromkeys(n.strip() for n in " ".join(_all).replace(". ", ".\n").split("\n") if n.strip()))
    # Always aim for the full number: if exact matches fall short, widen step by step and label what was widened.
    if len(kept) < total and jobs_override is None:
        have = {j["url"] for j in kept}
        stop = {"and", "or", "of", "the", "a", "an", "to", "in", "for", "i", "ii", "iii", "jr", "junior", "entry", "level",
                "associate", "intern", "new", "grad", "graduate", "early", "career"}
        words = sorted({w for r in settings.get("roles", []) for w in re.split(r"[\s/,&-]+", r.lower())
                        if len(w) > 2 and w not in stop})
        widen = [
            ("similar title", dict(settings, roles=words)),
            ("other U.S. location", dict(settings, roles=words, locations=[], allow_remote=True)),
        ]
        for label, s2 in widen:
            if len(kept) >= total or not words:
                break
            more, _ = apply_filters(jobs, s2, skip_urls, skip_cos, h1b)
            for j in more:
                if j["url"] in have:
                    continue
                have.add(j["url"])
                j["quick"] = quick_score(j, rtok) - 5
                j["age_tier"] = age_tier(j)
                j["near"] = label
                kept.append(j)
        if len(kept) < total:
            web_note += (f" Only {len(kept)} roles matched today, even after including similar titles and other U.S. "
                         "locations. Add more companies or roles in Setup to widen the net.")
    kept.sort(key=lambda j: (j.get("near", "") != "", j["age_tier"], -j["quick"]))
    # Only send the most promising few per tier to the AI (protects the free quota)
    for j in kept:  # roles from companies with no known size go under "Other companies" (never dropped)
        if j.get("size") not in mix:
            known = size_map.get(str(j.get("company", "")).lower())
            j["size"] = known if known in mix else "other"
    shortlist = []
    for tier, n in mix.items():
        shortlist += [j for j in kept if j.get("size") == tier][: max(n * 3, 6)]
    shortlist += [j for j in kept if j.get("size") == "other"][:total]  # they fill any size group that runs short
    if llm and shortlist:
        score_with_ai(llm, shortlist, resume, progress=lambda f, m: p(0.45 + 0.1499 * f, m))
    shortlist = shortlist or kept
    if len(shortlist) < total:  # never let the AI shortlist cut the list below the full number
        shortlist += [j for j in kept if j not in shortlist][: total - len(shortlist)]
    picks = pick_balanced(shortlist, mix, total)

    folder = store.OUTPUT / date.today().isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    from .render import save_rendered
    name = _slug(resume.get("name", "Resume"))
    mp = int(settings.get("max_projects", 3))

    def make_row(i, j, tailor: bool, ai=None):
        ai = ai or llm
        report = {"rewritten": 0, "reverted": 0, "keywords_used": [], "fit_notes": ""}
        tailored = dict(resume, projects=resume.get("projects", [])[:mp], experience=resume.get("experience", [])[:max_exp])
        status = "base (tailoring...)"
        if tailor and not _ai_left(llm):  # every AI key is out for today: don't wait, say so
            status = "not edited (AI limit)"
        elif ai and tailor:
            try:
                rules = settings.get("resume_rules", "") + f"\n- The finished resume must fit on {int(settings.get('resume_pages', 1))} page(s)."
                tailored, report = tailor_resume(ai, resume, j, mp, rules, max_exp, bchars)
                status = "tailored"
            except Exception as e:
                status = "not edited (AI limit)" if "used up" in str(e) else "not edited"
                TAILOR["last_error"] = f"{type(e).__name__}: {str(e)[:180]}"
                report["fit_notes"] = f"(Not edited, base resume used: {str(e)[:90]})"
        elif tailor:
            status = "not edited"
        if len(tailored.get("projects", [])) > mp:  # never print more projects than you chose
            tailored = dict(tailored, projects=tailored["projects"][:mp])
        stem = folder / f"{i:02d}_{_slug(j['company'])}_{_slug(j['title'])}_{name}"
        if tailor or not base_pdf.get("path"):
            pdf_path, out = save_rendered(stem, tailored, settings, base_resume)
            report["pages"] = out["pages"]
        else:  # quick list: copy the one base PDF now, the tailored one replaces it in the background
            import shutil
            pdf_path = stem.with_suffix(base_pdf["path"].suffix)
            shutil.copyfile(base_pdf["path"], pdf_path)
        stem.with_suffix(".json").write_text(json.dumps({
            "resume": tailored,
            "job": {"company": j["company"], "title": j["title"], "url": j["url"], "description": j["description"][:6000]},
        }, indent=2))
        return {
            "#": i, "company": j["company"], "size": j.get("size", ""), "industry": j.get("industry", ""),
            "title": j["title"], "location": j["location"], "posted": _ago(j.get("posted_at")),
            "score": j.get("ai_score", ""),
            "why": (f"({j['near']}) " if j.get("near") else "") + (j.get("why", "") or report.get("fit_notes", "")),
            "lead with": j.get("angle", ""),
            "sponsorship": j.get("sponsorship", ""), "h1b history": {True: "yes", False: "not found", None: "-"}[j.get("h1b_history")],
            "salary max": f"${j['salary_max']:,}" if j.get("salary_max") else "", "keywords mirrored": ", ".join(report["keywords_used"][:8]),
            "bullets rewritten": report["rewritten"], "bullets kept (guardrail)": report["reverted"],
            "pages": report.get("pages") or "", "resume": status,
            "url": j["url"], "apply_site": apply_site(j["url"]), "pdf": str(pdf_path),
        }

    base_pdf = {}
    rows = []
    if tailor_now or not llm:
        rows = [None] * len(picks)
        done_n = [0]

        def one(args):
            k, (i, j) = args
            rows[i - 1] = make_row(i, j, True, _ai_for(llm, k))
            done_n[0] += 1
            p(0.6 + 0.3999 * done_n[0] / max(len(picks), 1), f"{done_n[0]}/{len(picks)} ready")
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=_workers(llm)) as ex:
            _cx = __import__('contextvars').copy_context()
            list(ex.map(lambda a: _cx.copy().run(one, a), enumerate(enumerate(picks, 1))))
    else:
        p(0.61, "Preparing your list (tailored resumes follow in the background)...")
        bp, _ = save_rendered(folder / "_base_resume", dict(base_resume, projects=base_resume.get("projects", [])[:mp]),
                              settings, base_resume)
        base_pdf["path"] = Path(bp)
        rows = [make_row(i, j, False) for i, j in enumerate(picks, 1)]
        _tailor_in_background(picks, rows, make_row, folder, llm)

    df = pd.DataFrame(rows)
    if len(df):  # an empty re-run never wipes out today's earlier list
        df.to_csv(folder / "todays_list.csv", index=False)
    new = pd.DataFrame([{"date": date.today().isoformat(), "company": r["company"], "size": r["size"], "title": r["title"],
                         "location": r["location"], "url": r["url"], "score": r["score"], "status": "to apply", "pdf": r["pdf"]}
                        for r in rows])
    if len(new):
        keep = tracker[~(tracker["url"].isin(new["url"]) & (tracker["status"] == "to apply"))] if len(tracker) else tracker
        store.save_tracker(pd.concat([keep, new], ignore_index=True))
    p(1.0, "Done")
    return {"picks": df, "dropped": dropped, "errors": errors, "found": len(jobs), "passed": len(kept), "web": web_note,
            "folder": str(folder), "ran_at": datetime.now().strftime("%b %d, %I:%M %p")}
