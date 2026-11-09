"""Build a ranked list of companies that sponsor H-1B for entry-level roles like yours,
from the U.S. Department of Labor's LCA disclosure files (free, official, public).

Download the files at https://www.dol.gov/agencies/eta/foreign-labor/performance
(section "LCA Programs (H-1B, H-1B1, E-3)", the quarterly .xlsx files). One year = 4 files.
"""
from __future__ import annotations

import re
from collections import defaultdict

# Occupations that cover analyst / product / data / strategy / consulting / project roles
SOC_PREFIXES = (
    "13-1111",  # management analysts (consulting, strategy)
    "13-1161",  # market research analysts
    "13-1082",  # project management specialists
    "13-1199",  # business operations specialists
    "13-2051",  # financial and investment analysts
    "15-2051",  # data scientists
    "15-2041",  # statisticians
    "15-2031",  # operations research analysts
    "15-1211",  # computer systems analysts (business systems / BI)
    "15-1299",  # computer occupations, other (often product / BI analyst)
    "11-2021",  # marketing managers (product marketing)
    "11-3021",  # computer & information systems managers (product / program managers)
    "11-9199",  # managers, all other (often product managers)
)
TITLE_WORDS = ("analyst", "product", "data scien", "strategy", "consultant", "business intelligence",
               "program manager", "project manager", "insights", "operations", "associate")
SENIOR_WORDS = ("senior", "sr.", "sr ", "staff", "principal", "director", "lead", "head", "vp", "vice president",
                "chief", "manager ii", "manager iii", "iii", " iv")

# Outsourcing / staffing firms file huge numbers of LCAs but rarely hire for these roles directly.
STAFFING_HINTS = (
    "infosys", "cognizant", "tata consultancy", "wipro", "hcl america", "tech mahindra", "capgemini", "ltimindtree",
    "mphasis", "hexaware", "compunnel", "mastech", "igate", "syntel", "virtusa", "larsen", "l&t", "ust global",
    "zensar", "birlasoft", "sonata", "persistent systems", "cyient", "kforce", "insight global", "randstad",
    "teksystems", "apex systems", "collabera", "diverse lynx", "ampcus", "staffing", "consultancy services",
    "infotech", "it solutions", "software solutions", "technologies pvt", "systems inc", "global solutions",
)
ACADEMIC_HINTS = ("university", "college", "school of", "institute of technology", "hospital", "medical center",
                  "research foundation", "regents")

NAICS_INDUSTRY = [  # most specific first
    ("211", "oil & gas"), ("213", "oil & gas"), ("324", "oil & gas"), ("531", "real estate"),
    ("3254", "healthcare; pharma & biotech"), ("3391", "healthcare; medical devices"), ("62", "healthcare"),
    ("5417", "healthcare; pharma & biotech"), ("524", "insurance"), ("52", "finance & fintech"),
    ("315", "fashion & retail"), ("316", "fashion & retail"), ("4481", "fashion & retail"), ("4482", "fashion & retail"),
    ("455", "retail"), ("44", "retail"), ("45", "retail"), ("4541", "retail; e-commerce"),
    ("5112", "software"), ("5132", "software"), ("518", "software; data & AI"), ("519", "consumer tech; internet"),
    ("5415", "IT services"), ("5416", "consulting"), ("5418", "marketing & media"), ("512", "media & entertainment"),
    ("515", "media & entertainment"), ("516", "media & entertainment"), ("711", "media & entertainment"),
    ("334", "hardware & semiconductors"), ("336", "automotive & aerospace"), ("481", "travel & transport"),
    ("721", "travel & hospitality"), ("722", "restaurants"), ("61", "education"), ("22", "energy & utilities"),
    ("31", "consumer goods"), ("32", "manufacturing"), ("33", "manufacturing"), ("42", "wholesale & distribution"),
    ("48", "logistics"), ("49", "logistics"), ("54", "professional services"),
]


