"""Resume structure, PDF import, and truthful per-job tailoring with guardrails."""
from __future__ import annotations

import copy
import json
import io
import re
import threading
import time

RESUME_TEMPLATE = {
    "name": "Your Name",
    "contact": ["you@email.com", "(000) 000-0000", "linkedin.com/in/you", "yoursite.com"],
    "education": [
        {"school": "University", "degree": "MS in ...", "location": "City, ST", "dates": "Aug 2025 - May 2027", "details": ["Relevant coursework: ..."]}
    ],
    "experience": [
        {"title": "Role", "org": "Company", "location": "City, ST", "dates": "Jun 2026 - Aug 2026", "bullets": ["Verb-first bullet with a result you can defend"]}
    ],
    "leadership": [
        {"title": "Role", "org": "Club or organization", "location": "", "dates": "", "bullets": ["What you led and the result"]}
    ],
    "projects": [
        {"name": "Project name", "link": "yoursite.com/project", "dates": "2026", "bullets": ["What you built and the result"]}
    ],
    "skills": {"Tools": "Python, SQL, Tableau", "Professional": "Product strategy, stakeholder management", "Interests": "..."},
}

PARSE_SYSTEM = """You convert a resume (plain text, or LaTeX source) into JSON. Copy wording exactly; do not rewrite, add or drop facts.
If it is LaTeX, ignore formatting commands and turn escapes like \\% and \\& into plain characters.
Schema: {"name": str, "contact": [str], "education": [{"school","degree","location","dates","link","details":[str]}],
"experience": [{"title","org","location","dates","link","bullets":[str]}],
"leadership": [{"title","org","location","dates","link","bullets":[str]}],
"projects": [{"name","subtitle","link","dates","bullets":[str]}],
"skills": {"Tools": str, "Professional": str, "Interests": str}}
Sections:
- experience = jobs and internships only.
- leadership = student clubs, campus organizations, societies, volunteering, team captaincies and similar roles,
  even if the resume lists them under Education or Activities. Never put these under experience.
- contact = each item separately (city, phone, email, LinkedIn, portfolio, GitHub). For links, write the URL itself
  (e.g. "linkedin.com/in/name"); if a link has a visible label, keep the URL and drop the label.
Links: the text may end with "LINKS IN THE FILE (visible text -> address)", and URLs may appear in parentheses after words.
Attach every link to the place it belongs: contact items (LinkedIn, portfolio, GitHub, email), a project's "link"
(a link labelled "Project Website", "Demo", "GitHub" etc. on a project's heading line is that project's link),
or the "link" of the experience, leadership or education entry it sits in (for example a published research paper or
thesis). If the link sits on words inside a bullet (like a research paper), keep it inline in that bullet as
[the linked words](url), exactly where it appears. Never invent a link,
and never drop one that appears in the file. Write links without "https://".
A project's second line (like an award or competition) goes in "subtitle".
Copy every bullet in full, including bullets under leadership. Use "" for anything missing."""


def missing_links(resume: dict, source: str) -> list[str]:
    """Link addresses found in the file that didn't end up anywhere on the parsed resume."""
    blob = json.dumps(resume).lower()
    found = set(re.findall(r"(?:https?://)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s)\]\"']*)?", source.lower()))
    out = []
    for u in found:
        core = re.sub(r"^https?://(www\.)?", "", u).rstrip("/.,;")
        if "@" in core or len(core) < 6 or core.endswith((".tex", ".pdf", ".sty", ".cls")):
            continue
        if core not in blob:
            out.append(core)
    return sorted(out)


def unmatched_lines(resume: dict, source: str) -> list[str]:
    """Bullets that don't appear in the source text (possible misreads). Compares letters and digits only."""
    norm = lambda t: re.sub(r"[^a-z0-9]", "", re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t or "").lower())
    src = norm(source)
    bad = []
    for sec in ("experience", "leadership", "projects"):
        for e in resume.get(sec, []) or []:
            for b in e.get("bullets", []) or []:
                n = norm(b)
                if n and n[:60] not in src and n[-60:] not in src:
                    bad.append(b)
    return bad


