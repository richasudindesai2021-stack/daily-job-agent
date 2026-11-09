"""Score matches against the resume, then pick a balanced daily list by company size."""
from __future__ import annotations

import re

from .resume import resume_as_text

STOP = set("""a an and are as at be by for from has have in into is it its of on or our that the their this to we will with you your
who what which work working team teams role across using use able experience years year including strong skills new help""".split())

RANK_SYSTEM = """You are a recruiting analyst. Score how well a candidate fits each job, 0-100, based only on the resume given.
Weigh: role-level fit (entry/early-career), skills overlap, domain overlap, and seniority mismatch (penalize roles needing 4+ years).
Return JSON: {"scores": [{"id": int, "score": int, "why": str, "angle": str}]}
"why" = one plain sentence a candidate can use, naming specific overlaps. "angle" = which part of the resume to lead with (5-8 words)."""


def _tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z+#.]{2,}", (s or "").lower()) if w not in STOP}


def quick_score(job: dict, resume_tokens: set[str]) -> float:
    jt = _tokens(job["title"] + " " + job["description"][:5000])
    if not jt:
        return 0.0
    overlap = len(jt & resume_tokens) / (len(jt) ** 0.5)
    bonus = 2 if job.get("sponsorship") == "friendly" else 0
    bonus += 2 if job.get("h1b_history") else 0
    return overlap + bonus


def score_with_ai(llm, jobs: list[dict], resume: dict, batch: int = 8, progress=None) -> None:
    rtext = resume_as_text(resume, 4000)
    for start in range(0, len(jobs), batch):
        chunk = jobs[start : start + batch]
        listing = "\n\n".join(
            f"[id {i}] {j['title']} at {j['company']} ({j['location']})\n{j['description'][:1500]}" for i, j in enumerate(chunk)
        )
        try:
            out = llm.chat_json(RANK_SYSTEM, f"RESUME:\n{rtext}\n\nJOBS:\n{listing}", temperature=0.2, fast=True)
            for s in out.get("scores", []):
                i = s.get("id")
                if isinstance(i, int) and 0 <= i < len(chunk):
                    chunk[i]["ai_score"] = int(s.get("score", 0))
                    chunk[i]["why"] = s.get("why", "")
                    chunk[i]["angle"] = s.get("angle", "")
        except Exception as e:
            for j in chunk:
                j.setdefault("why", f"(AI scoring skipped: {str(e)[:80]})")
        if progress:
            progress(min(1.0, (start + batch) / len(jobs)), f"Scored {min(start + batch, len(jobs))}/{len(jobs)} matches")


def pick_balanced(jobs: list[dict], mix: dict, total: int, one_per_company: bool = True) -> list[dict]:
    """mix like {"big": 7, "mid": 7, "small": 6}. Shortfalls in one tier are filled from the others."""
    # newest first (posted within 24 h, then 36 h, 48 h, 3 days, 1 week, 2 weeks), best fit within each
    # exact matches before widened ones ("similar title", "other U.S. location")
    key = lambda j: (0 if j.get("near") else 1, -j.get("age_tier", 0), j.get("ai_score", -1), j.get("quick", 0))
    ranked = sorted(jobs, key=key, reverse=True)
    picked, used_companies, used_urls = [], set(), set()

    def ok(j):
        return j["url"] not in used_urls and (not one_per_company or j["company"] not in used_companies)

    def take(j):
        picked.append(j)
        used_urls.add(j["url"])
        used_companies.add(j["company"])

    for tier, n in mix.items():
        for j in [x for x in ranked if x.get("size") == tier]:
            if sum(1 for p in picked if p.get("size") == tier) >= n:
                break
            if ok(j):
                take(j)
    for j in ranked:  # fill any gap
        if len(picked) >= total:
            break
        if ok(j):
            take(j)
    if len(picked) < total and one_per_company:  # allow a second role at a company if still short
        for j in ranked:
            if len(picked) >= total:
                break
            if j["url"] not in used_urls:
                take(j)
    order = {t: i for i, t in enumerate(mix)}
    return sorted(picked[:total], key=lambda j: (order.get(j.get("size"), 9), -key(j)[0], -key(j)[1]))
