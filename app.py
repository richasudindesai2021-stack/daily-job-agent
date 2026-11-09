"""Daily Job Agent: set it up once, then get fresh roles and a tailored resume for each, every day.

Run with:  python -m streamlit run app.py
"""
from __future__ import annotations

import datetime as _dt
import json
import io
import os
import re
import tempfile
import uuid
from pathlib import Path

import pandas as pd
import streamlit as st

from agent import store
from agent.llm import LLM, PROVIDERS, key_name_for, pick_fast_model
from agent.pipeline import run_today
from agent.resume import file_to_text, parse_resume
from agent.sources import fetch_company

st.set_page_config(page_title="Daily Job Agent", page_icon=":material/work:", layout="wide", initial_sidebar_state="collapsed")
SECRETS_FILE = store.ROOT / ".streamlit" / "secrets.toml"
STATUS = ["to apply", "saved", "applied", "skipped", "not interested in company"]

st.markdown("""<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap');
:root { --bg:#0A0A0A; --card:#141414; --card2:#1B1B1B; --line:#262626; --line2:#333; --text:#F2F0EA; --soft:#B9B6AE;
        --muted:#7D7A73; --y:#F7A8C8; --y2:#FBC8DC; --ydim:rgba(247,168,200,.12); }
html, body, .stApp, .stMarkdown, p, label, input, textarea, button, li { font-family:'Inter', sans-serif; }
.stApp { background: var(--bg); color: var(--text); }
.block-container { padding-top: 1.2rem; max-width: 1150px; }
header[data-testid="stHeader"] { background: transparent; }
h1, h2, h3, h4 { font-family:'Space Grotesk', sans-serif !important; font-weight:700 !important; color: var(--text) !important; letter-spacing:-.02em; }
h3 { font-size: 1.9rem !important; } h4 { font-size: 1.3rem !important; }
.hero { position:relative; overflow:hidden; background: radial-gradient(120% 140% at 100% 0%, #1d1a0b 0%, #0f0f0f 45%, #0A0A0A 100%);
        border:1px solid var(--line); border-radius: 18px; padding: 2rem 2.3rem 1.8rem; margin-bottom: 1rem; }
.hero:after { content:""; position:absolute; right:-60px; top:-60px; width:220px; height:220px; border-radius:50%;
        background: radial-gradient(circle, rgba(247,168,200,.22), transparent 70%); }
.hero .eyebrow { font-family:'JetBrains Mono', monospace; font-size:.74rem; letter-spacing:.08em; color: var(--muted); }
.hero .eyebrow b { color: var(--y); font-weight:500; }
.hero h1 { font-size: 3.2rem !important; margin: .5rem 0 .3rem; line-height: 1.02; }
.hero h1 em { font-style: normal; color: #0A0A0A; background: var(--y); padding: 0 .18em; border-radius: 6px; }
.hero p { color: var(--soft); font-size: 1.02rem; margin: 0; max-width: 680px; }
.hero .quip { margin-top: .9rem; font-family:'JetBrains Mono', monospace; font-size:.8rem; color: var(--y2); }
.step-hint { color: var(--soft); font-size: .95rem; margin-top: -.3rem; }
.meta { color: var(--muted); font-size: .9rem; }
.tag { font-family:'JetBrains Mono', monospace; font-size:.7rem; color: var(--y); border:1px solid rgba(247,168,200,.35);
       padding: 1px 7px; border-radius: 999px; margin-left: 6px; }
div[data-testid="stVerticalBlockBorderWrapper"] { border-radius: 14px; border-color: var(--line) !important; background: var(--card); }
button[kind="primary"], button[kind="primaryFormSubmit"], a[data-testid^="stBaseLinkButton-primary"] { background: var(--y) !important; border-color: var(--y) !important;
        color: #0A0A0A !important; font-weight: 600 !important; border-radius: 10px; }
button[kind="primary"] *, button[kind="primaryFormSubmit"] *, a[data-testid^="stBaseLinkButton-primary"] * { color: #0A0A0A !important; }
button[kind="primary"]:hover, a[data-testid^="stBaseLinkButton-primary"]:hover { background: var(--y2) !important; border-color: var(--y2) !important; }
button[kind="secondary"] { border-radius: 10px; border-color: var(--line2) !important; background: var(--card2); }
button[kind="secondary"]:hover { border-color: var(--y) !important; color: var(--y) !important; }
/* step list on the left: aligned, compact */
.st-key-stepnav button { justify-content: flex-start !important; width: 100%; padding: .45rem .7rem; border-radius: 10px; }
.st-key-stepnav button > div { justify-content: flex-start !important; width: 100%; }
.st-key-stepnav button p { text-align: left; white-space: nowrap; }
.st-key-stepnav button[kind="tertiary"] { color: var(--soft) !important; }
.st-key-stepnav button[kind="tertiary"]:hover { color: var(--y) !important; background: var(--ydim); }
div[data-testid="stMetricValue"] { font-family:'Space Grotesk', sans-serif; font-weight:700; font-size: 2rem; color: var(--y); }
div[data-testid="stMetricLabel"] p, .stCaption, small { font-family:'JetBrains Mono', monospace; letter-spacing:.02em; }
div[data-testid="stProgress"] div[role="progressbar"] > div > div { background: var(--y) !important; }
a { color: var(--y) !important; }
hr { border-color: var(--line) !important; }
@media (max-width: 900px) {
  .block-container { padding-left: 1.2rem; padding-right: 1.2rem; }
  .hero { padding: 1.6rem 1.5rem 1.4rem; } .hero h1 { font-size: 2.5rem !important; }
}
@media (max-width: 640px) {
  .block-container { padding: 3.2rem .8rem 2rem; }
  .hero { padding: 1.2rem 1.1rem 1.1rem; border-radius: 14px; }
  .hero .eyebrow { font-size: .64rem; } .hero h1 { font-size: 2.1rem !important; } .hero p { font-size: .92rem; }
  h3 { font-size: 1.55rem !important; } h4 { font-size: 1.2rem !important; }
  div[data-testid="stMetricValue"] { font-size: 1.6rem; }
  button, a[data-testid^="stBaseLinkButton-primary"] { min-height: 44px; }
}
</style>""", unsafe_allow_html=True)


try:  # fix a secrets file that older versions could break (a backup key saved with quotes in it)
    from agent.llm import repair_secrets as _repair
    _repair()
except Exception:
    pass


def _secret(name: str) -> str:
    try:
        return str(st.secrets.get(name, "") or os.environ.get(name, ""))
    except Exception:
        return os.environ.get(name, "")


# Hosted mode (HOSTED_MODE = "true" in the host's secrets): each visitor gets a private workspace and brings their own key.
HOSTED = _secret("HOSTED_MODE").lower() in ("1", "true", "yes")
WS_ROOT = Path(tempfile.gettempdir()) / "job-agent-users"
if HOSTED:
    # Nothing is kept: each visitor works in a scratch folder that is wiped a few hours after they leave.
    # Their saved copy lives only in their own GitHub (Step 7).
    import shutil as _sh
    import time as _time
    if "ws_id" not in st.session_state:
        st.session_state["ws_id"] = uuid.uuid4().hex
        for _old in (WS_ROOT.iterdir() if WS_ROOT.exists() else []):
            try:
                if _time.time() - _old.stat().st_mtime > 3 * 3600:
                    _sh.rmtree(_old, ignore_errors=True)
            except OSError:
                pass
    store.use_workspace(WS_ROOT / st.session_state["ws_id"])
    (WS_ROOT / st.session_state["ws_id"]).touch()
    from agent.llm import SESSION_SECRETS
    SESSION_SECRETS.set(st.session_state.setdefault("sec", {}))

# Local mode: this app holds your resume, saved keys and Gmail login, so only this computer may open it.
# (Streamlit also listens on your Wi-Fi address, e.g. on campus networks, so others could otherwise reach it.)
if not HOSTED:
    _ip = getattr(st.context, "ip_address", None)
    if _ip and _ip not in ("127.0.0.1", "::1", "localhost") and not _ip.startswith("::ffff:127."):
        st.error("This Daily Job Agent can only be opened on the computer it runs on.")
        st.stop()

settings = store.load_settings()


def save_secret(pairs: dict):
    if HOSTED:  # kept in this browser session only, never written to the server's disk
        st.session_state.setdefault("sec", {}).update(pairs)
        return
    from agent.llm import read_secrets, write_secrets
    vals = read_secrets()
    vals.update({k: v for k, v in pairs.items()})
    write_secrets({k: v for k, v in vals.items() if v != "" or k not in pairs})


# ---------------- AI connection (shared by every page) ----------------
provider = settings["provider"] if settings["provider"] in PROVIDERS else list(PROVIDERS)[0]
KEY_NAME = key_name_for(provider)
from agent.llm import local_secret as _local_secret
# read straight from the secrets file: saving another key reloads Streamlit's copy, and for a moment it looks empty,
# which used to bounce people back to Setup
api_key = st.session_state.get("api_key::" + provider) or ("" if HOSTED else (_local_secret(KEY_NAME) or _secret(KEY_NAME)))
llm = None
if api_key:
    try:
        llm = LLM(provider, api_key, settings.get("model") or None, base_url=settings.get("base_url") or None)
        llm.fast_model = settings.get("fast_model") or None
        if not settings.get("fast_model") and "Gemini" in provider:  # upgrade for people who connected before this existed
            llm.fast_model = "gemini-flash-lite-latest"
        from agent.llm import with_backup
        with_backup(llm, provider)
    except Exception:
        llm = None

# ---------------- progress ----------------
resume = store.load_resume()
bank = store.load_project_bank()
companies = store.load_companies()
if not HOSTED:
    try:  # owner's laptop: keep finding job pages in the background whenever the app is open
        from agent.company_lists import auto_continue as _auto
        _auto()
    except Exception:
        pass
if not settings.get("age_v2"):  # newest-first with a 2-week cap replaced the old 1-3 day window
    settings.update(max_age_hours=336, age_v2=True)
    store.save_settings(settings)

STEPS = [
    ("ai", "Connect AI", bool(api_key), False),
    ("resume", "Upload resume", bool(resume and resume.get("experience")), False),
    ("projects", "Add projects", bool(bank) or settings.get("skip_projects", False), True),
    ("targets", "Choose jobs", settings.get("targets_saved", False), False),
    ("companies", "Pick companies", settings.get("companies_ok", False), False),
    ("alerts", "Daily email", bool(settings.get("daily_email") and settings.get("daily_email_time")), False),
    ("cloud", "Run without laptop", bool(settings.get("cloud_enabled")), True),
]
setup_done = all(done for key, _, done, optional in STEPS if not optional)
first_open = next((key for key, _, done, _ in STEPS if not done), None)

if "page" not in st.session_state:
    st.session_state["page"] = "today" if setup_done else "setup"
if "step" not in st.session_state:
    st.session_state["step"] = first_open or "ai"


def goto_step(key):
    st.session_state["page"], st.session_state["step"] = "setup", key


def next_step(after: str):
    keys = [k for k, *_ in STEPS]
    i = keys.index(after)
    st.session_state["step"] = keys[i + 1] if i + 1 < len(keys) else "finish"
    st.rerun()


def mark(flag: str, value=True):
    settings[flag] = value
    store.save_settings(settings)


# ---------------- header & navigation ----------------
QUIPS = ["Recruiters skim a resume in about 6 seconds. Yours gets tailored in about 20.",
         "No more “posted 30+ days ago.” Nothing here is older than 3 days.",
         "Your resume, except this time it actually read the job post.",
         "Less doom-scrolling job boards. More hitting Apply.",
         "Sponsorship wording checked before you fall in love with a role.",
         "30 roles a day. Zero cover letters written at 2 a.m. (well, fewer)."]