def merge_experience(resume: dict, bank: list[dict]) -> dict:
    """Resume experience + every experience-bank role not already on the resume (matched by organization + title)."""
    if not bank:
        return resume
    norm = lambda e: re.sub(r"[^a-z0-9]", "", ((e.get("org") or "") + (e.get("title") or "")).lower())
    have = {norm(e) for e in resume.get("experience", [])}
    merged = copy.deepcopy(resume)
    for e in bank:
        if norm(e) and norm(e) not in have:
            merged.setdefault("experience", []).append(
                {k: e.get(k, "") for k in ("title", "org", "location", "dates", "link")} | {"bullets": list(e.get("bullets", []))})
            have.add(norm(e))
    return merged


def merge_projects(resume: dict, bank: list[dict]) -> dict:
    """Resume projects + every project-bank project not already on the resume (matched by name)."""
    if not bank:
        return resume
    norm = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())
    have = {norm(p.get("name", "")) for p in resume.get("projects", [])}
    merged = copy.deepcopy(resume)
    for p in bank:
        n = norm(p.get("name", ""))
        if n and not any(n[:18] in h or h[:18] in n for h in have if h):
            merged.setdefault("projects", []).append(
                {"name": p["name"], "link": p.get("link", ""), "dates": p.get("dates", ""),
                 "bullets": list(p.get("bullets", [])), "tags": p.get("tags", "")})
            have.add(n)
    return merged


BANK_SYSTEM = """You turn a person's own project descriptions (portfolio text, project pages, notes) into resume entries.
Honesty rules: use only facts in the text. Copy every number exactly as written. Never invent tools, results or numbers.
For each project: 1-3 bullets, each starting with a strong past-tense verb, about 20-30 words.
Return JSON: {"projects": [{"name": str, "link": str (only if a URL for that project appears in the text, else ""),
"subtitle": str (award or recognition, only if stated, else ""), "dates": str (only if stated, else ""), "tags": str (comma-separated skills, tools and industries it proves),
"bullets": [str]}]}"""


def build_project_bank(llm, text: str) -> list[dict]:
    out = llm.chat_json(BANK_SYSTEM, text[:30000], temperature=0, fast=True)
    projects = out.get("projects", []) if isinstance(out, dict) else []
    source_nums = _numbers(text)
    clean = []
    for p in projects:
        if not isinstance(p, dict) or not p.get("name"):
            continue
        # guardrail: drop any bullet containing a number that isn't in the source text
        p["bullets"] = [b for b in p.get("bullets", []) if isinstance(b, str) and _numbers(b) <= source_nums]
        clean.append({k: p.get(k, "") for k in ("name", "subtitle", "link", "dates", "tags")} | {"bullets": p["bullets"]})
    return clean


def _pdf_links(data: bytes) -> list[tuple[str, str, str]]:
    """(visible text, URL, line) for every clickable link in a PDF, including links hidden behind words."""
    try:  # exact word positions
        import pdfplumber

        out, seen = [], set()
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for pg in pdf.pages:
                words = pg.extract_words(x_tolerance=1.5)
                for h in pg.hyperlinks:
                    uri = h.get("uri")
                    if not uri or uri in seen:
                        continue
                    seen.add(uri)
                    cy = (h["top"] + h["bottom"]) / 2
                    mid = lambda w: ((w["x0"] + w["x1"]) / 2, (w["top"] + w["bottom"]) / 2)
                    inside = [w["text"] for w in words
                              if h["x0"] - 1 <= mid(w)[0] <= h["x1"] + 1 and h["top"] - 2 <= mid(w)[1] <= h["bottom"] + 2]
                    line = [w["text"] for w in words if abs(mid(w)[1] - cy) < 4]
                    out.append((" ".join(inside)[:100], uri, " ".join(line)[:160]))
        return out
    except Exception:
        pass
    return _pdf_links_basic(data)


