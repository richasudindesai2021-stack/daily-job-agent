"""Resume chat: talk to the AI to edit a resume. Same honesty guardrail as auto-tailoring,
except numbers you type yourself in the chat are allowed."""
from __future__ import annotations

import copy
import json
import re

CHAT_SYSTEM = """You are a sharp resume editor for an early-career candidate. You edit the resume JSON you are given,
following the user's request. Rules:
- Never invent experience, tools, employers, titles, outcomes or numbers. Use only facts in the resume or facts the user states in this chat.
- Start bullets with strong past-tense verbs; never use weak verbs (delivered, supported, assisted, helped, worked on, responsible for).
- Keep bullets about 20-30 words. Don't repeat the same keyword across bullets. Keep it one page: max 4 experiences.
- If a job posting is given, mirror its exact keywords only where the resume truthfully supports them.
- If the user only asks a question (fit, feedback, what to change), answer it and do not edit.
- Keep the same JSON schema and keys. Return the WHOLE resume when you edit.
- Keep inline links written as [words](url) and every "link" field unless the user asks to change them.
Return JSON: {"reply": str (short, plain, friendly), "updated_resume": object or null, "changes": [str] (one line per change)}"""

_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def _nums(s: str) -> set[str]:
    return {n.replace(",", "") for n in _NUM.findall(s or "")}


def _guard(original: dict, updated: dict, allowed_text: str) -> tuple[dict, list[str]]:
    """Revert any bullet whose numbers appear nowhere in the original resume or the user's own messages."""
    allowed = _nums(json.dumps(original)) | _nums(allowed_text)
    notes = []
    out = copy.deepcopy(updated)
    for sec in ("experience", "leadership", "projects", "education"):
        key = "details" if sec == "education" else "bullets"
        for i, item in enumerate(out.get(sec) or []):
            if not isinstance(item, dict):
                continue
            orig_items = (original.get(sec) or [])
            orig_bullets = orig_items[i].get(key, []) if i < len(orig_items) and isinstance(orig_items[i], dict) else []
            fixed = []
            for b_i, b in enumerate(item.get(key) or []):
                bad = _nums(b) - allowed
                if bad:
                    notes.append(f"Kept original wording in {sec} #{i + 1}: the AI added a number ({', '.join(sorted(bad))}) you never gave.")
                    if b_i < len(orig_bullets):
                        fixed.append(orig_bullets[b_i])
                    continue
                fixed.append(b)
            item[key] = fixed
    return out, notes


def chat_edit(llm, resume: dict, history: list[dict], message: str, job: dict | None = None, rules: str = ""):
    """Returns (reply, proposed_resume_or_None, changes, guardrail_notes)."""
    convo = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in history[-8:])
    job_txt = ""
    if job:
        job_txt = f"\nTARGET JOB: {job.get('title')} at {job.get('company')}\nPOSTING:\n{job.get('description', '')[:5000]}\n"
    user = f"RESUME JSON:\n{json.dumps(resume)}\n{job_txt}\nCONVERSATION SO FAR:\n{convo}\n\nUSER: {message}"
    system = CHAT_SYSTEM + (f"\nThe candidate's own resume rules (follow them too):\n{rules}" if rules else "")
    out = llm.chat_json(system, user, temperature=0.4)
    reply = out.get("reply", "")
    proposed = out.get("updated_resume")
    if not isinstance(proposed, dict) or not proposed:
        return reply, None, [], []
    merged = copy.deepcopy(resume)
    merged.update({k: v for k, v in proposed.items() if k in resume})
    user_text = " ".join(m["content"] for m in history if m["role"] == "user") + " " + message
    guarded, notes = _guard(resume, merged, user_text)
    return reply, guarded, out.get("changes", []), notes