import random as _rnd
_quip = st.session_state.setdefault("quip", _rnd.choice(QUIPS))
st.markdown(f"""<div class="hero"><div class="eyebrow">[ <b>ai job search</b> · sponsor-friendly · refreshed daily ]</div>
<h1>Daily Job <em>Agent</em></h1>
<p>Fresh roles at companies that sponsor, each with a resume tailored to it. You just apply.</p>
<div class="quip">› {_quip}</div></div>""", unsafe_allow_html=True)
if HOSTED:
    st.caption("A personal project: I built this to automate my own job search, and you're welcome to try it. "
               "It runs on free AI keys, so you may hit daily limits or the odd hiccup. "
               "Feedback is very welcome: [dailyjobagent28@gmail.com](mailto:dailyjobagent28@gmail.com?subject=Daily%20Job%20Agent%20feedback).")
h1, h2 = st.columns([3, 2])
pages = {"setup": "Setup", "today": "Today's list", "chat": "Resume chat", "tracker": "Tracker"}
if setup_done:
    choice = h1.segmented_control("Go to", list(pages.values()), default=pages[st.session_state["page"]],
                                  label_visibility="collapsed")
    if choice:
        st.session_state["page"] = {v: k for k, v in pages.items()}[choice]
else:
    st.session_state["page"] = "setup"
    done_count = sum(1 for _, _, d, opt in STEPS if d and not opt)
    n_req = sum(1 for _, _, _, opt in STEPS if not opt)
    h1.caption(f"Setup · {done_count} of {n_req} required steps done")
    h1.progress(done_count / n_req)

if HOSTED:
    with st.sidebar:
        st.subheader("Your data stays yours")
        st.caption("This website keeps nothing. Your setup is saved only in your own private GitHub project "
                   "(last setup step), and this page's copy is wiped a few hours after you leave.")
        st.markdown("**Already set up?** Load your setup from GitHub to change it.")
        _tok = st.text_input("GitHub key", type="password", key="gh_load_tok", placeholder="ghp_...",
                             help="Make one here: " + __import__("agent.cloud", fromlist=["x"]).TOKEN_URL)
        if st.button("Load my setup", disabled=not _tok):
            from agent import cloud as _cloud
            try:
                _n = _cloud.load_into_workspace(_tok)
                st.session_state["gh_token"] = _tok
                st.session_state.pop("result", None)
                st.success(f"Loaded {_n} files.")
                st.rerun()
            except Exception as e:
                st.error(str(e)[:200])
        if st.button("Wipe my data from this website now"):
            import shutil as _sh
            _sh.rmtree(WS_ROOT / st.session_state["ws_id"], ignore_errors=True)
            st.session_state.clear()
            st.rerun()


# =====================================================================
#                               SETUP
# =====================================================================
def step_ai():
    st.markdown("### Step 1 · Connect an AI")
    st.markdown('<p class="step-hint">The AI scores each job against your resume and tailors it. '
                'Use your own key. Gemini is free.</p>', unsafe_allow_html=True)
    prov_list = list(PROVIDERS)
    prov = st.selectbox("AI provider", prov_list, index=prov_list.index(provider),
                        help="Gemini and Groq have free tiers. Claude and OpenAI bill your own account.")
    cfg = PROVIDERS[prov]
    if cfg["key_url"]:
        st.markdown(f"1. Get a key here: [{cfg['key_url']}]({cfg['key_url']})  \n2. Paste it below and click **Connect**  \n"
                    "3. Pick a model and click **Save and continue**")
    base_url = ""
    if not cfg["base_url"]:
        base_url = st.text_input("API base URL", settings.get("base_url", ""), placeholder="https://.../v1")
    kname = key_name_for(prov)
    existing = st.session_state.get("api_key::" + prov) or ("" if HOSTED else _secret(kname))
    key = st.text_input("API key", existing, type="password", placeholder="Paste your key here")

    # 1) Connect: check the key and fetch the models it can use
    if st.button("Connect", type="primary" if not st.session_state.get("models::" + prov) else "secondary", disabled=not key):
        with st.spinner("Checking your key...", show_time=True):
            try:
                names = LLM(prov, key, None, base_url=base_url or None).list_models()
                if not names:
                    raise RuntimeError("the provider returned no models for this key")
                st.session_state["models::" + prov] = chat_models(names)
                st.session_state["allmodels::" + prov] = names
                st.session_state["keyok::" + prov] = key
            except Exception as e:
                st.session_state.pop("models::" + prov, None)
                st.error(f"That key didn't work: {str(e)[:160]}")

    # 2) Pick a model and save
    models = st.session_state.get("models::" + prov)
    if models and st.session_state.get("keyok::" + prov) == key:
        st.success(f"Key works. {len(models)} models available.", icon=":material/check_circle:")
        saved = settings.get("model") if settings.get("provider") == prov else ""
        default = saved if saved in models else recommended_model(models, cfg["default_model"])
        model = st.selectbox("Model", models, index=models.index(default),
                             help="The recommended one is selected. Fast, cheaper models work well for this.")
        remember = True
        if not HOSTED:
            remember = st.checkbox("Remember this key on this laptop", True,
                                   help="Saved only on this computer, so you don't paste it again.")
        st.caption(f"For quick jobs like reading your resume, the agent uses a lighter model "
                   f"({pick_fast_model(st.session_state.get('allmodels::' + prov, models), model)}); tailoring uses the one above.")
        if st.button("Save and continue →", type="primary"):
            st.session_state["api_key::" + prov] = key
            if remember and not HOSTED:
                save_secret({kname: key})
            fast = pick_fast_model(st.session_state.get("allmodels::" + prov, models), model)
            settings.update({"provider": prov, "model": model, "base_url": base_url, "fast_model": fast})
            store.save_settings(settings)
            next_step("ai")
    elif api_key and settings.get("provider") == prov:
        st.caption(f":material/check_circle: Connected to {provider}" + (f" · {settings.get('model')}" if settings.get("model") else ""))
    _keys_manager()


def _key_adder(prefix: str):
    """Add a key from any provider. Used in Setup and in the pop-up when keys run out."""
    from agent import keys as K

    kinds = K.kinds()
    c1, c2 = st.columns([1.3, 2])
    kind = c1.selectbox("Provider", kinds, key=prefix + "_kind",
                        help="Any of these works. AI keys write your resumes; search keys help find roles.")
    url = K.key_url(kind)
    val = c2.text_input("Key", type="password", key=prefix + "_val", placeholder="Paste the key")
    base, model = "", ""
    if kind.startswith("Other"):
        o1, o2 = st.columns(2)
        base = o1.text_input("Its API address", key=prefix + "_base", placeholder="https://api.example.com/v1",
                             help="Any company whose API works like OpenAI's (most do: Mistral, Together, DeepSeek, "
                                  "Cerebras, Fireworks, Perplexity...). Their docs list this address.")
        model = o2.text_input("Model name", key=prefix + "_model", placeholder="e.g. mistral-small-latest")
    b1, b2 = st.columns([1, 1])
    if url:
        b2.link_button(f"Get a free {K.short(kind)} key", url, width="stretch")
    need_base = kind.startswith("Other") and not base.strip()
    if b1.button("Add key", key=prefix + "_add", type="primary", disabled=not val.strip() or need_base, width="stretch"):
        items = K.load()
        if any(i["key"] == val.strip() for i in items):
            st.info("That key is already added.")
        else:
            items.append({"provider": kind, "key": val.strip(), **({"base_url": base.strip()} if base.strip() else {}),
                          **({"model": model.strip()} if model.strip() else {})})
            save_secret({"BACKUP_KEYS": K.dumps(items)})
            from agent import quota as _q
            _q.clear_ai_outs()
            from agent.pipeline import TAILOR as _TT
            _TT["ai_out"] = False
            st.session_state["_key_added"] = True
            st.toast("Key added. It's used when the keys before it run out.")
            st.rerun()


def _keys_manager():
    """Step 1: backup and extra keys, any provider, used in order."""
    from agent import keys as K, quota

    with st.container(border=True):
        st.markdown("#### Backup keys (optional, recommended)")
        st.caption("Free keys have daily limits. Add more keys from any provider (another Gemini account, Groq, OpenAI, "
                   "OpenRouter, a search service like Tavily...). When one runs out, the next takes over, and the app "
                   "tells you which one it's using.")
        items = K.load()
        in_use = None
        if llm is not None:
            from agent import quota as _q
            in_use = next((m.label for m in llm.chain() if _q.check("ai:" + m.label)[0]), None)
        tab = st.expander(f"Your keys ({len(items) + 1})" + (f" · writing resumes with {in_use}" if in_use else ""))
        tab.caption(f"Main: {provider.split(' (')[0]} key from above")
        for n, e in enumerate(items, 1):
            svc = ("ai:" if K.is_ai(e["provider"]) else
                   {"Tavily (web search)": "tavily:", "JSearch (job listings)": "jsearch:"}.get(e["provider"], "")) + K.label(e)
            ok, msg = quota.check(svc)
            c1, c2 = tab.columns([5, 1])
            c1.markdown(f"**{n}.** {K.label(e)}  <span class='meta'>· "
                        f"{'writes resumes' if K.is_ai(e['provider']) else 'finds roles'} · "
                        f"{'ready' if ok else 'used up for now'}</span>", unsafe_allow_html=True)
            if c2.button("Remove", key=f"rmkey_{n}"):
                if e.get("legacy"):
                    save_secret({e["legacy"]: ""})
                save_secret({"BACKUP_KEYS": K.dumps([x for x in items if x is not e])})
                st.rerun()
        with st.expander("Add a key" if items else "Add your first backup key", expanded=False):
            _key_adder("setup")


SKIP_MODEL_WORDS = ("embed", "tts", "image", "audio", "live", "robotics", "lyria", "aqa", "transcribe", "translate",
                    "veo", "imagen", "whisper", "dall", "moderation", "nano-banana", "computer-use", "deep-research",
                    "realtime", "search", "omni")


def chat_models(names: list[str]) -> list[str]:
    """Only text models that can tailor a resume, newest-looking first."""
    keep = [n for n in names if not any(w in n.lower() for w in SKIP_MODEL_WORDS)]
    return sorted(keep or names, reverse=True)


def recommended_model(models: list[str], fallback: str) -> str:
    for want in ("gemini-3.8-flash", "gemini-flash-latest", "claude-haiku-5-5", "claude-sonnet-5-5", "gpt-5.4-mini"):
        if want in models:
            return want
    if fallback in models:
        return fallback
    fast = [m for m in models if any(w in m for w in ("flash", "haiku", "mini"))]
    return (fast or models)[0]


def _md_link(item: str) -> str:
    import re as _re
    m = _re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", item)
    if m:
        return item.replace(m.group(0), f"[{m.group(0)}](mailto:{m.group(0)})")
    m = _re.search(r"((?:https?://)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s)]*)?)", item, _re.I)
    if m and not _re.fullmatch(r"[\d().\s+-]+", item):
        u = m.group(0)
        return item.replace(u, f"[{u}]({u if u.startswith('http') else 'https://' + u})")
    return item


def resume_preview(r: dict):
    st.markdown(f"#### {r.get('name', '')}")
    st.markdown(" · ".join(_md_link(c) for c in r.get("contact", []) if c))

    def entries(items, head):
        for e in items:
            dates = f"  \n<span class='meta'>{e.get('dates', '')}</span>" if e.get("dates") else ""
            st.markdown(f"**{head(e)}**{dates}", unsafe_allow_html=True)
            for b in e.get("bullets", []) or e.get("details", []) or []:
                st.markdown(f"- {b}")

    lk = lambda e: f" · {_md_link(e['link'])}" if e.get("link") else ""
    if r.get("education"):
        st.markdown("##### Education")
        entries(r["education"], lambda e: f"{e.get('school', '')} · {e.get('degree', '')}" + lk(e))
    if r.get("leadership"):
        st.markdown("##### Leadership")
        entries(r["leadership"], lambda e: f"{e.get('title', '')} · {e.get('org', '')}" + lk(e))
    if r.get("experience"):
        st.markdown("##### Experience")
        entries(r["experience"], lambda e: f"{e.get('title', '')} · {e.get('org', '')}" + lk(e))
    if r.get("projects"):
        st.markdown("##### Projects")
        entries(r["projects"], lambda e: e.get("name", "") + (f" · {_md_link(e['link'])}" if e.get("link") else ""))
    if r.get("skills"):
        st.markdown("##### Skills")
        for k, v in r["skills"].items():
            if v:
                st.markdown(f"**{k}:** {v}")