def _pdf_links_basic(data: bytes) -> list[tuple[str, str, str]]:
    from pypdf import PdfReader

    out = []
    reader = PdfReader(io.BytesIO(data))
    for page in reader.pages:
        annots = page.get("/Annots") or []
        links = []
        for a in annots:
            try:
                o = a.get_object()
                uri = (o.get("/A") or {}).get("/URI")
                if o.get("/Subtype") == "/Link" and uri:
                    links.append([float(v) for v in o["/Rect"]] + [str(uri)])
            except Exception:
                continue
        if not links:
            continue
        frags = []

        def visit(text, cm, tm, fd, fs):
            if text and text.strip():
                x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
                y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
                size = abs((fs or 10) * (tm[0] or 1) * (cm[0] or 1))
                frags.append((x, y, text, size))

        try:
            page.extract_text(visitor_text=visit)
        except Exception:
            pass
        for x1, y1, x2, y2, uri in links:
            lo_x, hi_x, lo_y, hi_y = min(x1, x2) - 2, max(x1, x2) + 2, min(y1, y2) - 4, max(y1, y2) + 4
            label_chars, line = [], ""
            for (x, y, t, size) in frags:
                if not (lo_y <= y <= hi_y):
                    continue
                line = (line + " " + t).strip()
                w = size * 0.5  # average character width, good enough to find the words under the link
                label_chars += [ch for i, ch in enumerate(t) if lo_x <= x + (i + 0.5) * w <= hi_x]
            label = re.sub(r"\s+", " ", "".join(label_chars)).strip(" |·,")
            line = re.sub(r"\s+", " ", line)[:160]
            i = line.find(label) if label else -1
            if i >= 0:  # widen to whole words, since character positions are estimates
                start = len(re.split(r"[\s|·,]", line[:i])[-1]) if i else 0
                j = i + len(label)
                tail = re.split(r"[\s|·,]", line[j:])[0]
                label = (line[i - start:i] + label + tail).strip(" |·,")
            label = label[:80]
            out.append((label, uri, line))
    seen, uniq = set(), []
    for label, uri, line in out:
        if uri not in seen:
            seen.add(uri)
            uniq.append((label, uri, line))
    return uniq


def _links_block(links: list[tuple[str, str, str]]) -> str:
    if not links:
        return ""
    return "\n\nLINKS IN THE FILE (visible text -> address, and the line it sits on):\n" + "\n".join(
        f"- {label or '(hidden link)'} -> {uri}" + (f"   [line: {line}]" if line else "") for label, uri, line in links)


def file_to_text(name: str, data: bytes) -> str:
    """Resume text from a PDF, LaTeX (.tex), Word (.docx) or plain-text file, with every link address kept."""
    n = name.lower()
    if n.endswith(".pdf"):
        return pdf_to_text(data) + _links_block(_pdf_links(data))
    if n.endswith(".docx"):
        import zipfile
        from html import unescape

        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml = z.read("word/document.xml").decode("utf-8", "ignore")
            try:
                rels = z.read("word/_rels/document.xml.rels").decode("utf-8", "ignore")
            except KeyError:
                rels = ""
        targets = dict(re.findall(r'Id="([^"]+)"[^>]*Target="([^"]+)"', rels))
        targets.update({k: v for v, k in re.findall(r'Target="([^"]+)"[^>]*Id="([^"]+)"', rels)})

        def hyper(m):
            inner = re.sub(r"<[^>]+>", "", m.group(2))
            url = targets.get(m.group(1), "")
            return f"{inner} ({url})" if url else inner

        xml = re.sub(r'<w:hyperlink[^>]*r:id="([^"]+)"[^>]*>(.*?)</w:hyperlink>', hyper, xml, flags=re.S)
        xml = re.sub(r"</w:p>", "\n", xml)
        xml = re.sub(r"<w:tab/>", " ", xml)
        return unescape(re.sub(r"<[^>]+>", "", xml))
    text = data.decode("utf-8", "ignore")
    if n.endswith(".tex"):
        if "\\begin{document}" in text:
            text = text.split("\\begin{document}", 1)[1]
        text = re.sub(r"(?m)(?<!\\)%.*$", "", text)  # comments (but not \%)
        # \href{url}{label} -> label (url), so links behind words are never lost
        text = re.sub(r"\\href\{([^}]*)\}\{((?:[^{}]|\{[^{}]*\})*)\}", lambda m: f"{m.group(2)} ({m.group(1)})", text)
        text = re.sub(r"\\url\{([^}]*)\}", r"\1", text)
    return text


ONE_PROJECT_SYSTEM = """Turn one project description into 2-3 resume bullets.
Honesty rules: use only facts in the description. Copy every number exactly. Never invent tools, results or numbers.
Each bullet starts with a strong past-tense verb, leads with the result when there is one, about 20-30 words.
Return JSON: {"bullets": [str], "tags": str (comma-separated skills, tools and industries this project proves)}"""


