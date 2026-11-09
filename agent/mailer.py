"""Sends email through the website's own sender (a free Google Apps Script, see email_relay.gs),
so people only type where they want their list. A personal Gmail + app password still works as a fallback."""
from __future__ import annotations

import base64
import smtplib
from email.message import EmailMessage
from html import escape
from pathlib import Path

from .llm import local_secret


def gmail_creds() -> tuple[str, str]:
    return local_secret("GMAIL_USER"), local_secret("GMAIL_APP_PASSWORD").replace(" ", "")


def relay_config() -> tuple[str, str]:
    return local_secret("RELAY_URL").strip(), local_secret("RELAY_KEY").strip()


def email_ready() -> bool:
    url, key = relay_config()
    user, pw = gmail_creds()
    return bool(url and key) or bool(user and pw)


def relay_script(key: str) -> str:
    return (Path(__file__).with_name("email_relay.gs")).read_text().replace("__KEY__", key)


def _send_relay(url, key, subject, text, html, attachments, recipients):
    import requests

    files, total = [], 0
    for p in attachments or []:
        data = p.read_bytes()
        total += len(data)
        if total > 15_000_000:
            break
        files.append({"name": p.name, "type": "application/pdf" if p.suffix.lower() == ".pdf" else "application/octet-stream",
                      "data": base64.b64encode(data).decode()})
    for to in recipients[:3]:
        r = requests.post(url, json={"key": key, "to": to, "subject": subject, "text": text, "html": html or "",
                                     "attachments": files}, timeout=120)
        try:
            res = r.json()
        except Exception:
            body = r.text or ""
            if "authoriz" in body.lower() or "accounts.google.com" in r.url:
                raise RuntimeError("Google's permission step isn't finished. In the script editor, click Run "
                                   "(with doPost selected), then Advanced → Go to project → Allow. Then try again.")
            if r.status_code == 404 or "unable to open the file" in body.lower():
                raise RuntimeError("That Web app URL doesn't exist. Copy it again from Deploy → Manage deployments.")
            import re as _re
            snippet = _re.sub(r"<[^>]+>", " ", body)
            snippet = _re.sub(r"\s+", " ", snippet).strip()[:160]
            raise RuntimeError(f"The email sender answered with: [{r.status_code}] {snippet}")
        if not res.get("ok"):
            err = str(res.get("error", "unknown problem"))
            if err == "wrong key":
                raise RuntimeError("Email not sent: the app's key doesn't match your email sender's key. "
                                   "Fix it in Setup → Daily email → \"Emails not arriving?\" (takes 1 minute).")
            raise RuntimeError("Email not sent: " + err)


def send(subject: str, text: str, html: str | None = None, attachments: list[Path] | None = None, to: str | None = None):
    url, key = relay_config()
    recipients = [a.strip() for a in (to or "").replace(";", ",").split(",") if "@" in a]
    if url and key:
        if not recipients:
            raise RuntimeError("Add the email address the list should go to.")
        return _send_relay(url, key, subject, text, html, attachments, recipients)
    user, pw = gmail_creds()
    if not (user and pw):
        raise RuntimeError("Email sending isn't set up yet.")
    msg = EmailMessage()
    recipients = recipients or [user]
    msg["Subject"], msg["From"], msg["To"] = subject, user, ", ".join(recipients)
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    total = 0
    for p in attachments or []:
        data = p.read_bytes()
        total += len(data)
        if total > 20_000_000:  # stay under Gmail's 25 MB limit
            break
        sub_t = {"pdf": "pdf", "docx": "vnd.openxmlformats-officedocument.wordprocessingml.document",
                 "tex": "x-tex"}.get(p.suffix.lstrip(".").lower(), "octet-stream")
        msg.add_attachment(data, maintype="application", subtype=sub_t, filename=p.name)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as s:
        s.login(user, pw)
        s.send_message(msg)


def send_daily_list(picks, folder: str, attach_pdfs: bool = True, run_info: dict | None = None, to: str | None = None):
    """picks: the DataFrame returned by run_today()."""
    rows = picks.to_dict("records")
    date = Path(folder).name
    counts = {t: sum(1 for r in rows if r.get("size") == t) for t in ("big", "mid", "small", "other")}
    subject = (f"Your list for today is ready: {len(rows)} roles "
               f"({counts['big']} big · {counts['mid']} mid · {counts['small']} small"
               + (f" · {counts['other']} other" if counts["other"] else "") + ")")
    lines, html_rows = [], []
    for r in rows:
        why = r.get("why") or ""
        lines.append(f"{r['#']}. {r['company']} ({r['size']}): {r['title']}, {r['location']}\n   Fit {r.get('score', '')} · {why}\n"
                     f"   Apply: {r['url']}  ({r.get('apply_site', '')})")
        html_rows.append(
            f"<tr><td>{r['#']}</td><td><b>{escape(str(r['company']))}</b><br><small>{escape(str(r['size']))}</small></td>"
            f"<td><a href='{escape(str(r['url']))}'>{escape(str(r['title']))}</a><br><small>{escape(str(r['location']))}"
            f"<br>Opens: {escape(str(r.get('apply_site', '')))}</small></td>"
            f"<td style='text-align:center'>{escape(str(r.get('score', '')))}</td><td><small>{escape(why)}</small></td>"
            f"<td><small>{escape(str(r.get('sponsorship', '')))}</small></td></tr>")
    intro = ""
    if run_info:
        intro = (f"Checked {run_info.get('found', 0):,} postings; {run_info.get('passed', 0):,} matched your filters. "
                 "Every link goes straight to the company's own posting, not a job board. "
                 "Tailored resumes are attached, numbered to match the list.")
        if run_info.get("web"):
            intro += " " + str(run_info["web"])
    stop = ("Don't want these emails anymore? Open Daily Job Agent → Setup → 6. Daily email → Turn daily email off. "
            "(If it runs on GitHub, also click 'Stop running on GitHub' in step 7.)")
    text = (intro + "\n\n" + "\n\n".join(lines) + "\n\nThe same list, with your tailored resumes, is waiting in the app. "
            "Click 'I applied' there and the role moves to your Tracker; 'Save' keeps it for later.\n\n" + stop)
    html = (f"<div style='font-family:Arial,sans-serif'><p>{escape(intro)}</p>"
            "<table cellpadding='6' style='border-collapse:collapse;font-size:13px' border='1'>"
            "<tr style='background:#f2f2f2'><th>#</th><th>Company</th><th>Role (click to apply)</th><th>Fit</th><th>Why</th>"
            "<th>Sponsorship wording</th></tr>" + "".join(html_rows) + "</table>"
            "<p style='font-size:12px;color:#666'>Read each tailored resume before you send it. The same list is waiting in "
            "the app: click <b>I applied</b> and the role moves to your Tracker with the resume you used; <b>Save</b> keeps "
            f"it for later.</p><p style='font-size:11px;color:#999'>{escape(stop)}</p></div>")
    pdfs = []
    if attach_pdfs:
        pdfs = [Path(folder) / Path(r["pdf"]).name for r in rows if r.get("pdf")]
        pdfs = [p for p in pdfs if p.exists()]
    send(subject, text, html, pdfs, to=to)