def step_resume():
    st.markdown("### Step 2 · Upload your resume")
    st.markdown('<p class="step-hint">Upload the resume you use most. The agent only rephrases and reorders what\'s on it. '
                'It never adds numbers, tools or experience.</p>', unsafe_allow_html=True)
    if HOSTED:
        st.caption("Tip: leave your phone number and home address off. Free AI tiers may use what you send to improve their models.")
    up = st.file_uploader("Your resume: PDF, LaTeX (.tex), Word (.docx) or text file", type=["pdf", "tex", "docx", "txt", "md"])
    with st.expander("Or paste it instead"):
        pasted = st.text_area("Resume text or LaTeX code", height=200, label_visibility="collapsed",
                              placeholder="Paste your resume text, or your full LaTeX code")
    source = file_to_text(up.name, up.getvalue()) if up else (pasted or "")
    if st.button("Read my resume", type="primary", disabled=(llm is None or not source.strip())):
        note = st.empty()
        llm.status = lambda m: note.warning(m + " Free AI keys have per-minute limits; this usually clears quickly.")
        with st.spinner("Reading your resume. Usually 10 to 20 seconds, which is still longer than a recruiter's first skim.",
                        show_time=True):
            try:
                if len(source.strip()) < 200:
                    raise ValueError("couldn't find much text. If it's a scanned PDF, upload the .tex, .docx or paste the text")
                store.save_resume(parse_resume(llm, source))
                st.session_state["resume_source"] = source
                st.session_state.pop("preview", None)
                from agent.render import save_original
                if up:
                    save_original(up.name, up.getvalue())
                    if up.name.lower().endswith((".tex", ".docx")) and settings.get("resume_format") != "clean_locked":
                        settings["resume_format"] = "original"
                        store.save_settings(settings)
                st.rerun()
            except Exception as e:
                st.error(f"Couldn't read it: {str(e)[:200]}. If this keeps happening, go to Step 1 and pick "
                         "a lighter model such as gemini-flash-latest.")
    if llm is None:
        st.info("Connect an AI in Step 1 first.")
    r = store.load_resume()
    if r and r.get("experience"):
        st.success("Here's what the agent read. Check it, then continue.")
        if st.session_state.get("resume_source"):
            from agent.resume import missing_links, unmatched_lines
            lost = missing_links(r, st.session_state["resume_source"])
            if lost:
                st.warning("These links are in your file but weren't placed on the resume. Add them below "
                           "(as a project's or entry's \"link\", or in \"contact\"):\n\n" + "\n".join(f"- {u}" for u in lost))
            else:
                st.caption(":material/link: Every link in your file was kept.")
            odd = unmatched_lines(r, st.session_state["resume_source"])
            if odd:
                st.warning(f"{len(odd)} line(s) don't exactly match your file. Double-check them, and fix them below if needed:\n\n"
                           + "\n".join(f"- {o}" for o in odd[:8]))
            else:
                st.caption(":material/check_circle: Every bullet matches your file word for word.")
        with st.container(border=True):
            resume_preview(r)
        with st.expander("Something wrong? Fix it here"):
            edited = st.text_area("Resume details", json.dumps(r, indent=2), height=350, label_visibility="collapsed")
            if st.button("Save fixes"):
                try:
                    store.save_resume(json.loads(edited))
                    st.success("Saved.")
                    st.rerun()
                except json.JSONDecodeError as e:
                    st.error(f"Something's off in the formatting: {e}")
        from agent.render import MIME, original_path, render
        with st.container(border=True):
            st.markdown("**How should your tailored resumes look?**")
            orig = original_path()
            fmt_opts = ["Clean design (the agent can swap in your best projects for each job)"]
            if orig:
                fmt_opts.insert(0, f"Keep my own format (your {orig.suffix[1:].upper()} file, only the wording changes)")
            cur = 0 if (orig and settings.get("resume_format") == "original") or not orig else 1
            fmt = st.radio("Format", fmt_opts, index=cur if cur < len(fmt_opts) else 0, label_visibility="collapsed")
            if not orig:
                st.caption("To keep your exact format, upload your LaTeX (.tex) or Word (.docx) file instead of a PDF.")
            pages = st.radio("Length", [1, 2], index=int(settings.get("resume_pages", 1)) - 1, horizontal=True,
                             format_func=lambda n: f"{n} page" + ("s" if n > 1 else ""))
            new_fmt = "original" if fmt.startswith("Keep my own") else "clean"
            if (new_fmt, pages) != (settings.get("resume_format"), settings.get("resume_pages", 1)):
                settings.update({"resume_format": new_fmt, "resume_pages": int(pages)})
                store.save_settings(settings)
            if new_fmt == "original":
                st.caption("Projects stay as they are in your file. Picking different projects for each job works "
                           "with the clean design.")
        c1, c2 = st.columns([1, 3])
        if c1.button("Looks good, continue →", type="primary"):
            next_step("resume")
        if c2.button("Preview my resume"):
            with st.spinner("Building the preview...", show_time=True):
                out = render(r, settings, r)
            if out["pdf"]:
                st.session_state["preview"] = (out["pdf"], "pdf", out)
            elif out["source"]:
                st.session_state["preview"] = (out["source"][0], out["source"][1], out)
        pv = st.session_state.get("preview")
        if pv:
            data, ext, info = pv
            pg = info.get("pages")
            if pg and pg > int(settings.get("resume_pages", 1)):
                st.warning(f"It's {pg} pages, but you chose {settings.get('resume_pages', 1)}. "
                           "Trim it in your file, or switch to the clean design, which fits it automatically.")
            st.download_button(f"Download preview ({ext.upper()}" + (f", {pg} page" + ("s" if pg and pg > 1 else "") if pg else "") + ")",
                               data, f"resume_preview.{ext}", MIME[ext])
            if info.get("note"):
                st.caption(info["note"])


def step_projects():
    st.markdown("### Step 3 · Add your projects and roles (optional)")
    st.markdown('<p class="step-hint">Add every project you could show. For each job, the agent puts the best matches on '
                'your resume with a clickable Project Website link.</p>', unsafe_allow_html=True)
    b = store.load_project_bank()

    with st.form("add_project", clear_on_submit=True, border=True):
        st.markdown("**Add a project**")
        c1, c2 = st.columns(2)
        title = c1.text_input("Project title *", placeholder="Clarity: AI Productivity Assistant")
        award = c2.text_input("Award or recognition", placeholder="Semi-Finalist, Global MSBA AI Case Competition")
        c3, c4, c5 = st.columns([2, 2, 1])
        link = c3.text_input("Project link", placeholder="clarityai-app.netlify.app")
        tools = c4.text_input("Tools used", placeholder="Python, Figma, SQL")
        dates = c5.text_input("Dates", placeholder="2026")
        desc = st.text_area("What you did and what happened *", height=120,
                            placeholder="What was the problem, what did you build or analyze, and what was the result? "
                                        "Include real numbers if you have them.")
        use_ai = st.checkbox("Turn this into resume bullets for me", True,
                             help="The AI writes 2-3 bullets using only what you typed. Untick to use your own lines as bullets.")
        if st.form_submit_button("Add project", type="primary"):
            if not (title.strip() and desc.strip()):
                st.error("Add a title and a description.")
            else:
                from agent.resume import project_from_fields
                with st.spinner("Writing your bullets...", show_time=True):
                    try:
                        proj = project_from_fields(llm, title, award, link, tools, dates, desc, use_ai)
                    except Exception as e:
                        st.error(f"Couldn't write bullets ({str(e)[:100]}). Saved your own lines instead.")
                        proj = project_from_fields(None, title, award, link, tools, dates, desc, False)
                store.save_project_bank([p for p in b if p["name"].lower() != proj["name"].lower()] + [proj])
                st.rerun()

    if b:
        st.markdown(f"**Your projects ({len(b)})**")
        for i, p in enumerate(b):
            with st.container(border=True):
                l, r = st.columns([5, 1])
                l.markdown(f"**{p['name']}**" + (f"  \n*{p['subtitle']}*" if p.get("subtitle") else "")
                           + (f"  \n[Project Website](https://{p['link'].removeprefix('https://')})" if p.get("link") else ""))
                for bl in p.get("bullets", []):
                    l.markdown(f"- {bl}")
                if r.button("Remove", key=f"rm_{i}", icon=":material/delete:"):
                    store.save_project_bank([q for j, q in enumerate(b) if j != i])
                    st.rerun()
                with l.expander("Edit"):
                    with st.form(f"edit_{i}"):
                        e1, e2 = st.columns(2)
                        nt = e1.text_input("Title", p["name"])
                        na = e2.text_input("Award or recognition", p.get("subtitle", ""))
                        e3, e4 = st.columns(2)
                        nl = e3.text_input("Link", p.get("link", ""))
                        nd = e4.text_input("Dates", p.get("dates", ""))
                        nb = st.text_area("Bullets (one per line)", "\n".join(p.get("bullets", [])), height=120)
                        if st.form_submit_button("Save changes"):
                            b[i] = {**p, "name": nt.strip(), "subtitle": na.strip(), "dates": nd.strip(),
                                    "link": nl.strip().removeprefix("https://").removeprefix("http://").rstrip("/"),
                                    "bullets": [x.strip(" -•") for x in nb.splitlines() if x.strip(" -•")]}
                            store.save_project_bank(b)
                            st.rerun()

    with st.expander("Have many projects? Paste them all at once"):
        src = st.text_area("Paste your portfolio text or project descriptions (include each project's link)", height=150)
        if st.button("Add them all", disabled=not (src and llm)):
            from agent.resume import build_project_bank
            with st.spinner("Reading your projects...", show_time=True):
                try:
                    new = build_project_bank(llm, src)
                    names = {p["name"].lower() for p in b}
                    store.save_project_bank(b + [p for p in new if p["name"].lower() not in names])
                    st.rerun()
                except Exception as e:
                    st.error(f"Couldn't read those projects: {str(e)[:160]}")

    _roles_bank()
    with st.container(border=True):
        st.markdown("**How many go on each resume**")
        st.caption("For every job, the agent picks the best-matching ones from your resume plus everything above.")
        n_res_exp = len((store.load_resume() or {}).get("experience", []))
        c1, c2 = st.columns(2)
        mp = c1.number_input("Projects per resume", 1, 6, int(settings.get("max_projects", 3)))
        me = c2.number_input("Roles (experience) per resume", 1, 8,
                             int(settings.get("max_experience") or max(n_res_exp, 1)))
        if mp != int(settings.get("max_projects", 3)) or me != int(settings.get("max_experience") or max(n_res_exp, 1)):
            settings.update(max_projects=int(mp), max_experience=int(me))
            store.save_settings(settings)
            st.toast("Saved")
    if st.button("Continue →" if b else "Skip for now →", type="primary" if b else "secondary"):
        if not b:
            mark("skip_projects")
        next_step("projects")


def _roles_bank():
    """Extra roles beyond the resume (internships, research, part-time jobs...). The agent picks the best per job."""
    bank = store.load_experience_bank()
    st.markdown("#### Your other roles")
    st.caption("Had more roles than fit on one resume? Add them here. For each job, the agent picks the most relevant "
               "ones and writes them in your resume's style.")
    with st.form("add_role", clear_on_submit=True, border=True):
        c1, c2 = st.columns(2)
        org = c1.text_input("Company or organization *", placeholder="USC Office of Sustainability")
        title = c2.text_input("Your title *", placeholder="Facilities Dashboard Intern")
        c3, c4 = st.columns(2)
        loc = c3.text_input("Location", placeholder="Los Angeles, CA")
        dates = c4.text_input("Dates", placeholder="Aug 2026 - Present")
        bullets = st.text_area("What you did (one point per line)", height=110,
                               placeholder="Built monthly energy, solar and water dashboards for 40 buildings\n...")
        if st.form_submit_button("Add role", type="primary"):
            if org.strip() and title.strip():
                bank.append({"org": org.strip(), "title": title.strip(), "location": loc.strip(), "dates": dates.strip(),
                             "bullets": [x.strip(" -•\t") for x in bullets.splitlines() if x.strip()]})
                store.save_experience_bank(bank)
                st.rerun()
            else:
                st.warning("Add the company and your title.")
    for i, e in enumerate(bank):
        c1, c2 = st.columns([6, 1])
        c1.markdown(f"**{e.get('org', '')}** · {e.get('title', '')}  <span class='meta'>{e.get('dates', '')} · "
                    f"{len(e.get('bullets', []))} points</span>", unsafe_allow_html=True)
        if c2.button("Remove", key=f"rmrole_{i}"):
            store.save_experience_bank([x for k, x in enumerate(bank) if k != i])
            st.rerun()