def project_from_fields(llm, title: str, award: str, link: str, tools: str, dates: str, description: str,
                        use_ai: bool = True) -> dict:
    name = title.strip() + (f" [{tools.strip()}]" if tools.strip() else "")
    bullets, tags = [], tools
    if use_ai and llm is not None and description.strip():
        out = llm.chat_json(ONE_PROJECT_SYSTEM, f"PROJECT: {title}\nTOOLS: {tools}\nAWARD: {award}\n\n{description}",
                            temperature=0.2, fast=True)
        src = _numbers(" ".join([title, tools, award, description]))
        bullets = [b for b in out.get("bullets", []) if isinstance(b, str) and _numbers(b) <= src]
        tags = out.get("tags") or tools
    if not bullets:  # no AI, or nothing survived the honesty check: use the person's own lines
        bullets = [l.strip(" -•\t") for l in description.splitlines() if l.strip(" -•\t")]
    link = re.sub(r"^https?://", "", link.strip()).rstrip("/")
    return {"name": name, "subtitle": award.strip(), "link": link, "dates": dates.strip(), "tags": tags, "bullets": bullets}


def pdf_to_text(file_bytes: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(file_bytes))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def parse_resume(llm, text: str) -> dict:
    data = llm.chat_json(PARSE_SYSTEM, text[:20000], temperature=0, fast=True)
    base = copy.deepcopy(RESUME_TEMPLATE)
    base.update({k: v for k, v in data.items() if k in base})
    return base


def resume_as_text(r: dict, limit: int = 6000) -> str:
    lines = [r.get("name", "")]
    for e in r.get("experience", []) + r.get("leadership", []):
        lines.append(f"{e.get('title')} - {e.get('org')}: " + " | ".join(e.get("bullets", [])))
    for p in r.get("projects", []):
        lines.append(f"Project {p.get('name')}: " + " | ".join(p.get("bullets", [])))
    lines.append("Skills: " + " ; ".join(f"{k}: {v}" for k, v in (r.get("skills") or {}).items()))
    for ed in r.get("education", []):
        lines.append(f"{ed.get('degree')} - {ed.get('school')}")
    return "\n".join(lines)[:limit]


TAILOR_SYSTEM = """You tailor a resume to one job posting. Work in this order:
1. Read the posting and list its 10-15 most important keywords: skills, tools, methods and domain terms it asks for.
2. Choose the entries that best prove fit for THIS posting: exactly {n_proj} project(s) and {n_exp} experience entr(ies)
   (fewer only if fewer exist). Use each entry's bullets and tags to judge relevance.
3. Rewrite every bullet of the chosen experience, leadership and project entries so it mirrors those keywords wherever
   the original truthfully supports them. Spread different keywords across bullets; don't repeat one keyword.
Honesty rules are absolute:
- Only rephrase, reorder, and choose emphasis. Never invent experience, tools, numbers, titles, or outcomes.
- Every number in a rewritten bullet must already appear in the ORIGINAL bullet it came from. Never add new numbers.
Style rules:
- Start every bullet with a strong past-tense verb. Never use: delivered, supported, assisted, helped, worked on, responsible for.
- LENGTH: every bullet must be {lo}-{hi} characters long including spaces (that is exactly one and a half lines on the page).
  Count carefully. Too short: add the how or the tool. Too long: cut filler words.
- Keep the same number of bullets for each entry.
- Keep any inline links written as [words](url) exactly as they are, inside the same bullet.
Return JSON:
{{"job_keywords": [str],
 "experience_order": [int],         // experience indexes, most relevant first; exactly {n_exp}
 "experience": [{{"index": int, "bullets": [str]}}],
 "leadership": [{{"index": int, "bullets": [str]}}],
 "project_order": [int],            // project indexes, most relevant first; exactly {n_proj}
 "projects": [{{"index": int, "bullets": [str]}}],   // bullets for the chosen projects
 "skills": {{"Tools": str, "Professional": str, "Interests": str}},   // same items, reordered for relevance; never add items
 "keywords_used": [str],            // job keywords you mirrored
 "fit_notes": str}}                 // one sentence: why this resume fits the role"""

FIX_LENGTH_SYSTEM = """Rewrite each bullet to be {lo}-{hi} characters long including spaces (one and a half lines), keeping its
meaning, its verb-first style, every number exactly as is, and any [words](url) link. Never add facts or numbers.
Return JSON: {{"bullets": [str]}} in the same order."""

_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def _numbers(s: str) -> set[str]:
    return {n.replace(",", "") for n in _NUM.findall(s or "")}


def _items(s: str) -> set[str]:
    return {i.strip().lower() for i in re.split(r"[,;]", s or "") if i.strip()}


BULLET_CHARS = {"original": (150, 175), "clean": (165, 195)}


