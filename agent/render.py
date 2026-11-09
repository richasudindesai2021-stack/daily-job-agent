"""Turn a (tailored) resume into the file you send.

Two looks:
  clean    - the app's own design (supports picking different projects for each job)
  original - YOUR uploaded LaTeX or Word file, with only the bullet wording changed, so the format is exactly yours
"""
from __future__ import annotations

import io
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import store
from .pdf import build_pdf

SECTIONS = ("experience", "leadership", "projects", "education")


def original_path() -> Path | None:
    for ext in ("tex", "docx"):
        p = store.DATA / f"original_resume.{ext}"
        if p.exists():
            return p
    return None


def save_original(name: str, data: bytes):
    for ext in ("tex", "docx"):
        (store.DATA / f"original_resume.{ext}").unlink(missing_ok=True)
    ext = name.lower().rsplit(".", 1)[-1]
    if ext in ("tex", "docx"):
        store.DATA.mkdir(exist_ok=True)
        (store.DATA / f"original_resume.{ext}").write_bytes(data)


def page_count(pdf: bytes) -> int:
    from pypdf import PdfReader

    return len(PdfReader(io.BytesIO(pdf)).pages)


def _changes(base: dict, tailored: dict) -> list[tuple[str, str]]:
    """(old bullet, new bullet) pairs, matched by section and position."""
    out = []
    for sec in SECTIONS:
        key = "details" if sec == "education" else "bullets"
        b_items, t_items = base.get(sec) or [], tailored.get(sec) or []
        by_name = {(e.get("name") or e.get("org") or e.get("school") or "").lower(): e for e in t_items}
        for i, be in enumerate(b_items):
            name = (be.get("name") or be.get("org") or be.get("school") or "").lower()
            te = by_name.get(name) or (t_items[i] if i < len(t_items) else None)
            if not te:
                continue
            for old, new in zip(be.get(key) or [], te.get(key) or []):
                if old and new and old.strip() != new.strip():
                    out.append((old, new))
    return out


# ---------------- LaTeX ----------------
_TEX_ESC = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}",
            "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}


def tex_escape(s: str) -> str:
    return "".join(_TEX_ESC.get(c, c) for c in s)


def _tex_pattern(plain: str) -> re.Pattern:
    """Find a plain-text bullet inside LaTeX source, allowing escapes, line breaks and simple formatting commands."""
    parts = []
    for ch in plain.strip():
        if ch.isspace():
            parts.append(r"\s+")
        elif ch in _TEX_ESC:
            parts.append(r"\\?" + re.escape(ch))
        else:
            parts.append(re.escape(ch))
    # allow formatting like \textbf{ or } between characters
    glue = r"(?:\\href\{[^}]*\}\{|\\[a-zA-Z]+\{|\})*"
    return re.compile(glue.join(parts))


_MD = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


def _plain(s: str) -> str:
    return _MD.sub(r"\1", s or "")


def _tex_with_links(s: str) -> str:
    out, last = [], 0
    for m in _MD.finditer(s):
        out.append(tex_escape(s[last:m.start()]))
        url = m.group(2) if m.group(2).startswith("http") else "https://" + m.group(2)
        out.append(r"\href{" + url.replace("%", r"\%").replace("#", r"\#") + "}{" + tex_escape(m.group(1)) + "}")
        last = m.end()
    out.append(tex_escape(s[last:]))
    return "".join(out)


def render_tex(base: dict, tailored: dict, tex: str) -> tuple[str, int]:
    done = 0
    for old, new in _changes(base, tailored):
        po, pn = _plain(old), _plain(new)
        pat = _tex_pattern(po)
        m = pat.search(tex)
        if not m:
            continue
        # keep the unchanged start of the line exactly as written (bold labels like "Leadership:", links, etc.)
        ow, nw = po.split(" "), pn.split(" ")
        k = 0
        while k < min(len(ow), len(nw)) - 1 and ow[k] == nw[k]:
            k += 1
        start, new_txt = m.start(), new
        if k:
            mp = _tex_pattern(" ".join(ow[:k])).match(tex, m.start())
            if mp and mp.end() <= m.end():
                start = mp.end()
                new_txt = " " + " ".join(nw[k:])
                while start < m.end() and tex[start] == "}":  # a label's closing brace stays where it was
                    start += 1
                while start < m.end() and tex[start] == " ":
                    start += 1
                new_txt = new_txt.lstrip() if tex[start - 1] == " " else new_txt
        seg = tex[start:m.end()]
        # formatting groups opened inside the old text (e.g. \href{..}{ or \textbf{) close right after it: take those too
        unclosed = len(re.findall(r"(?<!\\)\{", seg)) - len(re.findall(r"(?<!\\)\}", seg))
        end = m.end()
        while unclosed > 0 and end < len(tex) and tex[end] == "}":
            end += 1
            unclosed -= 1
        lead = "}" * max(0, -unclosed)  # never drop a brace that closes something opened before
        tex = tex[:start] + lead + _tex_with_links(new_txt) + tex[end:]
        done += 1
    return tex, done