VISA_OPTS = ["F-1 OPT / STEM OPT (will need H-1B later)", "F-1 CPT", "H-1B (need a transfer)",
             "U.S. citizen or green card (no sponsorship needed)"]

ROLE_OPTIONS = ["Associate Product Manager", "Product Manager", "Product Analyst", "Business Analyst", "Data Analyst",
                "Data Scientist", "Strategy Analyst", "Consultant", "Business Intelligence", "Project Manager",
                "Program Manager", "Operations Analyst", "Financial Analyst", "Marketing Analyst", "Software Engineer"]


def step_targets():
    st.markdown("### Step 4 · Choose the jobs you want")
    st.markdown('<p class="step-hint">The agent only shows roles that match all of these.</p>', unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    with c1:
        roles = st.multiselect("Roles", sorted(set(ROLE_OPTIONS) | set(settings["roles"])), settings["roles"],
                               accept_new_options=True, help="Type to add your own.")
        locs = st.multiselect("Locations", sorted(set(settings["locations"]) | {"Austin", "Boston", "Chicago", "TX", "MA", "IL"}),
                              settings["locations"], accept_new_options=True,
                              help="Cities, states or state codes. Type to add your own.")
        allow_remote = st.toggle("Include remote-only roles", settings["allow_remote"])
    with c2:
        visa = st.selectbox("Work authorization", VISA_OPTS,
                            index=VISA_OPTS.index(settings["visa_status"]) if settings["visa_status"] in VISA_OPTS else 0,
                            help="If you'll need sponsorship, you only get companies that sponsor, and postings that rule it out are hidden.")
        min_sal = st.number_input("Minimum salary (USD a year)", 0, 500000, int(settings["min_salary"]), 5000,
                                  help="Only applied when a posting lists pay.")
        _ages = [24, 48, 72, 168, 336]
        _cur = int(settings["max_age_hours"])
        age = st.select_slider("Oldest posting to include", _ages, _cur if _cur in _ages else 336,
                               format_func=lambda h: {24: "1 day", 48: "2 days", 72: "3 days", 168: "1 week",
                                                      336: "2 weeks"}[h],
                               help="Newest roles always come first (last 24 hours, then 36, 48, 3 days, 1 week). "
                                    "Older ones only fill the spots that are left. Nothing older than 2 weeks.")
        st.markdown("**Roles per day, by company size**")
        m1, m2, m3 = st.columns(3)
        big = m1.number_input("Big", 0, 30, settings["size_mix"]["big"])
        mid = m2.number_input("Mid-size", 0, 30, settings["size_mix"]["mid"])
        small = m3.number_input("Small", 0, 30, settings["size_mix"]["small"])
    from agent.company_lists import industries_in, load_master
    _m = load_master()
    all_inds = industries_in(_m if _m is not None else store.load_companies())
    inds = st.multiselect("Industries you want (leave empty for all)", sorted(set(all_inds) | set(settings["industries"])),
                          settings["industries"], accept_new_options=True,
                          help="Your company list is built around these. Type to add your own.")
    with st.expander("More options"):
        bad = st.text_input("Never show these industries", ", ".join(settings["exclude_industries"]))
        excl = st.text_input("Skip titles containing", ", ".join(settings["exclude_title_words"]))
        max_proj = st.slider("Projects per resume", 1, 5, int(settings["max_projects"]))
        rules = st.text_area("Your resume rules (the AI follows these when tailoring)", settings["resume_rules"], height=150)
    if st.button("Save and continue →", type="primary", disabled=not roles):
        settings.update({
            "roles": roles, "locations": locs, "allow_remote": allow_remote, "visa_status": visa,
            "needs_sponsorship": not visa.startswith(("U.S. citizen", "No sponsorship")), "industries": inds,
            "exclude_industries": [x.strip() for x in bad.split(",") if x.strip()],
            "exclude_title_words": [w.strip() for w in excl.split(",") if w.strip()],
            "min_salary": int(min_sal), "max_age_hours": int(age),
            "size_mix": {"big": int(big), "mid": int(mid), "small": int(small)},
            "daily_count": int(big + mid + small), "max_projects": int(max_proj), "resume_rules": rules,
            "targets_saved": True,
        })
        store.save_settings(settings)
        next_step("targets")


def check_companies(df: pd.DataFrame):
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from agent.sources import configure

    configure(settings["roles"])
    df = df[(df["ats"].astype(str).str.strip() != "") & (df["token"].astype(str).str.strip() != "")]
    res, bar, recs = [], st.progress(0.0, "Checking..."), df.to_dict("records")
    with ThreadPoolExecutor(max_workers=24) as ex:
        futs = {ex.submit(fetch_company, r): r for r in recs}
        for i, f in enumerate(as_completed(futs), 1):
            row = futs[f]
            try:
                res.append({"company": row["company"], "ok": True, "status": f"Working · {len(f.result())} open roles"})
            except Exception as e:
                res.append({"company": row["company"], "ok": False, "status": f"Not responding · {str(e)[:60]}"})
            bar.progress(i / len(recs), f"Checked {i} of {len(recs)}")
    bar.empty()
    return pd.DataFrame(res)


def _lookup_many(names: list[str]) -> list[dict]:
    from concurrent.futures import ThreadPoolExecutor
    from agent.company_lookup import lookup

    bar = st.progress(0.0, "Looking up companies...")
    out = [None] * len(names)
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(lookup, n): i for i, n in enumerate(names)}
        for k, f in enumerate(futs, 1):
            i = futs[f]
            try:
                out[i] = f.result()
            except Exception as e:
                out[i] = {"input": names[i], "status": "unknown", "company": names[i], "candidates": [], "error": str(e)}
            bar.progress(k / len(names), f"Looked up {k} of {len(names)}")
    bar.empty()
    return out


SIZE_LABEL = {"big": "Big", "mid": "Mid-size", "small": "Small"}