def naics_to_industry(code: str) -> str:
    c = re.sub(r"\D", "", str(code or ""))
    for prefix, label in NAICS_INDUSTRY:
        if c.startswith(prefix):
            return label
    return "other"


# Role families: used to match companies to what each person is looking for
ROLE_FAMILIES = {
    "product": ("product manager", "product owner", "product analyst", "product strategy", "associate product", "product operations"),
    "analyst": ("business analyst", "data analyst", "business intelligence", "bi analyst", "analytics", "insights", "reporting analyst"),
    "data": ("data scientist", "data science", "machine learning", "statistician", "quantitative", "research scientist"),
    "consulting": ("consultant", "consulting", "strategy", "strategic", "management analyst", "advisory"),
    "project": ("project manager", "program manager", "project coordinator", "program coordinator", "operations manager",
                "operations analyst", "business operations"),
    "finance": ("financial analyst", "finance", "investment", "fp&a", "credit", "risk analyst", "accounting"),
    "marketing": ("marketing", "growth", "brand", "market research", "merchandis"),
    "engineering": ("software engineer", "developer", "data engineer", "engineer"),
}


def families_for(text: str) -> list[str]:
    t = (text or "").lower()
    return [f for f, words in ROLE_FAMILIES.items() if any(w in t for w in words)]


COLS = {
    "status": ("CASE_STATUS",), "visa": ("VISA_CLASS",), "employer": ("EMPLOYER_NAME",),
    "title": ("JOB_TITLE",), "soc": ("SOC_CODE",), "state": ("WORKSITE_STATE", "WORKSITE_STATE_1"),
    "level": ("PW_WAGE_LEVEL", "PW_WAGE_LEVEL_1", "PW_LEVEL"), "naics": ("NAICS_CODE",),
    "city": ("WORKSITE_CITY", "WORKSITE_CITY_1"),
    "case": ("CASE_NUMBER",),
}


def _rows(path: str):
    """Stream rows as dicts from a DOL .xlsx or .csv without loading the whole file into memory."""
    if path.lower().endswith((".xlsx", ".xlsm")):
        try:  # fast reader (the yearly DOL file is ~250 MB)
            from python_calamine import CalamineWorkbook

            it = CalamineWorkbook.from_path(path).get_sheet_by_index(0).iter_rows()
            header = [str(h or "").strip().upper() for h in next(it)]
            for r in it:
                yield dict(zip(header, r))
            return
        except ImportError:
            pass
        from openpyxl import load_workbook

        wb = load_workbook(path, read_only=True, data_only=True)
        ws = wb.worksheets[0]
        it = ws.iter_rows(values_only=True)
        header = [str(h or "").strip().upper() for h in next(it)]
        for r in it:
            yield dict(zip(header, r))
        wb.close()
    else:
        import csv

        with open(path, newline="", encoding="utf-8", errors="ignore") as f:
            for r in csv.DictReader(f):
                yield {k.strip().upper(): v for k, v in r.items() if k}


def _get(row: dict, key: str):
    for c in COLS[key]:
        if c in row and row[c] not in (None, ""):
            return str(row[c]).strip()
    return ""


def clean_name(name: str) -> str:
    n = re.sub(r"\s+", " ", name or "").strip()
    suffix = r"(inc|llc|l\.l\.c|llp|l\.l\.p|corp|corporation|co|company|ltd|limited|lp|l\.p|plc|pbc|na|n\.a|us|usa|u\.s)"
    for _ in range(3):  # "Goldman Sachs & Co. LLC" -> "Goldman Sachs"
        n2 = re.sub(r"[,.]?\s+" + suffix + r"\.?$", "", n, flags=re.I)
        n2 = re.sub(r"\s*(&|and)$", "", n2, flags=re.I).strip(" ,.")
        if n2 == n:
            break
        n = n2
    return n.title() if n.isupper() else n