_BULLET_LINE = re.compile(r"^(\s*)\\(resumeItem|item)\b\s*(\{)?")
_SECTION_END = re.compile(r"\\section\*?\{|\\resumeSubHeadingListEnd|\\end\{document\}")


def _entry_name(e: dict) -> str:
    return e.get("name") or e.get("org") or ""


def select_entries_tex(tex: str, base_list: list[dict], chosen: list[dict]) -> str:
    """Keep only the chosen entries (projects or experiences), in the chosen order. Entries that aren't in the file
    (from the project/experience bank) are added by copying the format of the first entry. If the file's layout
    can't be read safely, the file is left as it was."""
    if not base_list or not chosen:
        return tex
    base_names = [_entry_name(e) for e in base_list]
    if [_entry_name(c) for c in chosen] == base_names:
        return tex
    lines = tex.split("\n")
    joined_starts, pos = [], 0
    for ln in lines:
        joined_starts.append(pos)
        pos += len(ln) + 1

    def line_of(char_pos):
        lo, hi = 0, len(joined_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if joined_starts[mid] <= char_pos:
                lo = mid
            else:
                hi = mid - 1
        return lo

    starts, search_from = [], 0
    for nm in base_names:
        if not nm:
            return tex
        m = _tex_pattern(_plain(nm)).search(tex, search_from)
        if not m:
            return tex
        starts.append(line_of(m.start()))
        search_from = m.end()
    if starts != sorted(starts) or len(set(starts)) != len(starts):
        return tex
    end = len(lines)
    for k in range(starts[-1] + 1, len(lines)):
        if _SECTION_END.search(lines[k]):
            end = k
            break
    blocks = {base_names[i]: lines[starts[i]:(starts[i + 1] if i + 1 < len(starts) else end)] for i in range(len(starts))}
    template, t_entry = blocks[base_names[0]], base_list[0]
    out = []
    for c in chosen:
        nm = _entry_name(c)
        if nm in blocks:
            out += blocks[nm]
            continue
        blk = "\n".join(template)
        # swap the template's own fields for the new entry's (name, then subtitle/title, dates, link)
        for field in ("name", "org", "title", "subtitle", "dates", "location"):
            old, new = t_entry.get(field) or "", c.get(field) or ""
            if old:
                m = _tex_pattern(_plain(old)).search(blk)
                if m:
                    blk = blk[:m.start()] + tex_escape(_plain(new)) + blk[m.end():]
        if c.get("link"):
            blk = re.sub(r"\\href\{[^}]*\}", lambda _m: "\\href{" + (c["link"] if c["link"].startswith("http") else "https://" + c["link"]) + "}", blk, count=1)
        bl = blk.split("\n")
        idx = [k for k, l in enumerate(bl) if re.match(r"\s*\\resumeItem\b", l)]
        if not idx:  # plain \item bullets: never the entry's own first (heading) line
            idx = [k for k, l in enumerate(bl) if k > 0 and _BULLET_LINE.match(l)]
        if idx:
            m = _BULLET_LINE.match(bl[idx[0]])
            indent, cmd, brace = m.group(1), m.group(2), m.group(3)
            new_lines = [f"{indent}\\{cmd}" + ("{" + _tex_with_links(b) + "}" if brace else " " + _tex_with_links(b))
                         for b in c.get("bullets", [])]
            bl = bl[:idx[0]] + new_lines + bl[idx[-1] + 1:]
        out += bl
    return "\n".join(lines[:starts[0]] + out + lines[end:])


import os as _os
import threading as _threading

_TEX_SLOTS = _threading.BoundedSemaphore(max(2, min(8, (_os.cpu_count() or 4))))
_FIT_LEVEL = {"level": -1}  # the spacing level that made the last resume fit: try it first next time
_WORD_LOCK = _threading.Lock()  # Word can only convert one file at a time


def compile_tex(tex: str) -> bytes | None:
    exe = shutil.which("pdflatex") or shutil.which("xelatex")
    if not exe:
        return None
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "resume.tex"
        src.write_text(tex, encoding="utf-8")
        with _TEX_SLOTS:  # a few at a time: more just makes them all slower
            subprocess.run([exe, "-interaction=nonstopmode", "-halt-on-error", "resume.tex"], cwd=d,
                           capture_output=True, timeout=120)  # one run is enough for a resume (no cross-references)
        pdf = Path(d) / "resume.pdf"
        return pdf.read_bytes() if pdf.exists() else None


TEX_TIGHTEN = [  # gentle first: line spacing, then margins, then a slightly smaller font
    ("\\linespread{0.94}\\selectfont", None),
    ("\\linespread{0.92}\\selectfont", "left=0.5in,right=0.5in,top=0.4in,bottom=0.4in"),
    ("\\linespread{0.9}\\selectfont\\small", "left=0.45in,right=0.45in,top=0.35in,bottom=0.35in"),
    ("\\linespread{0.88}\\selectfont\\small", "left=0.4in,right=0.4in,top=0.3in,bottom=0.3in"),
]


def _tighten_tex(tex: str, level: int) -> str:
    body_cmd, margins = TEX_TIGHTEN[level]
    if margins:
        if re.search(r"\\usepackage(\[[^\]]*\])?\{geometry\}", tex):
            body_cmd = f"\\newgeometry{{{margins}}}" + body_cmd
        else:
            tex = tex.replace("\\begin{document}", f"\\usepackage[{margins}]{{geometry}}\n\\begin{{document}}", 1)
    # tighter lists too, without needing any package
    body_cmd += "\\setlength{\\parskip}{0pt}"
    return tex.replace("\\begin{document}", "\\begin{document}\n" + body_cmd + "\n", 1)


def fit_tex(tex: str, pages: int) -> tuple[bytes | None, str, str]:
    """Compile; if it runs over the page limit, tighten spacing step by step until it fits. (pdf, tex used, note)"""
    lv = _FIT_LEVEL["level"]
    if lv >= 0:  # resumes in one list are alike: start at the spacing that worked last time
        t2 = _tighten_tex(tex, lv)
        p2 = compile_tex(t2)
        if p2 and page_count(p2) <= pages:
            return p2, t2, " Spacing tightened slightly to fit " + ("1 page." if pages == 1 else f"{pages} pages.")
    pdf = compile_tex(tex)
    if not pdf or page_count(pdf) <= pages:
        _FIT_LEVEL["level"] = -1
        return pdf, tex, ""
    for level in range(max(lv + 1, 0), len(TEX_TIGHTEN)):
        t2 = _tighten_tex(tex, level)
        p2 = compile_tex(t2)
        if p2 and page_count(p2) <= pages:
            _FIT_LEVEL["level"] = level
            return p2, t2, " Spacing tightened slightly to fit " + ("1 page." if pages == 1 else f"{pages} pages.")
    # last resort, strict page limit: drop the final one-line bullets (usually the least important project lines)
    tight = _tighten_tex(tex, len(TEX_TIGHTEN) - 1)
    dropped = 0
    for _ in range(6):
        lines = tight.split("\n")
        idx = [i for i, l in enumerate(lines) if re.match(r"\s*\\(item|resumeItem)\b", l) and l.count("{") == l.count("}")]
        if not idx:
            break
        del lines[idx[-1]]
        tight = "\n".join(lines)
        dropped += 1
        p2 = compile_tex(tight)
        if p2 and page_count(p2) <= pages:
            return p2, tight, (f" Tightened spacing and left out the last {dropped} bullet(s) to keep it to "
                               + ("1 page." if pages == 1 else f"{pages} pages."))
    return pdf, tex, f" Still {page_count(pdf)} pages; cut a bullet in Resume chat to fit."


# ---------------- Word ----------------
def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def render_docx(base: dict, tailored: dict, data: bytes) -> tuple[bytes, int]:
    import docx

    d = docx.Document(io.BytesIO(data))
    changes = {_norm(_plain(o)): _plain(n) for o, n in _changes(base, tailored)}
    done = 0
    paragraphs = list(d.paragraphs)
    for t in d.tables:
        for row in t.rows:
            for cell in row.cells:
                paragraphs += cell.paragraphs
    for p in paragraphs:
        key = _norm(p.text)
        if key in changes and p.runs:
            new = changes.pop(key)
            p.runs[0].text = new  # keeps the first run's font, size and style
            for r in p.runs[1:]:
                r.text = ""
            done += 1
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue(), done


def _tighten_docx(data: bytes, level: int) -> bytes:
    """Smaller gaps between lines, slimmer margins, then half a point smaller text."""
    import docx
    from docx.shared import Inches, Pt

    d = docx.Document(io.BytesIO(data))
    margin = {1: 0.55, 2: 0.45, 3: 0.4}[level]
    for sec in d.sections:
        sec.left_margin = sec.right_margin = Inches(margin)
        sec.top_margin = sec.bottom_margin = Inches(margin - 0.1)
    for para in d.paragraphs + [p for t in d.tables for r in t.rows for c in r.cells for p in c.paragraphs]:
        pf = para.paragraph_format
        pf.space_before = Pt(0)
        pf.space_after = Pt(1 if level < 3 else 0)
        pf.line_spacing = {1: 1.0, 2: 0.97, 3: 0.95}[level]
        if level == 3:
            for r in para.runs:
                if r.font.size:
                    r.font.size = Pt(max(r.font.size.pt - 0.5, 8))
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def docx_to_pdf(data: bytes) -> bytes | None:
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "resume.docx"
        src.write_bytes(data)
        try:  # Microsoft Word (Windows / Mac)
            from docx2pdf import convert

            with _WORD_LOCK:
                convert(str(src), str(Path(d) / "resume.pdf"))
        except Exception:
            office = shutil.which("soffice") or shutil.which("libreoffice")
            if office:
                subprocess.run([office, "--headless", "--convert-to", "pdf", "--outdir", d, str(src)],
                               capture_output=True, timeout=180)
        pdf = Path(d) / "resume.pdf"
        return pdf.read_bytes() if pdf.exists() else None


# ---------------- entry point ----------------
def render(tailored: dict, settings: dict, base: dict | None = None) -> dict:
    """Returns {"pdf": bytes|None, "source": (bytes, ext)|None, "pages": int|None, "note": str}."""
    pages = int(settings.get("resume_pages", 1))
    orig = original_path() if settings.get("resume_format") == "original" else None
    if orig is None:
        pdf = build_pdf(tailored, pages=pages)
        return {"pdf": pdf, "source": None, "pages": page_count(pdf), "note": ""}
    base = base or store.load_resume() or {}
    if orig.suffix == ".tex":
        tex0 = orig.read_text(encoding="utf-8", errors="ignore")
        for sec in ("projects", "experience"):  # keep the best N entries, add bank entries in the file's own style
            tex0 = select_entries_tex(tex0, (store.load_resume() or {}).get(sec, []), tailored.get(sec, []))
        tex, n = render_tex(base, tailored, tex0)
        pdf, tex, fit_note = fit_tex(tex, pages)
        note = f"{n} bullet(s) updated in your LaTeX file." + fit_note
        if pdf is None:
            note += " No LaTeX installed on this computer, so you get the .tex file (compile it in Overleaf or install MiKTeX)."
        return {"pdf": pdf, "source": (tex.encode("utf-8"), "tex"), "pages": page_count(pdf) if pdf else None, "note": note}
    out, n = render_docx(base, tailored, orig.read_bytes())
    pdf = docx_to_pdf(out)
    note = f"{n} bullet(s) updated in your Word file."
    if pdf and page_count(pdf) > pages:
        for level in (1, 2, 3):
            out2 = _tighten_docx(out, level)
            p2 = docx_to_pdf(out2)
            if p2 and page_count(p2) <= pages:
                out, pdf = out2, p2
                note += " Spacing tightened slightly to fit."
                break
    if pdf is None:
        note += " Couldn't convert to PDF on this computer (needs Microsoft Word or LibreOffice), so you get the .docx."
    return {"pdf": pdf, "source": (out, "docx"), "pages": page_count(pdf) if pdf else None, "note": note}


MIME = {"pdf": "application/pdf", "tex": "application/x-tex",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


def save_rendered(stem: Path, tailored: dict, settings: dict | None = None, base: dict | None = None) -> tuple[Path, dict]:
    """Write the resume next to `stem` (no extension). Returns (main file to send, render info)."""
    settings = settings or store.load_settings()
    out = render(tailored, settings, base)
    for ext in ("pdf", "tex", "docx"):
        stem.with_suffix("." + ext).unlink(missing_ok=True)
    if out["source"]:
        stem.with_suffix("." + out["source"][1]).write_bytes(out["source"][0])
    if out["pdf"]:
        main = stem.with_suffix(".pdf")
        main.write_bytes(out["pdf"])
    else:
        main = stem.with_suffix("." + out["source"][1])
    return main, out


def find_file(path_like: str | Path) -> Path | None:
    """The resume file for a list row, whatever its extension."""
    p = Path(path_like)
    for ext in (".pdf", ".docx", ".tex"):
        q = p.with_suffix(ext)
        if q.exists():
            return q
    return None