def step_companies():
    from agent.company_lists import (build_user_list, default_locations, load_master, location_options,
                                     preview_counts, user_families)

    st.markdown("### Step 5 · Pick your companies")
    st.markdown('<p class="step-hint">Choose one or both. The agent watches each company\'s own job page '
                '(U.S. companies only).</p>', unsafe_allow_html=True)
    from agent.company_lists import enrich as _enrich
    df = _enrich(store.load_companies())
    t_own, t_auto = st.tabs(["Option 1 · Add your own companies", "Option 2 · Let Daily Job Agent build it"])

    # ---------- option 1: type names, we figure out the rest ----------
    with t_own:
        st.caption("Type company names, one per line. Any spelling or capitalization works. The agent finds the real "
                   "company, its job page, its size and its industry.")
        names = st.text_area("Company names", height=120, label_visibility="collapsed",
                             placeholder="warby parker\nthe realreal\ngrammerly")
        if st.button("Find these companies", type="primary", disabled=not names.strip()):
            nm = list(dict.fromkeys(n.strip() for n in names.splitlines() if n.strip()))
            st.session_state["lookups"] = _lookup_many(nm)
        res = st.session_state.get("lookups") or []
        if res:
            from agent.company_lookup import board_from_url, board_from_website, lookup, _verify, size_from
            have = set(df["company"].str.lower())
            for i, r in enumerate(res):
                with st.container(border=True):
                    stt = r.get("status")
                    if stt == "found":
                        dup = " · already on your list (its job page will be refreshed)" if r["company"].lower() in have else ""
                        st.markdown(f":material/check_circle: **{r['company']}** · {SIZE_LABEL.get(r['size'], 'Mid-size')}"
                                    + (f" · {r['industry'][:60]}" if r.get("industry") else "")
                                    + f" · {r['open_roles']} open roles{dup}")
                    elif stt == "choose":
                        st.markdown(f"**\"{r['input']}\"**: did you mean one of these?")
                        opts = [f"{c['label']}: {c['description']}" for c in r["candidates"]] + ["None of these"]
                        pick = st.radio("Pick one", opts, key=f"pick_{i}", label_visibility="collapsed")
                        if st.button("Use this", key=f"use_{i}"):
                            if pick == "None of these":
                                res[i] = {**r, "status": "skipped"}
                            else:
                                c = r["candidates"][opts.index(pick)]
                                with st.spinner("Looking it up..."):
                                    res[i] = lookup(c["label"], chosen_qid=c["qid"])
                            st.rerun()
                    elif stt == "not_us":
                        st.markdown(f":material/public_off: **{r['company']}** isn't a U.S. company, so it's skipped.")
                    elif stt == "skipped":
                        st.markdown(f"Skipped **{r['input']}**.")
                    else:
                        st.markdown(f":material/help: **{r['company']}**: couldn't find their job page automatically.")
                        u = st.text_input("Paste their careers page, or any job posting link from them", key=f"url_{i}",
                                          placeholder="https://www.warbyparker.com/careers")
                        if u and st.button("Use this link", key=f"fix_{i}"):
                            with st.spinner("Checking the link..."):
                                bd = board_from_url(u) or board_from_website(u)
                                n = _verify(bd, r["company"]) if bd else None
                            if bd and n is not None:
                                res[i] = {**r, **bd, "open_roles": n, "status": "found",
                                          "size": r.get("size") or size_from(None, n)}
                                st.rerun()
                            else:
                                st.error("That page doesn't use a job system the agent can read yet "
                                         "(it reads Greenhouse, Lever, Ashby, Workday, SmartRecruiters and Workable).")
            ready = [r for r in res if r.get("status") == "found"]
            if ready and st.button(f"Add {len(ready)} compan{'y' if len(ready) == 1 else 'ies'} to my list", type="primary"):
                rows = {r["company"].lower(): {"company": r["company"], "ats": r["ats"], "token": r["token"],
                                               "size": r["size"] or "mid", "industry": r.get("industry", "")} for r in ready}
                keep = df[~df["company"].str.lower().isin(rows)]  # companies already listed get their details refreshed
                store.save_companies(pd.concat([keep, pd.DataFrame(rows.values())], ignore_index=True))
                st.session_state.pop("lookups", None)
                mark("companies_ok")
                st.rerun()

    # ---------- option 2: built from your answers ----------
    with t_auto:
        master = load_master()
        if master is not None and len(master):
            needs = settings.get("needs_sponsorship", True)
            fams = user_families(settings["roles"])
            opts = location_options(master)
            if "list_locations" not in settings:
                settings["list_locations"] = default_locations(settings.get("locations", []), opts)
            locs = st.multiselect(
                "Where do you want to work? Pick states, cities, or both", opts,
                [l for l in settings["list_locations"] if l in opts],
                placeholder="Anywhere in the U.S.",
                help="Uses where each company actually hired entry-level people in the government data. "
                     "Leave empty for anywhere in the U.S.")
            if locs != settings["list_locations"]:
                settings["list_locations"] = locs
                store.save_settings(settings)
            n_avail, n_here, n_watch = preview_counts(master, needs, settings["industries"], settings["exclude_industries"], locs)
            st.markdown(
                f"Built from your answers:\n"
                f"- **{'Only companies that have sponsored entry-level hires' if needs else 'Every company'}** "
                f"({settings['visa_status'].split('(')[0].strip()})\n"
                f"- **Where:** {', '.join(locs) if locs else 'anywhere in the U.S.'}\n"
                f"- **Industries:** {', '.join(settings['industries']) or 'all'} (change in Step 4)\n"
                f"- **Hiring for:** {', '.join(fams) or 'your roles'}\n\n"
                f"**{min(int(settings.get('list_limit', 2000)), n_watch):,} companies go in your list, and every one of them is scanned every day.** "
                f"Only companies whose job page the agent can read are included, so none are skipped. "
                + (f"Companies that hired in your places come first. " if locs else "")
                + (f"\n\n{n_avail - n_watch:,} more matching sponsors don't have a readable job page yet. The agent keeps "
                   f"looking in the background and adds each one it finds to your list automatically, up to "
                   f"{int(settings.get('list_limit', 2000)):,}." if n_watch < min(int(settings.get('list_limit', 2000)), n_avail) else ""))
            from agent.company_lists import BUILD as _B
            if not HOSTED:
                from agent.company_lists import lookup_status, start_background_build
                _chk, _fnd, _tot = lookup_status()
                if _tot and (_chk < _tot or _B["running"]):
                    with st.container(border=True):
                        st.markdown(f"**Owner only:** job pages checked for **{_chk:,} of {_tot:,}** sponsors "
                                    f"({_fnd:,} found so far). More checked means more jobs in your daily list.")
                        if not _B["running"] and st.button("Continue finding job pages", type="primary", key="cont_lookup"):
                            start_background_build()
                            st.rerun()

                        @st.fragment(run_every=3)
                        def _lookup_live():
                            if _B["running"]:
                                st.progress(_B["frac"], _B["msg"] or "Working...")
                                st.caption("Runs in the background; keep the app open. New job pages join your list "
                                           "automatically before each search.")
                            elif _B["error"]:
                                st.error("It stopped: " + _B["error"] + ". Click the button again to continue.")
                            elif _B["done"]:
                                st.success("All sponsors checked.")
                        _lookup_live()
            if n_avail:
                limit = st.slider("List size to aim for", 100, 3000, int(settings.get("list_limit", 2000)), step=100,
                                  help="Your list grows to this size automatically as job pages are found.")
                extra = st.text_area("Any companies you definitely want in your list? (optional)",
                                     placeholder="Type names, separated by commas, e.g. Stripe, Notion, Duolingo. "
                                                 "Spelling and capitals don't matter.", height=80, key="must_have")
                keep_mine = st.checkbox("Also keep the companies already in my list", True,
                                        help="Your current list below, including any you added before.")
                if st.button(f"Build my list (aiming for {limit:,})", type="primary"):
                    settings.update(auto_list=True, list_limit=int(limit), list_locations=locs)
                    store.save_settings(settings)
                    lst = build_user_list(master, needs, settings["industries"], settings["exclude_industries"],
                                          settings["roles"], settings["size_mix"], limit, locs)
                    names = [n.strip() for n in re.split(r"[,\n;]+", extra or "") if n.strip()]
                    added, missed = [], []
                    if names:
                        for r in _lookup_many(names):
                            if r and r.get("status") == "found":
                                added.append({"company": r["company"], "ats": r["ats"], "token": r["token"],
                                              "size": r.get("size") or "mid", "industry": r.get("industry", "")})
                            else:
                                missed.append((r or {}).get("input") or (r or {}).get("company", "?"))
                    if added:  # yours go first, replacing duplicates from the built list
                        mine_new = pd.DataFrame(added)
                        lst = pd.concat([mine_new, lst[~lst["company"].str.lower().isin(mine_new["company"].str.lower())]],
                                        ignore_index=True)
                    if keep_mine:
                        mine = df[(df["ats"].astype(str).str.strip() != "")
                                  & ~df["company"].str.lower().isin(lst["company"].str.lower())]
                        lst = pd.concat([lst, mine], ignore_index=True)
                    store.save_companies(lst)
                    mark("companies_ok")
                    st.session_state["build_note"] = (added and [a["company"] for a in added], missed)
                    st.rerun()
                note = st.session_state.pop("build_note", None)
                if note:
                    got, missed = note
                    if got:
                        st.success("Added at the top of your list: " + ", ".join(got))
                    if missed:
                        st.warning("Couldn't add " + ", ".join(missed) + " automatically (spelling unclear, not a U.S. "
                                   "company, or no job page found). Fix them in the **Option 1** tab, where you can "
                                   "pick the right match or paste their careers link.")
            else:
                st.warning("No companies match. Pick more places, or remove an industry in Step 4.")
        else:
            st.markdown("This option builds a list of up to **3,000 U.S. companies** that match your work authorization, "
                        "industries and roles, using government data on which companies sponsor visas.")
            if HOSTED:
                st.info("Not ready yet. The app owner needs to load the sponsorship data once. "
                        "Until then, you're using the starter list below, and you can add companies with Option 1.")
            if not HOSTED:
                from agent.company_lists import ranked_path as _ranked
                if _ranked().exists():
                    st.markdown("**Almost there.** The government data is already read. One last click finds each company's "
                                "job page for all 8,000 sponsors. It runs in the background, so you can keep going with the other steps.")
                    from agent.company_lists import BUILD, start_background_build
                    if not BUILD["running"] and st.button("Finish building the company list", type="primary", key="finish_master"):
                        start_background_build()
                        st.rerun()

                    @st.fragment(run_every=2)
                    def _build_status():
                        if BUILD["running"]:
                            st.progress(BUILD["frac"], BUILD["msg"] or "Working...")
                            st.caption("Running in the background. You can go to other steps; just keep the app open. "
                                       "Option 2 works as soon as the first 1,000 are saved.")
                        elif BUILD["error"]:
                            st.error("It stopped: " + BUILD["error"] + ". Click the button again to continue.")
                        elif BUILD["done"]:
                            st.success("Company list ready. Refresh the page to use Option 2.")
                    _build_status()
                else:
                    st.markdown("**You're the owner, so this is one click.** Daily Job Agent downloads the government file "
                                "(about 250 MB) and builds the list. Takes 20 to 40 minutes; you can keep using your computer.")
                    if st.button("Load sponsorship data for me", type="primary", key="auto_lca"):
                        from agent.company_lists import build_master, download_dol_file
                        dl_dir = store.ROOT / "data" / "_dol"
                        dl_dir.mkdir(parents=True, exist_ok=True)
                        bar = st.progress(0.0, "Finding the newest file on the Department of Labor site...")
                        upd = lambda f, msg: bar.progress(min(max(f, 0.0), 1.0), msg)
                        try:
                            path_ = download_dol_file(dl_dir, progress=upd)
                        except Exception as e:
                            st.session_state["lca_fallback"] = str(e)
                            st.rerun()
                        bar.progress(0.0, "Reading the file (the slow part)...")
                        build_master([path_], progress=upd)
                        Path(path_).unlink(missing_ok=True)
                        st.session_state.pop("lca_fallback", None)
                        st.success("Done. Your master list is ready.")
                        st.rerun()
                    if st.session_state.get("lca_fallback"):
                        st.warning("The government site blocked the automatic download "
                                   f"({st.session_state['lca_fallback']}). Plan B takes one minute: "
                                   "[click here to download the file](https://www.dol.gov/agencies/eta/foreign-labor/performance) "
                                   "(under **LCA Programs**, pick the newest *LCA_Disclosure_Data_FY...xlsx*), then drop it below.")
                        lca = st.file_uploader("The file you downloaded", type=["xlsx", "csv"], accept_multiple_files=True,
                                               key="lca", label_visibility="collapsed")
                        if lca and st.button("Load", type="primary"):
                            from agent.company_lists import build_master
                            tmp = []
                            for f in lca:
                                p_ = store.ROOT / "data" / f"_lca_{f.name}"
                                p_.write_bytes(f.getvalue())
                                tmp.append(str(p_))
                            bar = st.progress(0.0, "Starting...")
                            try:
                                build_master(tmp, progress=lambda f, msg: bar.progress(min(max(f, 0.0), 1.0), msg))
                            finally:
                                for p_ in tmp:
                                    Path(p_).unlink(missing_ok=True)
                            st.session_state.pop("lca_fallback", None)
                            st.rerun()

    # ---------- the list itself ----------
    df = store.load_companies()
    st.markdown(f"#### Your list · {len(df):,} companies")
    counts = df["size"].value_counts()
    n_live = int(((df["ats"].astype(str).str.strip() != "") & (df["token"].astype(str).str.strip() != "")).sum())
    st.caption(f"{int(counts.get('big', 0))} big · {int(counts.get('mid', 0))} mid-size · {int(counts.get('small', 0))} small"
               f" · the agent reads job posts from {n_live:,} of them today")
    view = df[["company", "size", "industry"]].copy()
    view["size"] = view["size"].map(SIZE_LABEL).fillna("Mid-size")
    view.insert(0, "remove", False)
    def _job_page(r):
        a, t = str(r.get("ats", "")).strip().lower(), str(r.get("token", "")).strip()
        return {"greenhouse": f"https://boards.greenhouse.io/{t}", "lever": f"https://jobs.lever.co/{t}",
                "ashby": f"https://jobs.ashbyhq.com/{t}"}.get(a, t if t.startswith("http") else "")
    xl = df.copy()
    xl["job page"] = xl.apply(_job_page, axis=1)
    xl["agent reads it daily"] = xl["job page"].map(lambda u: "yes" if u else "no (add a careers link in Option 1)")
    xl["size"] = xl["size"].map(SIZE_LABEL).fillna("Mid-size")
    _buf = io.BytesIO()
    xl[["company", "size", "industry", "job page", "agent reads it daily"]].to_excel(_buf, index=False, sheet_name="My companies")
    st.download_button("Download my list (Excel)", _buf.getvalue(), "my_company_list.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", icon=":material/download:")
    ed = st.data_editor(view, hide_index=True, width="stretch", height=min(420, 38 + 35 * len(view)),
                        disabled=["company", "industry"],
                        column_config={"remove": st.column_config.CheckboxColumn("Remove", width="small"),
                                       "company": "Company", "industry": "Industry",
                                       "size": st.column_config.SelectboxColumn("Size", options=list(SIZE_LABEL.values()))})
    to_remove = int(ed["remove"].sum())
    changed_size = (ed["size"] != view["size"]).any()
    if (to_remove or changed_size) and st.button(f"Save changes" + (f" (remove {to_remove})" if to_remove else "")):
        inv = {v: k for k, v in SIZE_LABEL.items()}
        df["size"] = ed["size"].map(inv).values
        store.save_companies(df[~ed["remove"].values])
        st.rerun()
    if st.button("Continue →", type="primary"):
        mark("companies_ok")
        next_step("companies")


def _relay_owner_setup():
    """One-time, owner only: the website's own email sender, so visitors only type their email and a time."""
    from agent.mailer import relay_script
    import secrets as _sec

    st.info("One-time setup for you as the owner (about 5 minutes). After this, everyone, including you, "
            "just types an email address and a time.")
    key = _local_secret("RELAY_KEY") or _secret("RELAY_KEY")
    if not key:  # saved right away, so reloading the page can't make a new key that no longer matches the script
        key = _sec.token_urlsafe(24)
        save_secret({"RELAY_KEY": key})
    st.markdown(
        "1. **Recommended:** make a new Gmail just for the app (e.g. *dailyjobagent.yourname@gmail.com*), "
        "so emails come from it and not your personal account. Sign in to it.\n"
        "2. Open [script.google.com](https://script.google.com), click **New project**, delete what's there, "
        "and paste the code below (use the copy button at its top right).\n"
        "3. Click **Deploy → New deployment**, click the gear next to *Select type* and choose **Web app**. "
        "Set **Execute as: Me** and **Who has access: Anyone**, then **Deploy**.\n"
        "4. Click **Authorize access**, pick the account, then **Advanced → Go to project (unsafe) → Allow**. "
        "(It says unsafe only because you wrote it yourself.)\n"
        "5. Copy the **Web app URL** it shows and paste it here.")
    st.code(relay_script(key), language="javascript")
    url = st.text_input("Web app URL", placeholder="https://script.google.com/macros/s/.../exec")
    if st.button("Save", type="primary", disabled=not url.strip().startswith("https://script.google.com/")):
        save_secret({"RELAY_URL": url.strip(), "RELAY_KEY": key})
        st.rerun()
    st.caption("To use it on the public website later, add RELAY_URL and RELAY_KEY to the website's secrets "
               "(I'll walk you through it). Nothing is stored: the sent copy goes straight to Trash.")