def build_sponsor_list(paths: list[str], states=("CA", "WA", "NY"), max_companies: int = 3000,
                       exclude_staffing: bool = True, exclude_academic: bool = True, progress=None) -> list[dict]:
    states = {s.upper() for s in states}
    total = defaultdict(int)          # all certified H-1B LCAs (proxy for sponsorship scale)
    entry = defaultdict(int)          # entry-level, relevant role, in your states
    titles = defaultdict(lambda: defaultdict(int))
    cities = defaultdict(lambda: defaultdict(int))
    states_ct = defaultdict(lambda: defaultdict(int))
    naics = defaultdict(lambda: defaultdict(int))
    fams = defaultdict(lambda: defaultdict(int))
    display = {}
    n = 0
    seen_cases = set()  # the same filing can appear in more than one quarterly file
    for p in paths:
        for row in _rows(p):
            n += 1
            case = _get(row, "case")
            if case:
                if case in seen_cases:
                    continue
                seen_cases.add(case)
            if progress and n % 50000 == 0:
                progress(None, f"Read {n:,} filings...")
            if "CERTIFIED" not in _get(row, "status").upper() or "WITHDRAWN" in _get(row, "status").upper():
                continue
            if _get(row, "visa").upper() not in ("", "H-1B"):
                continue
            emp_raw = _get(row, "employer")
            if not emp_raw:
                continue
            key = re.sub(r"[^a-z0-9 ]", "", emp_raw.lower())
            key = re.sub(r"\b(inc|llc|corp|corporation|co|company|ltd|limited|lp|plc|na)\b", "", key)
            key = re.sub(r"\s+", " ", key).strip()
            display.setdefault(key, clean_name(emp_raw))
            total[key] += 1
            naics[key][_get(row, "naics")[:4]] += 1
            title = _get(row, "title").lower()
            soc = _get(row, "soc")
            level = _get(row, "level").upper().replace("LEVEL", "").strip()
            if level not in ("I", "II", "1", "2"):
                continue
            if any(w in f" {title} " for w in SENIOR_WORDS):
                continue
            if not (soc.startswith(SOC_PREFIXES) or any(w in title for w in TITLE_WORDS)):
                continue
            if states and _get(row, "state").upper() not in states:
                continue
            entry[key] += 1
            for f in families_for(title):
                fams[key][f] += 1
            titles[key][title.title()] += 1
            st_code = _get(row, "state").upper()
            cities[key][f"{_get(row, 'city').title()}, {st_code}"] += 1
            states_ct[key][st_code] += 1

    out = []
    for key, cnt in entry.items():
        name = display[key]
        low = name.lower()
        if exclude_staffing and any(h in low for h in STAFFING_HINTS):
            continue
        if exclude_academic and any(h in low for h in ACADEMIC_HINTS):
            continue
        t = total[key]
        out.append({
            "company": name,
            "size": "big" if t >= 200 else ("mid" if t >= 20 else "small"),
            "industry": naics_to_industry(max(naics[key].items(), key=lambda x: x[1])[0] if naics[key] else ""),
            "entry_level_filings": cnt,
            "all_h1b_filings": t,
            "top_titles": "; ".join(k for k, _ in sorted(titles[key].items(), key=lambda x: -x[1])[:3]),
            "top_cities": "; ".join(k for k, _ in sorted(cities[key].items(), key=lambda x: -x[1])[:3]),
            # where they hire: every state, and their top 25 cities, with counts (for the location filter)
            "states": ";".join(f"{k}:{v}" for k, v in sorted(states_ct[key].items(), key=lambda x: -x[1]) if k),
            "cities": ";".join(f"{k}:{v}" for k, v in sorted(cities[key].items(), key=lambda x: -x[1])[:25]),
            **{f"fam_{f}": fams[key].get(f, 0) for f in ROLE_FAMILIES},
        })
    out.sort(key=lambda r: (-r["entry_level_filings"], -r["all_h1b_filings"]))
    return out[:max_companies]
