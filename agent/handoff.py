"""Claude polish step (free, human-in-the-loop).

The agent exports today's tailored resumes + postings as one markdown file that already contains the prompt.
You drop it into a Claude chat, Claude replies with a JSON edit plan, you upload the reply, and the app
applies it with the same honesty guardrails and rebuilds the PDFs.
"""
from __future__ import annotations

import json
from pathlib import Path

from .llm import parse_json
from .pdf import build_pdf
from .resume import apply_edits

CLAUDE_PROMPT = """# Claude polish: tailor today's resumes

You are an expert resume editor for early-career product, analytics and consulting roles.
Below are {n} job postings, each with the resume my job agent already tailored for it.
Improve each resume for its posting.

## Honesty rules (my app enforces these; edits that break them are thrown out)
- Only rephrase, reorder and change emphasis. Never invent experience, tools, numbers, titles or outcomes.
- Every number in a bullet must already be in that same bullet. Don't add numbers.
- Keep the same number of bullets per experience, leadership role and project.
- Skills: reorder items only; never add new ones.
- Keep inline links written as [words](url) exactly as they are.

## My resume rules
{rules}

## What to send back
Reply with ONE JSON code block and nothing else outside it:

```json
{{"resumes": [
  {{"id": "<id from the posting header>",
    "experience": [{{"index": 0, "bullets": ["...", "..."]}}],
    "leadership": [{{"index": 0, "bullets": ["..."]}}],
    "projects":   [{{"index": 0, "bullets": ["..."]}}],
    "project_order": [0, 1, 2],
    "skills": {{"Tools": "...", "Professional": "...", "Interests": "..."}},
    "keywords_used": ["..."],
    "notes": "one sentence: the biggest change and why"}}
]}}
```
Include every id. Indexes refer to positions in that resume's JSON.
"""


def build_handoff(folder: Path, rules: str) -> tuple[str, int]:
    items = sorted(folder.glob("*.json"))
    listing = folder / "todays_list.csv"
    if listing.exists():  # only the latest run's picks
        import pandas as pd

        names = {Path(p).with_suffix(".json").name for p in pd.read_csv(listing)["pdf"].dropna()}
        items = [i for i in items if i.name in names] or items
    parts = []
    for jf in items:
        d = json.loads(jf.read_text())
        r, job = d["resume"], d["job"]
        slim = {k: r.get(k) for k in ("experience", "leadership", "projects", "skills")}
        parts.append(
            f"---\n## id: {jf.stem}\n**{job['title']} at {job['company']}**  ({job['url']})\n\n"
            f"### Posting (excerpt)\n{job['description'][:3000]}\n\n### Current resume JSON\n```json\n{json.dumps(slim, indent=1)}\n```\n"
        )
    return CLAUDE_PROMPT.format(n=len(items), rules=rules.strip()) + "\n" + "\n".join(parts), len(items)


def import_reply(folder: Path, reply_text: str, max_projects: int = 5) -> list[dict]:
    plan = parse_json(reply_text)
    results = []
    for item in plan.get("resumes", []) if isinstance(plan, dict) else []:
        jf = folder / f"{item.get('id', '')}.json"
        if not jf.exists():
            results.append({"id": item.get("id"), "status": "❌ id not found"})
            continue
        d = json.loads(jf.read_text())
        new_resume, report = apply_edits(d["resume"], item, max_projects)
        d["resume"] = new_resume
        d["claude_polish"] = {"notes": report["fit_notes"], "keywords_used": report["keywords_used"]}
        jf.write_text(json.dumps(d, indent=2))
        from .render import save_rendered
        save_rendered(jf.with_suffix(""), new_resume)
        results.append({"id": jf.stem, "status": "✅ updated", "bullets rewritten": report["rewritten"],
                        "kept by honesty check": report["reverted"], "notes": report["fit_notes"]})
    return results