BATCH_SIZE = 5       # jobs tailored in one AI request (free keys limit requests per minute, not jobs)
BATCH_WAIT_S = 1.2   # how long the first job waits for others to join its request
_BATCH = {"lock": threading.Lock(), "groups": {}}

BATCH_NOTE = """
SEVERAL POSTINGS: you get several JOBs, numbered. Do all the work above separately for each JOB (its own keywords,
its own choice of entries, its own rewrites). Return JSON {{"results": [...]}} with one object per JOB, in order, each
exactly the object described above plus "job": its number."""


def _job_text(job: dict, n: int) -> str:
    return f"JOB {n}: {job['title']} at {job['company']} ({job.get('location', '')})\nPOSTING:\n{(job.get('description') or '')[:3500]}"


def _tailor_batch(llm, resume, jobs, max_projects, rules, max_experience, bullet_chars) -> list:
    """One AI request for several jobs. Returns one (tailored, report) or Exception per job."""
    import json as _json

    n_proj = min(max_projects, len(resume.get("projects", [])))
    n_exp = min(max_experience, len(resume.get("experience", [])))
    lo, hi = bullet_chars
    system = TAILOR_SYSTEM.format(n_proj=n_proj, n_exp=n_exp, lo=lo, hi=hi) + BATCH_NOTE.format() + (
        f"\nThe candidate's own resume rules (follow them too):\n{rules}" if rules else "")
    user = ("\n\n".join(_job_text(j, k) for k, j in enumerate(jobs, 1)) + "\n\nRESUME JSON:\n"
            + _json.dumps({k: resume.get(k, []) for k in ("experience", "leadership", "projects", "skills")}))
    out = llm.chat_json(system, user, temperature=0.4, fast=True, max_tokens=min(32000, 3500 * len(jobs)))
    res = out.get("results") if isinstance(out, dict) else out
    res = res if isinstance(res, list) else []
    by_n = {r.get("job"): r for r in res if isinstance(r, dict)}
    results = []
    for k, j in enumerate(jobs, 1):
        r = by_n.get(k) or (res[k - 1] if len(res) >= k and isinstance(res[k - 1], dict) and "job" not in res[k - 1] else None)
        if not r:
            results.append(RuntimeError("missing from the shared answer"))
            continue
        t, report = apply_edits(resume, r, max_projects, max_experience)
        report["job_keywords"] = r.get("job_keywords", [])
        results.append((t, report))
    return results


def tailor_resume(llm, resume: dict, job: dict, max_projects: int = 3, rules: str = "", max_experience: int = 99,
                  bullet_chars: tuple[int, int] = (150, 175), batch: bool = True) -> tuple[dict, dict]:
    """Returns (tailored_resume, report). Any bullet that breaks the honesty checks is reverted to the original.
    When several jobs are tailored at the same time, they share one AI request (much faster on free keys)."""
    if batch and BATCH_SIZE > 1:
        key = (id(resume), max_projects, rules, max_experience, tuple(bullet_chars))
        me = {"job": job, "done": threading.Event(), "out": None, "llm": llm}
        with _BATCH["lock"]:
            g = _BATCH["groups"].setdefault(key, [])
            g.append(me)
            leader = len(g) == 1
        if leader:
            time.sleep(BATCH_WAIT_S)
            with _BATCH["lock"]:
                group = _BATCH["groups"].pop(key, [])
            chunks = [group[i:i + BATCH_SIZE] for i in range(0, len(group), BATCH_SIZE)]

            def run(chunk):
                try:
                    outs = _tailor_batch(chunk[0]["llm"], resume, [c["job"] for c in chunk], max_projects, rules, max_experience,
                                         bullet_chars)
                except Exception as e:  # noqa: BLE001
                    outs = [e] * len(chunk)
                for c, o in zip(chunk, outs):
                    c["out"] = o
                    c["done"].set()
            from concurrent.futures import ThreadPoolExecutor
            import contextvars
            cx = contextvars.copy_context()
            with ThreadPoolExecutor(max_workers=max(1, len(chunks))) as ex:
                list(ex.map(lambda ch: cx.copy().run(run, ch), chunks))
        me["done"].wait(timeout=600)
        o = me["out"]
        if isinstance(o, tuple):
            return o
        if isinstance(o, Exception) and "used up" in str(o):
            raise o
        # the shared request failed for this job: do it on its own
    return _tailor_one(llm, resume, job, max_projects, rules, max_experience, bullet_chars)


