"""Files: settings, resume, company list, tracker.

Local mode: everything lives in this folder on your laptop.
Hosted mode: each visitor gets a private workspace folder, set per session with use_workspace(),
so one person's resume and tracker are never visible to anyone else.
"""
from __future__ import annotations

import copy
import io
import json
import shutil
import zipfile
from contextvars import ContextVar
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
STARTER = ROOT / "data" / "starter_companies.csv"  # shared starting list; each person's own list is data/companies.csv
_workspace: ContextVar[Path] = ContextVar("workspace", default=ROOT)
_FILES = {"SETTINGS": "settings.json", "RESUME": "resume.json", "COMPANIES": "companies.csv",
          "TRACKER": "tracker.csv", "H1B": "h1b_employers.csv", "PROJECT_BANK": "project_bank.json",
          "EXP_BANK": "experience_bank.json"}


def use_workspace(path: Path):
    """Point every read/write in this session at `path` (seeded with the default company list)."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "data").mkdir(exist_ok=True)
    if not (path / "data" / "companies.csv").exists():
        shutil.copy(STARTER, path / "data" / "companies.csv")
    _workspace.set(path)


def __getattr__(name):  # store.DATA, store.OUTPUT, store.RESUME ... resolve to the current workspace
    ws = _workspace.get()
    if name == "DATA":
        return ws / "data"
    if name == "OUTPUT":
        return ws / "output"
    if name in _FILES:
        return ws / "data" / _FILES[name]
    raise AttributeError(name)


def _p(name: str) -> Path:
    return __getattr__(name)


def export_profile() -> bytes:
    """Zip of this workspace's settings, resume, company list, tracker and outputs."""
    ws, buf = _workspace.get(), io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        files = list((ws / "data").glob("*")) + (list((ws / "output").rglob("*")) if (ws / "output").exists() else [])
        for f in files:
            if f.is_file():
                z.write(f, f.relative_to(ws))
    return buf.getvalue()


def import_profile(data: bytes):
    ws = _workspace.get()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for m in z.namelist():
            target = (ws / m).resolve()
            if not str(target).startswith(str(ws.resolve())) or not (m.startswith("data/") or m.startswith("output/")):
                continue  # ignore anything outside the workspace
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(m))

DEFAULT_SETTINGS = {
    "roles": ["Associate Product Manager", "Product Manager", "Product Analyst", "Business Analyst",
              "Data Analyst", "Data Scientist", "Strategy Analyst", "Consultant", "Business Intelligence"],
    "exclude_title_words": ["senior", "sr.", "sr ", "staff", "principal", "director", "head of", "vp",
                            "vice president", "lead", "manager ii", "manager iii", "phd", "intern"],
    "locations": ["California", "CA", "San Francisco", "Los Angeles", "San Diego", "San Jose", "Palo Alto",
                  "Mountain View", "Menlo Park", "Bay Area", "Seattle", "WA", "New York", "NY", "NYC"],
    "allow_remote": False,
    "visa_status": "F-1 OPT / STEM OPT (will need H-1B later)",
    "needs_sponsorship": True,
    "industries": [],
    "exclude_industries": ["oil", "real estate"],
    "size_mix": {"big": 10, "mid": 10, "small": 10},
    "daily_count": 30,
    "min_salary": 90000,
    "max_age_hours": 336,
    "max_experience": 0,  # 0 = as many as on the uploaded resume
    "web_fill": True,  # fill short size groups with Gemini + Google Search (checked links only)
    "max_projects": 3,
    "resume_rules": (
        "- Start every bullet with a strong past-tense verb; never deliver/support/assist/help/worked on.\n"
        "- Lead with the result, then how. About 1.5 lines per bullet.\n"
        "- Use the posting's exact language where the experience truthfully matches.\n"
        "- Don't repeat the same keyword across bullets.\n"
        "- Keep it to one page: max 4 experiences, 2-3 bullets per project.\n"
        "- No numbers I couldn't defend in an interview."
    ),
    "ntfy_topic": "",
    "targets_saved": False,
    "resume_format": "clean",
    "resume_pages": 1,
    "companies_ok": False,
    "skip_projects": False,
    "alerts_done": False,
    "daily_email": False,
    "daily_email_time": "",
    "daily_email_tz": "",
    "daily_email_to": "",
    "daily_email_attach_pdfs": True,
    "cloud_enabled": False,
    "cloud_repo": "",
    "provider": "Google Gemini (free)",
    "model": "",
}


def _read_json(p: Path, default):
    try:
        return json.loads(p.read_text())
    except Exception:
        return copy.deepcopy(default)


def load_settings() -> dict:
    s = copy.deepcopy(DEFAULT_SETTINGS)
    s.update(_read_json(_p("SETTINGS"), {}))
    return s


def save_settings(s: dict):
    _p("DATA").mkdir(exist_ok=True)
    _p("SETTINGS").write_text(json.dumps(s, indent=2))


def load_resume() -> dict | None:
    return _read_json(_p("RESUME"), None)


def save_resume(r: dict):
    _p("DATA").mkdir(exist_ok=True)
    _p("RESUME").write_text(json.dumps(r, indent=2))


def load_project_bank() -> list[dict]:
    """All your projects (with links). The agent picks the best few for each job."""
    data = _read_json(_p("PROJECT_BANK"), [])
    if isinstance(data, dict):
        data = data.get("projects", [])
    return [p for p in data if isinstance(p, dict) and p.get("name")]


def save_project_bank(projects: list[dict]):
    _p("DATA").mkdir(exist_ok=True)
    _p("PROJECT_BANK").write_text(json.dumps(projects, indent=2))


def load_experience_bank() -> list[dict]:
    """Extra roles (jobs, internships, research...) beyond the resume. The agent picks the best ones per job."""
    data = _read_json(_p("EXP_BANK"), [])
    return [e for e in data if isinstance(e, dict) and (e.get("org") or e.get("title"))]


def save_experience_bank(items: list[dict]):
    _p("DATA").mkdir(exist_ok=True)
    _p("EXP_BANK").write_text(json.dumps(items, indent=2))


def load_companies() -> pd.DataFrame:
    if not _p("COMPANIES").exists():
        _p("DATA").mkdir(exist_ok=True)
        shutil.copy(STARTER, _p("COMPANIES"))
    df = pd.read_csv(_p("COMPANIES"), dtype=str).fillna("")
    return df[df["company"].str.strip() != ""]


def save_companies(df: pd.DataFrame):
    df.to_csv(_p("COMPANIES"), index=False)


TRACKER_COLS = ["date", "company", "size", "title", "location", "url", "score", "status", "pdf"]


def load_tracker() -> pd.DataFrame:
    if _p("TRACKER").exists():
        return pd.read_csv(_p("TRACKER"), dtype=str).fillna("")
    return pd.DataFrame(columns=TRACKER_COLS)


def save_tracker(df: pd.DataFrame):
    _p("DATA").mkdir(exist_ok=True)
    df.to_csv(_p("TRACKER"), index=False)