def step_alerts():
    st.markdown("### Step 6 · Your daily email (phone alerts optional)")
    if HOSTED:
        st.markdown('<p class="step-hint">Set these up here, then the last step moves everything into your own GitHub, '
                    'which sends them every day. This website does not keep your password.</p>', unsafe_allow_html=True)
    elif settings.get("cloud_enabled"):
        st.markdown('<p class="step-hint">These now run on GitHub, so your laptop can stay off. After changing anything here, '
                    'click <b>Update my GitHub copy</b> in the last step.</p>', unsafe_allow_html=True)
    else:
        st.markdown('<p class="step-hint">These work while this computer runs the agent (start_agent.bat). To get them with your '
                    'laptop off, finish this step, then do the last step.</p>', unsafe_allow_html=True)

    from agent.mailer import relay_config, relay_script, send
    r_url, r_key = relay_config()
    if not (r_url and r_key):
        if HOSTED:
            st.warning("Email sending isn't switched on for this website yet. Check back soon.")
            return
        _relay_owner_setup()
        return

    with st.expander("Emails not arriving? (\"wrong key\")"):
        st.markdown("The app and your email sender (the Google Apps Script) must share the same key. To match them:\n"
                    "1. Sign in to the Gmail you made for the app, open [script.google.com](https://script.google.com) "
                    "and open your project.\n"
                    "2. Near the top, find the line `const KEY = '...';` and copy the text between the quotes.\n"
                    "3. Paste it here and click **Save key**.")
        fk = st.text_input("Key from your script", type="password", key="relay_fix_key")
        if st.button("Save key", disabled=not fk.strip(), key="relay_fix_btn"):
            save_secret({"RELAY_KEY": fk.strip().strip("'\"")})
            st.success("Saved. Click **Save and send me a test email** below to check.")
    with st.container(border=True):
        st.markdown("#### Where should your daily list go?")
        send_to = st.text_input("Email address", settings.get("daily_email_to", ""), placeholder="you@example.com",
                                label_visibility="collapsed")
        try:
            saved_t = _dt.time.fromisoformat(settings["daily_email_time"]) if settings.get("daily_email_time") else None
        except ValueError:
            saved_t = None
        try:
            from zoneinfo import available_timezones
            zones = sorted(z for z in available_timezones() if "/" in z and not z.startswith(("Etc/", "SystemV", "posix", "right")))
        except Exception:
            zones = ["America/Los_Angeles"]
        guess = settings.get("daily_email_tz") or getattr(st.context, "timezone", None) or "America/Los_Angeles"
        guess = guess if guess in zones else "America/Los_Angeles"
        st.markdown("#### What time every day?")
        t1, t2 = st.columns([1, 1.6])
        picked = t1.time_input("Time", value=saved_t, step=900, label_visibility="collapsed")
        tz_pick = t2.selectbox("Time zone", zones, index=zones.index(guess) if guess in zones else 0,
                               label_visibility="collapsed")
        st.caption("You'll get 30 roles with links to each company's own job page, plus a tailored resume PDF for each.")
        ok_addr = bool(re.match(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$", send_to.strip()))
        b1, b2 = st.columns(2)
        if b1.button("Save and send me a test email", type="primary", disabled=not (ok_addr and picked)):
            settings.update({"daily_email": True, "daily_email_time": picked.strftime("%H:%M"), "daily_email_tz": tz_pick,
                             "daily_email_attach_pdfs": True, "daily_email_to": send_to.strip()})
            store.save_settings(settings)
            try:
                with st.spinner("Sending..."):
                    send("you're set up", f"Your list of 30 roles will arrive every day at {picked.strftime('%H:%M')} "
                                          f"({tz_pick}).", to=send_to.strip())
                st.success(f"Test email sent to {send_to.strip()}. Check your inbox (and spam, the first time).")
            except Exception as e:
                st.error(f"Saved, but the test email failed: {str(e)[:180]}")
        if settings.get("daily_email") and b2.button("Turn daily email off"):
            mark("daily_email", False)
            st.rerun()
        if send_to.strip() and not ok_addr:
            st.caption("That doesn't look like an email address yet.")

    with st.container(border=True):
        st.markdown("#### Instant phone alerts when a matching role is posted")
        st.markdown("1. Install the free **ntfy** app on your phone.  \n"
                    "2. Make up a hard-to-guess topic name (like `richa-jobs-7f3k9q`), subscribe to it in the app, and enter it here.")
        n1, n2 = st.columns([3, 1])
        ntfy = n1.text_input("Topic name", settings.get("ntfy_topic", ""), label_visibility="collapsed",
                             placeholder="your-topic-name")
        if n2.button("Save and test", disabled=not ntfy):
            import requests as _rq
            mark("ntfy_topic", ntfy.strip())
            try:
                _rq.post(f"https://ntfy.sh/{ntfy.strip()}", data="Your job alerts are connected.".encode(), timeout=10,
                         headers={"Title": "Test alert", "Tags": "briefcase"})
                st.success("Sent. Check your phone.")
            except Exception as e:
                st.error(str(e)[:120])

    if st.button("Next →", type="primary"):
        mark("alerts_done")
        next_step("alerts")


def _to_today_button(label: str):
    st.divider()
    if st.button(label, type="primary", icon=":material/arrow_forward:", key="to_today"):
        st.session_state["page"] = "today"
        st.rerun()


def step_cloud():
    from agent import cloud
    from agent.mailer import relay_config

    st.markdown("### Step 7 · Run without your laptop (optional)")
    st.markdown('<p class="step-hint">Your agent moves into a private project in <b>your own</b> GitHub account and runs on '
                "GitHub's free computers every day, even with your laptop off. Only you can see it, and nobody else "
                "(not even whoever runs this website) keeps a copy.</p>", unsafe_allow_html=True)
    if not setup_done:
        st.warning("Finish the required steps first (AI, resume, jobs, companies).")
        return
    st.caption("This step is optional. You can skip it and come back any time.")
    r_url, r_key = relay_config()
    if not (settings.get("daily_email") and settings.get("daily_email_time") and settings.get("daily_email_to")
            and r_url and r_key):
        st.info("Set up the daily email in Step 6 first, so your GitHub copy knows where and when to send your list.")
        if st.button("Go to Step 6"):
            goto_step("alerts")
            st.rerun()
        _to_today_button("Skip for now: go to Today's list →")
        return
    if not settings.get("daily_email_tz"):
        try:
            from zoneinfo import available_timezones
            zones = sorted(z for z in available_timezones() if "/" in z and not z.startswith(("Etc/", "SystemV", "posix", "right")))
        except Exception:
            zones = ["America/Los_Angeles"]
        tz = st.selectbox("GitHub needs your time zone to send at " + settings["daily_email_time"], zones,
                          index=zones.index("America/Los_Angeles") if "America/Los_Angeles" in zones else 0)
        settings["daily_email_tz"] = tz

    with st.container(border=True):
        st.markdown("#### 1 · Make a GitHub key")
        st.markdown("No GitHub account? [Sign up free](https://github.com/signup) first (2 minutes).  \n"
                    "Then click the button. On that page, pick an expiration (**7 days** is fine), scroll to the bottom, "
                    "click **Generate token**, and copy the key that starts with `ghp_`.")
        st.link_button("Make my GitHub key", cloud.TOKEN_URL, icon=":material/key:")
        st.caption("The key is used only while this page is open and is never saved. "
                   "You can delete it on GitHub afterward; make a new one whenever you want to change settings.")
    with st.container(border=True):
        st.markdown("#### 2 · Paste it and go")
        tok = st.text_input("GitHub key", st.session_state.get("gh_token", ""), type="password",
                            placeholder="ghp_...", label_visibility="collapsed")
        label = "Update my GitHub copy" if settings.get("cloud_enabled") else "Set it up in my GitHub"
        if st.button(label, type="primary", disabled=not tok.strip()):
            st.session_state["gh_token"] = tok.strip()
            from agent.llm import local_secret as _ls
            secrets = {key_name_for(provider): api_key, "RELAY_URL": r_url, "RELAY_KEY": r_key,
                       **{k: _ls(k) for k in ("TAVILY_API_KEY", "JSEARCH_API_KEY", "GROQ_API_KEY", "BACKUP_KEYS")}}
            bar = st.progress(0.0, "Starting...")
            settings["cloud_enabled"] = True
            try:
                url = cloud.setup(tok, settings, secrets, progress=lambda f, m: bar.progress(f, m),
                                  first_run=not settings.get("cloud_repo"))
                settings["cloud_repo"] = url
                store.save_settings(settings)
                st.session_state["cloud_msg"] = url
                st.rerun()
            except Exception as e:
                settings["cloud_enabled"] = bool(settings.get("cloud_repo"))
                st.error(f"That didn't work: {str(e)[:200]}")

    if settings.get("cloud_enabled") and settings.get("cloud_repo"):
        url = settings["cloud_repo"]
        if st.session_state.pop("cloud_msg", None):
            st.success("Done. Your agent now runs on GitHub. The first run has started; your list should arrive "
                       "in about 10 to 20 minutes, then every day at " + settings["daily_email_time"] + ".")
        with st.container(border=True):
            st.markdown(f"#### Running on GitHub\n[Open my private project]({url}) · [See its runs]({url}/actions)")
            st.caption("GitHub sometimes starts scheduled runs up to 30 minutes late. "
                       + ("Phone alerts are checked every 4 hours. " if settings.get("ntfy_topic") else "")
                       + "This uses a few hundred of GitHub's 2,000 free minutes a month."
                       + ("" if HOSTED else " You don't need start_agent.bat anymore."))
            c1, c2 = st.columns(2)
            tok_ok = bool(st.session_state.get("gh_token"))
            if c1.button("Send me today's list now", disabled=not tok_ok,
                         help=None if tok_ok else "Paste your GitHub key above first"):
                try:
                    gh = cloud.GitHub(st.session_state["gh_token"]); gh.check(); gh.run_now("daily")
                    st.success("Started. Check your email in 10 to 20 minutes.")
                except Exception as e:
                    st.error(str(e)[:200])
            if c2.button("Stop running on GitHub", disabled=not tok_ok):
                try:
                    gh = cloud.GitHub(st.session_state["gh_token"]); gh.check(); gh.disable()
                    settings["cloud_enabled"] = False
                    store.save_settings(settings)
                    st.success("Stopped. To delete everything, open the project → Settings → Delete this repository.")
                    st.rerun()
                except Exception as e:
                    st.error(str(e)[:200])
    _to_today_button("Done: go to Today's list →" if settings.get("cloud_enabled") else "Skip for now: go to Today's list →")
    if HOSTED and settings.get("cloud_enabled"):
        st.caption("All saved in your GitHub. You can close this page; use **Wipe my data from this website now** in the sidebar "
                   "if you want this page's copy gone immediately.")


def page_setup():
    nav, body = st.columns([1, 3.2], gap="large")
    with nav:
        with st.container(key="stepnav"):
            st.markdown("**Setup**")
            for i, (key, label, done, optional) in enumerate(STEPS, 1):
                icon = ":material/check_circle:" if done else ":material/radio_button_unchecked:"
                cur = st.session_state["step"] == key
                if st.button(f"{i}. {label}", key=f"nav_{key}", icon=icon, width="stretch",
                             type="primary" if cur else "tertiary"):
                    st.session_state["step"] = key
                    st.rerun()
    with body:
        step = st.session_state["step"]
        if step == "finish":
            if setup_done:
                st.markdown("### You're all set")
                st.markdown("Open **Today's list** to get your first set of roles and tailored resumes.")
                if st.button("Go to Today's list →", type="primary"):
                    st.session_state["page"] = "today"
                    st.rerun()
            else:
                missing = [label for _, label, done, opt in STEPS if not done and not opt]
                st.warning("Almost there. Still to do: " + ", ".join(missing))
            return
        {"ai": step_ai, "resume": step_resume, "projects": step_projects, "targets": step_targets,
         "companies": step_companies, "alerts": step_alerts, "cloud": step_cloud}[step]()


# =====================================================================
#                             TODAY'S LIST
# =====================================================================
def _set_status(url: str, status: str, row: dict | None = None, pdf: Path | None = None):
    """Update a role's status. 'applied' keeps a copy of the exact resume used, so it's in the Tracker for good."""
    import shutil

    t = store.load_tracker()
    if not (t["url"] == url).any() and row is not None:
        t = pd.concat([t, pd.DataFrame([{"date": _dt.date.today().isoformat(), "company": row.get("company", ""),
                                         "size": row.get("size", ""), "title": row.get("title", ""),
                                         "location": row.get("location", ""), "url": url, "score": row.get("score", ""),
                                         "status": status, "pdf": str(pdf or "")}])], ignore_index=True)
    t.loc[t["url"] == url, "status"] = status
    if status in ("applied", "saved") and pdf and Path(pdf).exists():
        keep_dir = store.DATA / ("applied_resumes" if status == "applied" else "saved_resumes")
        keep_dir.mkdir(parents=True, exist_ok=True)
        dest = keep_dir / Path(pdf).name
        shutil.copyfile(pdf, dest)
        t.loc[t["url"] == url, "pdf"] = str(dest)
        t.loc[t["url"] == url, "date"] = _dt.date.today().isoformat()
    store.save_tracker(t)


def page_today():
    top1, top2 = st.columns([3, 1.3])
    top1.markdown(f"### Today's list")
    _age_txt = {24: "1 day", 48: "2 days", 72: "3 days", 168: "1 week", 336: "2 weeks"}.get(int(settings["max_age_hours"]), "2 weeks")
    top1.caption(f"{settings['daily_count']} new roles every day (posted in the last {_age_txt}), each with a resume "
                 "made for that job. For each role: **Apply** to open the job → **Resume** to download yours → "
                 "**I applied** to move it to your Tracker.")
    try:
        from agent.company_lists import BUILD as _B, auto_continue, enrich
        if not HOSTED:
            auto_continue()  # keeps finding job pages in the background while the app is open
        _all = store.load_companies()
        _live = enrich(_all)
        _n = int(((_live["ats"].astype(str).str.strip() != "") & (_live["token"].astype(str).str.strip() != "")).sum())

        @st.fragment(run_every=5)
        def _coverage():
            from agent.company_lists import enrich as _en
            lv = _en(store.load_companies())
            n = int(((lv["ats"].astype(str).str.strip() != "") & (lv["token"].astype(str).str.strip() != "")).sum())
            msg = f"Looking for jobs at **{n:,} companies** that sponsor visas."
            if _B["running"]:
                import re as _re
                m = _re.search(r"([\d,]+)\s*/\s*([\d,]+)", _B.get("msg") or "")
                msg += (f" More are being added in the background ({m.group(1)} of {m.group(2)} sponsors checked)."
                        if m else " More are being added in the background.")
            st.caption(msg)
        _coverage()
    except Exception:
        pass
    from agent.pipeline import RUN as _RUN, start_run_background, todays_list_path
    # Every day, today's list is just there: the daily job (or opening the app) builds it without any click.
    if setup_done and llm and not todays_list_path().exists() and not _RUN["running"] and not st.session_state.get("_auto_ran"):
        st.session_state["_auto_ran"] = True
        start_run_background(settings, store.load_resume(), llm)
    if top2.button("Refresh today's roles" if todays_list_path().exists() else "Find today's roles",
                   icon=":material/search:", type="primary", width="stretch", disabled=_RUN["running"]):
        st.session_state.pop("result", None)
        start_run_background(settings, store.load_resume(), llm)
        st.rerun()

    @st.fragment(run_every=2)
    def _run_live():
        if _RUN["running"]:
            b, bar = st.columns([1, 7], vertical_alignment="center")
            if b.button("Resume" if _RUN.get("paused") else "Pause", key="run_pause", width="stretch",
                        icon=":material/play_arrow:" if _RUN.get("paused") else ":material/pause:",
                        type="primary" if _RUN.get("paused") else "secondary"):
                _RUN["paused"] = not _RUN.get("paused")
                st.rerun(scope="fragment")
            bar.progress(min(max(_RUN["frac"], 0.0), 1.0),
                         "Paused. Click Resume to keep looking for roles." if _RUN.get("paused")
                         else (_RUN["msg"] or "Getting today's roles..."))
            st.session_state["_run_seen"] = True
        elif st.session_state.pop("_run_seen", False):
            if _RUN.get("result"):
                st.session_state["result"] = _RUN["result"]
            elif _RUN.get("error"):
                st.session_state["_run_error"] = _RUN["error"]
            st.rerun(scope="app")
    _run_live()
    if st.session_state.get("_run_error"):
        st.error("Today's search stopped: " + st.session_state.pop("_run_error"))
    if llm is None:
        st.warning("Your AI isn't connected, so roles won't be scored or resumes tailored. Fix it in Setup → Connect AI.")

    result = st.session_state.get("result")
    if result is None:
        days = sorted(store.OUTPUT.glob("*/todays_list.csv")) if store.OUTPUT.exists() else []
        for d in reversed(days):
            try:
                result = {"picks": pd.read_csv(d).fillna(""), "folder": str(d.parent), "ran_at": d.parent.name}
                break
            except Exception:
                continue
    from agent.pipeline import TAILOR as _T

    @st.fragment(run_every=3)
    def _tailor_live():
        if _T["running"]:
            import time as _t
            done, total = _T["done"], max(_T["total"], 1)
            el = _t.time() - _T.get("started", _t.time())
            if done:
                left = el / done * (total - done)
                eta = f"about {int(left // 60)} min {int(left % 60)} s left" if left >= 60 else f"about {int(left)} s left"
            else:
                eta = "estimating time left..."
            from agent.pipeline import pause_tailoring
            b, bar = st.columns([1, 7], vertical_alignment="center")
            if b.button("Resume" if _T.get("paused") else "Pause", width="stretch",
                        icon=":material/play_arrow:" if _T.get("paused") else ":material/pause:",
                        type="primary" if _T.get("paused") else "secondary", key="tailor_pause"):
                pause_tailoring(not _T.get("paused"))
                st.rerun(scope="fragment")
            if _T.get("paused"):
                bar.progress(done / total, f"Paused: {done}/{total} resumes done. The rest wait until you click Resume.")
            else:
                bar.progress(done / total, f"Editing your resumes: {done}/{total} done · {eta} · "
                                           f"{int(el // 60)} min {int(el % 60)} s so far")
            if _T.get("ai_out"):
                st.caption("Your keys hit today's limit partway, so some keep your base resume for now. "
                           "You can add a key when this finishes.")
            st.session_state["_tailor_seen"] = True
            if st.session_state.get("_tailor_shown") != _T["done"]:  # a resume just finished: refresh its button
                st.session_state["_tailor_shown"] = _T["done"]
                r = st.session_state.get("result")
                if r and r.get("folder"):
                    try:
                        r["picks"] = pd.read_csv(Path(r["folder"]) / "todays_list.csv").fillna("")
                    except Exception:
                        pass
                st.rerun(scope="app")
        elif st.session_state.pop("_tailor_seen", False):
            r = st.session_state.get("result")
            if r and r.get("folder"):
                try:
                    r["picks"] = pd.read_csv(Path(r["folder"]) / "todays_list.csv").fillna("")
                except Exception:
                    pass
            st.rerun(scope="app")
    _tailor_live()

    _picks0 = result["picks"] if result else None
    _has_unedited = _picks0 is not None and "resume" in _picks0 and \
        (_picks0["resume"].astype(str).str.startswith("not edited")
         | (_picks0["resume"].astype(str).str.contains("tailoring") & (not _T["running"]))).any()
    # one notice at a time: search/editing progress, else unedited resumes, else the key status
    _quiet = llm is None or _RUN["running"] or _T["running"] or _has_unedited
    if not _quiet and result and (result.get("web") or (llm and llm.notes)):
        notes = list(dict.fromkeys(x.strip() for x in [result.get("web", "")] + (llm.notes if llm else []) if x and x.strip()))
        out = [n for n in notes if "Add another key below" in n]
        using = [n for n in notes if n.startswith("Writing resumes with") or "Switching to" in n]
        from agent.pipeline import _ai_left as _al
        if out and not _al(llm):
            st.warning(out[-1])
            with st.expander("Add another key (any provider, your own free account)"):
                _key_adder("today")
        elif using:
            st.info(using[-1])

    if _RUN["running"]:  # a new search is running: don't show the old list's finished resumes as if they were new
        with st.container(border=True):
            st.markdown("**Your new list is being made.** Each role appears here with a resume marked "
                        "**Editing…** until it's tailored for that job, then **✓ Resume**.")
            st.caption("Steps: reading job boards → searching the web → scoring roles for you → editing resumes.")
        return
    if result is None:
        st.info("Click **Find today's roles** to get your first list. It takes a few minutes.")
        return

    picks = result["picks"]
    _rs = picks["resume"].astype(str) if "resume" in picks else pd.Series(dtype=str)
    _unedited = picks[_rs.str.startswith("not edited") | (_rs.str.contains("tailoring") & (not _T["running"]))] \
        if "resume" in picks else picks.head(0)
    from agent.pipeline import _ai_left, retailor_background
    _just_added = st.session_state.pop("_key_added", False)
    if len(_unedited) and not _T["running"] and _just_added and llm is not None and _ai_left(llm):
        retailor_background(result["folder"], settings, store.load_resume(), llm)  # new key: start right away
        st.rerun()
    if len(_unedited) and not _T["running"]:
        n_u = len(_unedited)
        hit_limit = _unedited["resume"].astype(str).str.contains("AI limit").any()
        can_edit = llm is not None and _ai_left(llm)
        with st.container(border=True):
            st.markdown(f"#### {n_u} of your resumes aren't tailored yet")
            if hit_limit and not can_edit:
                st.markdown("Your free AI keys reached today's limit. These roles still have your regular resume, "
                            "so you can apply now. To tailor them, add another free key below (any provider). "
                            "Your keys also reset tomorrow.")
            elif hit_limit:
                st.markdown("Your AI keys ran out while making this list. You have a key that works now.")
            else:
                st.markdown("These weren't finished last time (the app was closed, or the AI had a problem). They still have your regular "
                            "resume, so you can apply now. Click below to try again.")
            if can_edit:
                if st.button(f"Tailor {'these' if n_u > 1 else 'this'} {n_u} resume{'s' if n_u > 1 else ''} now",
                             type="primary", icon=":material/edit_note:"):
                    retailor_background(result["folder"], settings, store.load_resume(), llm)
                    st.rerun()
            with st.expander("Add another AI key", expanded=hit_limit and not can_edit):
                _key_adder("today_unedited")
            if _T.get("last_error") and not hit_limit:
                with st.expander("Technical details"):
                    st.code(_T["last_error"], language=None)
    _sent_file = store.DATA / "last_daily_email.txt"
    _sent_today = _sent_file.exists() and _sent_file.read_text().strip() == _dt.date.today().isoformat()
    if _T.get("email_error") and not _sent_today:
        st.caption("Couldn't email today's list: " + _T["email_error"] + " Check Setup → Daily email.")
    elif _sent_today and settings.get("daily_email_to"):
        st.caption(f"Today's list was emailed to {settings['daily_email_to']}.")
    elif settings.get("daily_email") and settings.get("daily_email_to") and not _T["running"] and not len(_unedited):
        from agent.pipeline import email_when_ready
        if email_when_ready(result["folder"]):  # list was ready before this version: send it now
            st.caption(f"Today's list was emailed to {settings['daily_email_to']}.")
    elif not settings.get("daily_email"):
        st.caption("Want this list by email every day? Turn it on in Setup → Daily email.")
    if "found" in result:
        st.caption(f"Checked {result['found']:,} job postings · {result['passed']:,} fit you · showing the best {len(picks)}")
    else:
        try:
            _ts = pd.Timestamp(str(result["ran_at"])[:10])
            _d = f"{_ts.strftime('%A, %B')} {_ts.day}"
        except Exception:
            _d = result["ran_at"]
        st.caption(f"List made {_d}")
    if len(picks) == 0:
        st.warning("No new matches right now. Try again later, or in Setup → Choose jobs widen the time window "
                   "or add locations, or add more companies.")
        return

    tracker = store.load_tracker()
    applied = int((tracker["url"].isin(picks["url"]) & (tracker["status"] == "applied")).sum()) if len(tracker) else 0
    st.progress(applied / len(picks), f"Applied to {applied} of {len(picks)}")

    a1, a2 = st.columns([1, 1])
    if not HOSTED and a1.button("Email me this list", icon=":material/mail:"):
        from agent.mailer import send_daily_list
        try:
            if not settings.get("daily_email_to"):
                raise RuntimeError("Add your email address in Setup → Daily email first.")
            send_daily_list(picks, result["folder"], bool(settings.get("daily_email_attach_pdfs", True)), result,
                            to=settings.get("daily_email_to"))
            st.success(f"Sent to {settings['daily_email_to']}. Check your inbox (and spam, the first time).")
        except Exception as e:
            st.error(str(e)[:200])
    with a2.popover("Polish resumes with Claude", icon=":material/auto_awesome:"):
        from agent.handoff import build_handoff, import_reply
        folder = Path(result["folder"])
        md, n = build_handoff(folder, settings["resume_rules"])
        st.markdown("1. Download this file  \n2. Drop it into a Claude chat  \n3. Paste Claude's reply below")
        st.download_button(f"Download ({n} resumes)", md, f"claude_handoff_{folder.name}.md", "text/markdown")
        pasted = st.text_area("Claude's reply", height=120)
        if st.button("Apply Claude's edits", disabled=not pasted):
            try:
                res = import_reply(folder, pasted, int(settings.get("max_projects", 3)))
                st.success(f"Updated {sum(1 for r in res if r['status'].startswith('\u2705'))} resumes.")
            except Exception as e:
                st.error(f"Couldn't read that reply: {e}")

    acted = set(tracker.loc[tracker["status"].isin(["applied", "saved", "skipped", "not interested in company"]), "url"]) \
        if len(tracker) else set()
    picks = picks[~picks["url"].isin(acted)]
    saved = tracker[tracker["status"] == "saved"] if len(tracker) else tracker
    if len(saved):
        with st.expander(f"Saved for later · {len(saved)}", icon=":material/bookmark:"):
            from agent.render import find_file as _ff
            for _, s_ in saved.iterrows():
                c1, c2, c3, c4 = st.columns([5, 1.2, 1.2, 1.2])
                c1.markdown(f"**{s_['company']}** · {s_['title']}  \n<span class='meta'>saved {s_['date']}</span>",
                            unsafe_allow_html=True)
                c2.link_button("Apply", s_["url"], width="stretch")
                sp = _ff(s_.get("pdf", "")) if s_.get("pdf") else None
                if sp:
                    c3.download_button("Resume", sp.read_bytes(), sp.name, key=f"sdl_{s_['url']}", width="stretch")
                if c4.button("I applied", key=f"sap_{s_['url']}", width="stretch"):
                    _set_status(s_["url"], "applied", None, sp)
                    st.rerun()
    if not len(picks):
        st.success("You've gone through everything on today's list. Tomorrow's arrives automatically.")
    picks = picks.assign(size=picks["size"].where(picks["size"].isin(["big", "mid", "small"]), "other"))
    for tier, label in (("big", "Big companies"), ("mid", "Mid-size companies"), ("small", "Small companies"),
                        ("other", "Other companies")):
        rows = picks[picks["size"] == tier]
        if not len(rows):
            continue
        st.markdown(f"#### {label} · {len(rows)}")
        for _, r in rows.iterrows():
            cur = tracker.loc[tracker["url"] == r["url"], "status"]
            cur = cur.iloc[-1] if len(cur) else "to apply"
            with st.container(border=True):
                l, m, rr = st.columns([5, 1, 2.2])
                done = ":material/check_circle: " if cur == "applied" else ""
                l.markdown(f"{done}**{r['company']}** · {r['title']}  \n"
                           f"<span class='meta'>{r['location']} · {r['posted']}"
                           + (f" · up to {r['salary max']}" if r.get("salary max") else "") + "</span>",
                           unsafe_allow_html=True)
                if r.get("why"):
                    l.caption(r["why"])
                if r.get("apply_site"):
                    l.caption(f"Opens the {r['apply_site']}")
                if "tailoring" in str(r.get("resume", "")):
                    l.caption(":material/edit_note: Resume: editing for this job…")
                elif "AI limit" in str(r.get("resume", "")):
                    l.caption(":material/info: Resume not edited: your AI keys hit today's limit. Add a key in Setup → 1, "
                              "then Refresh.")
                elif str(r.get("resume", "")) == "tailored":
                    kw = str(r.get("keywords mirrored", "") or "")
                    l.caption(":material/check_circle: Resume edited for this job" + (f" · keywords: {kw}" if kw else ""))
                m.metric("Fit", r["score"] if str(r["score"]) != "" else "–")
                from agent.render import MIME, find_file
                pdf = find_file(Path(result["folder"]) / Path(r["pdf"]).name)
                b1, b2 = rr.columns(2)
                b1.link_button("Apply", r["url"], icon=":material/north_east:", type="primary", width="stretch")
                if "tailoring" in str(r.get("resume", "")):
                    b2.button("Editing…", key=f"ld_{r['url']}", disabled=True, width="stretch", icon=":material/edit_note:",
                              help="Your resume is being edited for this job. The button turns on when it's ready.")
                elif str(r.get("resume", "")).startswith("not edited") and pdf:
                    b2.download_button("Base resume", pdf.read_bytes(), pdf.name, MIME.get(pdf.suffix[1:], "application/pdf"),
                                       key=f"dl_{r['url']}", width="stretch",
                                       help="Not edited for this job: your AI keys hit today's limit. It's your base resume.")
                elif pdf:
                    b2.download_button("Resume", pdf.read_bytes(), pdf.name, MIME.get(pdf.suffix[1:], "application/pdf"),
                                       key=f"dl_{r['url']}", width="stretch", icon=":material/check:",
                                       help="Edited for this job: keywords from the posting, best projects and roles.")
                s1, s2 = rr.columns(2)
                if s1.button("I applied", key=f"ap_{r['url']}", icon=":material/check:", width="stretch",
                             help="Moves it to your Tracker with the resume you used. It won't show up again."):
                    _set_status(r["url"], "applied", dict(r), pdf)
                    st.toast(f"Nice. {r['company']} is in your Tracker with this resume.")
                    st.rerun()
                if s2.button("Save", key=f"sv_{r['url']}", icon=":material/bookmark:", width="stretch",
                             help="Keeps it in Saved for later, so it doesn't disappear tomorrow."):
                    _set_status(r["url"], "saved", dict(r), pdf)
                    st.toast(f"Saved {r['company']} for later.")
                    st.rerun()
                if rr.button("Not for me", key=f"nf_{r['url']}", width="stretch", type="tertiary"):
                    _set_status(r["url"], "skipped", dict(r), pdf)
                    st.rerun()


# =====================================================================
#                             RESUME CHAT
# =====================================================================
def page_chat():
    st.markdown("### Edit a resume by chatting")
    st.caption("Ask for changes in plain English. You approve every edit. Numbers the AI makes up are blocked.")
    base = store.load_resume()
    targets = {}
    if base:
        targets["My main resume"] = None
    if store.OUTPUT.exists():
        for jf in sorted(store.OUTPUT.glob("*/*.json"), reverse=True)[:60]:
            d = json.loads(jf.read_text())
            targets[f"{d['job']['company']} · {d['job']['title']} ({jf.parent.name})"] = jf
    if not targets:
        st.info("Upload your resume in Setup first.")
        return
    choice = st.selectbox("Which resume?", list(targets))
    jf = targets[choice]
    if jf is None:
        cur_resume, job = base, None
    else:
        d = json.loads(jf.read_text())
        cur_resume, job = d["resume"], d["job"]
        st.caption(f"Tailored for [{job['title']} at {job['company']}]({job['url']}). The AI can see this posting.")

    history = st.session_state.setdefault(f"chat::{choice}", [])
    if not history:
        st.markdown("Try: *“Make my bullets more product-focused”* · *“Shorten every bullet”* · "
                    "*“What's missing for this posting?”*")
    for msg_ in history:
        with st.chat_message(msg_["role"]):
            st.markdown(msg_["content"])

    pend_key = f"pending::{choice}"
    pending = st.session_state.get(pend_key)
    if pending:
        with st.container(border=True):
            st.markdown("**Proposed changes**")
            for c in pending["changes"] or ["(see the updated resume)"]:
                st.markdown(f"- {c}")
            for n in pending["notes"]:
                st.warning(n)
            a, b = st.columns(2)
            if a.button("Apply", icon=":material/check:", type="primary"):
                from agent.render import save_rendered
                if jf is None:
                    store.save_resume(pending["resume"])
                else:
                    jf.write_text(json.dumps({"resume": pending["resume"], "job": job}, indent=2))
                    save_rendered(jf.with_suffix(""), pending["resume"], settings)
                st.session_state.pop(pend_key)
                history.append({"role": "assistant", "content": "Applied." + ("" if jf is None else " PDF updated.")})
                st.rerun()
            if b.button("Discard", icon=":material/close:"):
                st.session_state.pop(pend_key)
                st.rerun()

    from agent.render import MIME, find_file, render
    f_ = find_file(jf.with_suffix(".pdf")) if jf is not None else None
    if f_:
        st.download_button("Download this resume", f_.read_bytes(), f_.name, MIME.get(f_.suffix[1:], "application/pdf"))
    elif jf is None and base and st.button("Download this resume"):
        out = render(base, settings, base)
        data, ext = (out["pdf"], "pdf") if out["pdf"] else out["source"]
        st.download_button("Save file", data, f"resume.{ext}", MIME[ext])

    msg = st.chat_input("Tell the AI what to change...", disabled=llm is None)
    if msg:
        from agent.chat import chat_edit
        history.append({"role": "user", "content": msg})
        with st.spinner("Thinking..."):
            try:
                reply, proposed, changes, notes = chat_edit(llm, cur_resume, history[:-1], msg, job,
                                                            settings.get("resume_rules", ""))
            except Exception as e:
                reply, proposed, changes, notes = f"Sorry, that didn't work: {str(e)[:150]}", None, [], []
        history.append({"role": "assistant", "content": reply or "Here's what I'd change."})
        if proposed:
            st.session_state[pend_key] = {"resume": proposed, "changes": changes, "notes": notes}
        st.rerun()


# =====================================================================
#                               TRACKER
# =====================================================================
def page_tracker():
    st.markdown("### Your applications")
    t = store.load_tracker()
    if not len(t):
        st.info("Roles you're shown will appear here.")
        return
    c = t["status"].value_counts()
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Applied", int(c.get("applied", 0)))
    k2.metric("Saved for later", int(c.get("saved", 0)))
    k3.metric("Suggested, not acted on", int(c.get("to apply", 0)))
    k4.metric("Skipped", int(c.get("skipped", 0) + c.get("not interested in company", 0)))
    applied = t[t["status"] == "applied"]
    if len(applied):
        from agent.render import find_file as _ff
        pick = st.selectbox("Get the exact resume you applied with", applied.index,
                            format_func=lambda i: f"{applied.at[i, 'company']} · {applied.at[i, 'title']} ({applied.at[i, 'date']})")
        fp = _ff(applied.at[pick, "pdf"]) if applied.at[pick, "pdf"] else None
        if fp:
            st.download_button("Download that resume", fp.read_bytes(), fp.name, key="trk_dl")
    edited = st.data_editor(t.drop(columns=["pdf"]), width="stretch", hide_index=True,
                            column_config={"status": st.column_config.SelectboxColumn(options=STATUS),
                                           "url": st.column_config.LinkColumn("link")})
    c1, c2 = st.columns([1, 4])
    if c1.button("Save changes"):
        edited["pdf"] = t["pdf"]
        store.save_tracker(edited)
        st.success("Saved. Companies marked 'not interested' won't come back; applied ones pause for 14 days.")
    c2.download_button("Download as CSV", t.to_csv(index=False), "applications.csv", "text/csv")


{"setup": page_setup, "today": page_today, "chat": page_chat, "tracker": page_tracker}[st.session_state["page"]]()
