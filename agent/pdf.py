"""One-page resume PDF: large name, thick rule under it, contact line, black links."""
from __future__ import annotations

import io
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

FONT, BOLD, ITAL = "Times-Roman", "Times-Bold", "Times-Italic"


import re as _re

_URL = _re.compile(r"((?:https?://)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s)]*)?)", _re.I)
_EMAIL = _re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def _link(text: str) -> str:
    """Make the email or URL inside a contact item clickable, keeping any label like '(portfolio)'."""
    m = _EMAIL.search(text or "")
    if m:
        e = escape(m.group(0))
        return escape(text[:m.start()]) + f'<a href="mailto:{e}" color="{LINK_COLOR}">{e}</a>' + escape(text[m.end():])
    m = _URL.search(text or "")
    if m and not _re.fullmatch(r"[\d().\s+-]+", text or ""):
        u = m.group(0)
        href = u if u.lower().startswith("http") else "https://" + u
        return escape(text[:m.start()]) + f'<a href="{escape(href)}" color="{LINK_COLOR}">{escape(u)}</a>' + escape(text[m.end():])
    return escape(text)


LINK_COLOR = "#2F6DB5"
_MD_LINK = _re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


def _rich(text: str) -> str:
    """Escape text but keep inline [words](url) links clickable."""
    out, last = [], 0
    for m in _MD_LINK.finditer(text or ""):
        out.append(escape(text[last:m.start()]))
        url = m.group(2) if m.group(2).lower().startswith(("http", "mailto:")) else "https://" + m.group(2)
        out.append(f'<a href="{escape(url)}" color="{LINK_COLOR}">{escape(m.group(1))}</a>')
        last = m.end()
    out.append(escape((text or "")[last:]))
    return "".join(out)


def _lk(e: dict) -> str:
    return f"  |  {_link(e['link'])}" if e.get("link") else ""


def _styles(fs: float):
    return {
        "name": ParagraphStyle("name", fontName=BOLD, fontSize=fs * 2.1, leading=fs * 2.4, alignment=TA_CENTER),
        "contact": ParagraphStyle("contact", fontName=FONT, fontSize=fs * 0.95, leading=fs * 1.2, alignment=TA_CENTER),
        "section": ParagraphStyle("section", fontName=BOLD, fontSize=fs * 1.1, leading=fs * 1.3, spaceBefore=fs * 0.55),
        "left": ParagraphStyle("left", fontName=FONT, fontSize=fs, leading=fs * 1.18),
        "right": ParagraphStyle("right", fontName=FONT, fontSize=fs, leading=fs * 1.18, alignment=2),
        "bullet": ParagraphStyle("bullet", fontName=FONT, fontSize=fs, leading=fs * 1.18, leftIndent=12, bulletIndent=2),
    }


def _row(left: str, right: str, st, width, split: float = 0.72):
    t = Table([[Paragraph(left, st["left"]), Paragraph(right, st["right"])]], colWidths=[width * split, width * (1 - split)])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 0),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
    return t


def _story(r: dict, fs: float, width: float):
    st = _styles(fs)
    s = [Paragraph(escape(r.get("name", "")), st["name"]),
         HRFlowable(width="100%", thickness=1.6, color=colors.black, spaceBefore=1, spaceAfter=3),
         Paragraph("  |  ".join(_link(c) for c in r.get("contact", []) if c), st["contact"])]

    def section(title):
        s.append(Paragraph(title.upper(), st["section"]))
        s.append(HRFlowable(width="100%", thickness=0.5, color=colors.black, spaceBefore=1, spaceAfter=2))

    def bullets(items):
        for b in items:
            if b:
                s.append(Paragraph(_rich(b), st["bullet"], bulletText="•"))

    if r.get("education"):
        section("Education")
        for e in r["education"]:
            s.append(_row(f"<b>{escape(e.get('school',''))}</b>" + _lk(e), escape(e.get("location", "")), st, width))
            s.append(_row(f"<i>{escape(e.get('degree',''))}</i>", escape(e.get("dates", "")), st, width))
            bullets(e.get("details", []))
    if r.get("leadership"):
        section("Leadership")
        for e in r["leadership"]:
            s.append(_row(f"<b>{escape(e.get('org',''))}</b>" + _lk(e), escape(e.get("location", "")), st, width))
            s.append(_row(f"<i>{escape(e.get('title',''))}</i>", escape(e.get("dates", "")), st, width))
            bullets(e.get("bullets", []))
    if r.get("experience"):
        section("Experience")
        for e in r["experience"]:
            s.append(_row(f"<b>{escape(e.get('org',''))}</b>" + _lk(e), escape(e.get("location", "")), st, width))
            s.append(_row(f"<i>{escape(e.get('title',''))}</i>", escape(e.get("dates", "")), st, width))
            bullets(e.get("bullets", []))
            s.append(Spacer(1, fs * 0.25))
    if r.get("projects"):
        section("Projects")
        for p in r["projects"]:
            right = ""
            if p.get("link"):
                href = p["link"] if p["link"].startswith("http") else "https://" + p["link"]
                right = f'<a href="{escape(href)}" color="{LINK_COLOR}"><b>Project Website</b></a>'
            elif p.get("dates"):
                right = escape(p["dates"])
            s.append(_row(f"<b>{escape(p.get('name',''))}</b>", right, st, width, split=0.83))
            if p.get("subtitle"):
                s.append(Paragraph(f"<i>{escape(p['subtitle'])}</i>", st["left"]))
            bullets(p.get("bullets", []))
    if r.get("skills"):
        section("Skills")
        for k, v in r["skills"].items():
            if v:
                s.append(Paragraph(f"<b>{escape(k)}:</b> {escape(v)}", st["left"]))
    return s


def build_pdf(resume: dict, pages: int = 1) -> bytes:
    """Shrinks the font step by step until the resume fits on the chosen number of pages."""
    margin = 0.5 * inch
    width = LETTER[0] - 2 * margin
    data = b""
    sizes = (11.5, 11, 10.5, 10, 9.5, 9) if pages >= 2 else (10.5, 10, 9.5, 9, 8.75, 8.5)
    for fs in sizes:
        buf = io.BytesIO()
        pages_seen = []
        doc = SimpleDocTemplate(buf, pagesize=LETTER, leftMargin=margin, rightMargin=margin,
                                topMargin=0.45 * inch, bottomMargin=0.45 * inch, title=resume.get("name", "Resume"),
                                author=resume.get("name", ""))
        doc.build(_story(resume, fs, width), onFirstPage=lambda c, d: pages_seen.append(1), onLaterPages=lambda c, d: pages_seen.append(1))
        data = buf.getvalue()
        if len(pages_seen) <= pages:
            break
    return data