def _tailor_one(llm, resume: dict, job: dict, max_projects: int = 3, rules: str = "", max_experience: int = 99,
                bullet_chars: tuple[int, int] = (150, 175)) -> tuple[dict, dict]:
    import json as _json

    n_proj = min(max_projects, len(resume.get("projects", [])))
    n_exp = min(max_experience, len(resume.get("experience", [])))
    lo, hi = bullet_chars
    user = (
        f"JOB: {job['title']} at {job['company']} ({job.get('location','')})\n"
        f"POSTING:\n{job['description'][:7000]}\n\nRESUME JSON:\n"
        + _json.dumps({k: resume.get(k, []) for k in ("experience", "leadership", "projects", "skills")})
    )
    system = TAILOR_SYSTEM.format(n_proj=n_proj, n_exp=n_exp, lo=lo, hi=hi) + (
        f"\nThe candidate's own resume rules (follow them too):\n{rules}" if rules else "")
    out = llm.chat_json(system, user, temperature=0.4, fast=True)  # light model, little thinking: ~5x faster
    t, report = apply_edits(resume, out, max_projects, max_experience)
    report["job_keywords"] = out.get("job_keywords", [])
    # one quick repair pass for bullets that missed the 1.5-line length
    off = [(sec, i, k, b) for sec in ("experience", "leadership", "projects") for i, e in enumerate(t.get(sec, []))
           for k, b in enumerate(e.get("bullets", [])) if b and not (lo - 10 <= len(_plain_len(b)) <= hi + 10)]
    if len(off) >= 3:  # only spend an extra AI request when several bullets missed (saves your daily limit)
        try:
            fix = llm.chat_json(FIX_LENGTH_SYSTEM.format(lo=lo, hi=hi), _json.dumps([b for *_, b in off]), temperature=0.3, fast=True)
            for (sec, i, k, b), nb in zip(off, fix.get("bullets") or []):
                if isinstance(nb, str) and nb.strip() and _numbers(nb) <= _numbers(b):
                    t[sec][i]["bullets"][k] = nb.strip()
        except Exception:
            pass
    return t, report


def _plain_len(b: str) -> str:
    return re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", b or "")


def apply_edits(resume: dict, out: dict, max_projects: int = 3, max_experience: int = 99) -> tuple[dict, dict]:
    """Apply an edit plan (from Gemini or from Claude) with the honesty guardrails. Returns (resume, report)."""
    t = copy.deepcopy(resume)
    report = {"reverted": 0, "rewritten": 0, "keywords_used": out.get("keywords_used", []),
              "fit_notes": out.get("fit_notes", "") or out.get("notes", "")}

    for section in ("experience", "leadership", "projects"):
        for item in out.get(section, []) or []:
            i = item.get("index") if isinstance(item, dict) else None
            if not isinstance(i, int) or not (0 <= i < len(t.get(section, []))):
                continue
            orig = t[section][i].get("bullets", [])
            new = item.get("bullets") or []
            if len(new) != len(orig):
                continue
            fixed = []
            for o, n in zip(orig, new):
                if not n or not _numbers(n) <= _numbers(o):  # guardrail: no invented numbers
                    fixed.append(o)
                    report["reverted"] += 1
                else:
                    fixed.append(n.strip())
                    report["rewritten"] += n.strip() != o
            t[section][i]["bullets"] = fixed

    order = [i for i in (out.get("project_order") or []) if isinstance(i, int) and 0 <= i < len(t["projects"])]
    order = list(dict.fromkeys(order + list(range(len(t["projects"])))))
    t["projects"] = [t["projects"][i] for i in order][:max_projects]
    if len(t.get("experience", [])) > max_experience:  # more roles than fit: keep the most relevant ones, in time order
        eo = [i for i in (out.get("experience_order") or []) if isinstance(i, int) and 0 <= i < len(t["experience"])]
        eo = list(dict.fromkeys(eo + list(range(len(t["experience"])))))[:max_experience]
        t["experience"] = [t["experience"][i] for i in sorted(eo)]

    new_sk = out.get("skills") or {}
    for k, v in (resume.get("skills") or {}).items():
        cand = new_sk.get(k)
        if isinstance(cand, str) and _items(cand) and _items(cand) <= _items(v):  # guardrail: reorder only
            missing = [x.strip() for x in re.split(r"[,;]", v) if x.strip() and x.strip().lower() not in _items(cand)]
            t["skills"][k] = ", ".join([c.strip() for c in re.split(r"[,;]", cand) if c.strip()] + missing)
    return t, report
