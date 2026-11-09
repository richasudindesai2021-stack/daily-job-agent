"""Run Daily Job Agent on the user's OWN GitHub, so it works with every laptop closed.

Nothing is stored by whoever hosts the website: this module copies the code and the user's own
settings into a private repository in the user's GitHub account, puts their AI key and Gmail app
password in that repository's encrypted secrets, and writes a schedule. GitHub's free computers then
run the agent every day. The GitHub key (token) the user pastes is used only for these calls and is
never saved.
"""
from __future__ import annotations

import base64
import time
from datetime import datetime
from pathlib import Path

import requests

from . import store

API = "https://api.github.com"
REPO_NAME = "my-daily-job-agent"
TOKEN_URL = ("https://github.com/settings/tokens/new?scopes=repo,workflow"
             "&description=Daily%20Job%20Agent%20setup")
WORKFLOW_PATH = ".github/workflows/agent.yml"
KEY_SECRETS = ["GEMINI_API_KEY", "GROQ_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
               "OPENROUTER_API_KEY", "CUSTOM_API_KEY", "TAVILY_API_KEY", "JSEARCH_API_KEY", "BACKUP_KEYS"]

# What goes into the user's repo. Personal data comes from the current workspace only.
CODE_GLOBS = ["*.py", "agent/*.py", "requirements.txt", ".streamlit/config.toml"]
DATA_FILES = ["settings.json", "resume.json", "project_bank.json", "companies.csv", "tracker.csv",
              "h1b_employers.csv", "original_resume.tex", "original_resume.docx"]
GITIGNORE = "__pycache__/\noutput/\n.streamlit/secrets.toml\n"
README = """# My Daily Job Agent (private)

This private copy runs on GitHub's free computers and emails you fresh roles every day.
Your resume and settings live only here, in your own account.

* **Runs:** the *Actions* tab shows every run. Click *Daily Job Agent → Run workflow* to run it now.
* **Change settings:** open the Daily Job Agent website and use *Load my setup from GitHub*.
* **Stop it:** *Actions → Daily Job Agent → ··· → Disable workflow*.
* **Delete everything:** *Settings → Delete this repository*.
"""


class GitHubError(RuntimeError):
    pass


class GitHub:
    def __init__(self, token: str):
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token.strip()}",
                               "Accept": "application/vnd.github+json",
                               "X-GitHub-Api-Version": "2022-11-28",
                               "User-Agent": "daily-job-agent"})
        self.login = ""

    def call(self, method: str, path: str, ok=(200, 201, 204), **kw):
        r = self.s.request(method, API + path, timeout=60, **kw)
        if r.status_code not in ok:
            try:
                msg = r.json().get("message", "")
            except Exception:
                msg = r.text[:200]
            raise GitHubError(f"{r.status_code} {msg}".strip())
        return r

    # ---------- account ----------
    def check(self) -> str:
        """Who is this token for, and can it do what we need? Returns the GitHub username."""
        try:
            r = self.call("GET", "/user")
        except GitHubError as e:
            if str(e).startswith("401"):
                raise GitHubError("GitHub didn't accept that key. Copy it again (it starts with ghp_).")
            raise
        scopes = r.headers.get("X-OAuth-Scopes")
        if scopes is not None:
            have = {s.strip() for s in scopes.split(",")}
            if "repo" not in have or "workflow" not in have:
                raise GitHubError("That key is missing permissions. Make a new one with the link above "
                                  "(it ticks 'repo' and 'workflow' for you).")
        self.login = r.json()["login"]
        return self.login

    @property
    def repo(self) -> str:
        return f"{self.login}/{REPO_NAME}"

    def repo_exists(self) -> bool:
        return self.call("GET", f"/repos/{self.repo}", ok=(200, 404)).status_code == 200

    def create_repo(self):
        self.call("POST", "/user/repos", json={
            "name": REPO_NAME, "private": True, "auto_init": True,
            "description": "My Daily Job Agent: private, runs on GitHub every day"})
        for _ in range(20):  # GitHub needs a moment before the first commit exists
            if self.call("GET", f"/repos/{self.repo}/git/ref/heads/main", ok=(200, 404, 409)).status_code == 200:
                return
            time.sleep(1.5)
        raise GitHubError("GitHub is slow to create the project. Try again in a minute.")

    # ---------- files ----------
    def default_branch(self) -> str:
        return self.call("GET", f"/repos/{self.repo}").json().get("default_branch", "main")

    def push(self, files: dict[str, bytes], message: str, delete: list[str] | None = None):
        """One commit that adds or replaces `files` (path -> bytes)."""
        br = self.default_branch()
        head = self.call("GET", f"/repos/{self.repo}/git/ref/heads/{br}").json()["object"]["sha"]
        base_tree = self.call("GET", f"/repos/{self.repo}/git/commits/{head}").json()["tree"]["sha"]
        tree = []
        for path, data in files.items():
            blob = self.call("POST", f"/repos/{self.repo}/git/blobs", json={
                "content": base64.b64encode(data).decode(), "encoding": "base64"}).json()["sha"]
            tree.append({"path": path, "mode": "100644", "type": "blob", "sha": blob})
        for path in delete or []:
            tree.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
        new_tree = self.call("POST", f"/repos/{self.repo}/git/trees",
                             json={"base_tree": base_tree, "tree": tree}).json()["sha"]
        commit = self.call("POST", f"/repos/{self.repo}/git/commits",
                           json={"message": message, "tree": new_tree, "parents": [head]}).json()["sha"]
        self.call("PATCH", f"/repos/{self.repo}/git/refs/heads/{br}", json={"sha": commit})

    def list_dir(self, path: str) -> list[str]:
        r = self.call("GET", f"/repos/{self.repo}/contents/{path}", ok=(200, 404))
        return [] if r.status_code == 404 else [x["name"] for x in r.json() if x.get("type") == "file"]

    def read(self, path: str) -> bytes | None:
        r = self.call("GET", f"/repos/{self.repo}/contents/{path}", ok=(200, 404),
                      headers={"Accept": "application/vnd.github.raw"})
        return None if r.status_code == 404 else r.content

    # ---------- secrets & runs ----------
    def set_secret(self, name: str, value: str):
        from nacl import encoding, public

        k = self.call("GET", f"/repos/{self.repo}/actions/secrets/public-key").json()
        box = public.SealedBox(public.PublicKey(k["key"].encode(), encoding.Base64Encoder()))
        enc = base64.b64encode(box.encrypt(value.encode())).decode()
        self.call("PUT", f"/repos/{self.repo}/actions/secrets/{name}",
                  json={"encrypted_value": enc, "key_id": k["key_id"]})

    def run_now(self, mode: str = "daily"):
        for _ in range(6):  # a just-pushed workflow can take a few seconds to register
            r = self.call("POST", f"/repos/{self.repo}/actions/workflows/agent.yml/dispatches",
                          ok=(204, 404, 422), json={"ref": self.default_branch(), "inputs": {"mode": mode}})
            if r.status_code == 204:
                return
            time.sleep(3)
        raise GitHubError("GitHub didn't start the run yet. Open the Actions tab and click 'Run workflow'.")

    def disable(self):
        self.call("PUT", f"/repos/{self.repo}/actions/workflows/agent.yml/disable", ok=(204, 404))

    def enable(self):
        self.call("PUT", f"/repos/{self.repo}/actions/workflows/agent.yml/enable", ok=(204, 404))

    def last_runs(self, n: int = 5) -> list[dict]:
        r = self.call("GET", f"/repos/{self.repo}/actions/runs", ok=(200, 404), params={"per_page": n})
        return [] if r.status_code == 404 else r.json().get("workflow_runs", [])


# ---------- what to upload ----------
def utc_crons(hhmm: str, tz: str) -> list[str]:
    """Cron lines (GitHub runs on UTC) for a local time, covering both daylight-saving offsets."""
    from zoneinfo import ZoneInfo

    hh, mm = [int(x) for x in hhmm.split(":")[:2]]
    out = []
    for month in (1, 7):
        local = datetime(2026, month, 15, hh, mm, tzinfo=ZoneInfo(tz))
        u = local.astimezone(ZoneInfo("UTC"))
        line = f"{u.minute} {u.hour} * * *"
        if line not in out:
            out.append(line)
    return out


def workflow_yaml(settings: dict, alert_hours: int = 4) -> str:
    crons = []
    if settings.get("daily_email") and settings.get("daily_email_time") and settings.get("daily_email_tz"):
        crons += utc_crons(settings["daily_email_time"], settings["daily_email_tz"])
    if settings.get("ntfy_topic"):
        crons.append(f"23 */{alert_hours} * * *")
    sched = "".join(f'    - cron: "{c}"\n' for c in crons)
    keys = "".join(f"          {k}: ${{{{ secrets.{k} }}}}\n" for k in KEY_SECRETS)
    return f"""# Written by Daily Job Agent. Runs on GitHub's free computers, so your laptop can stay off.
name: Daily Job Agent
on:
{('  schedule:' + chr(10) + sched) if sched else ''}  workflow_dispatch:
    inputs:
      mode:
        description: "daily = build and email today's list now, auto = whatever is due"
        default: daily
permissions:
  contents: write
concurrency:
  group: daily-job-agent
  cancel-in-progress: false
jobs:
  run:
    runs-on: ubuntu-latest
    timeout-minutes: 45
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - run: pip install -r requirements.txt
      - name: LaTeX (only if your resume is a .tex file)
        if: hashFiles('data/original_resume.tex') != ''
        run: sudo apt-get update -qq && sudo apt-get install -y -qq --no-install-recommends texlive-latex-base texlive-latex-recommended texlive-latex-extra texlive-fonts-recommended
      - name: LibreOffice (only if your resume is a Word file)
        if: hashFiles('data/original_resume.docx') != ''
        run: sudo apt-get update -qq && sudo apt-get install -y -qq --no-install-recommends libreoffice-writer
      - name: Run the agent
        run: python watch.py --cloud "${{{{ inputs.mode || 'auto' }}}}"
        env:
{keys}          GMAIL_USER: ${{{{ secrets.GMAIL_USER }}}}
          GMAIL_APP_PASSWORD: ${{{{ secrets.GMAIL_APP_PASSWORD }}}}
          RELAY_URL: ${{{{ secrets.RELAY_URL }}}}
          RELAY_KEY: ${{{{ secrets.RELAY_KEY }}}}
      - name: Keep today's resumes for 7 days (Actions tab, bottom of the run)
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: todays-list
          path: output/
          retention-days: 7
          if-no-files-found: ignore
      - name: Remember what was already sent
        if: always()
        run: |
          git config user.name "daily-job-agent"
          git config user.email "daily-job-agent@users.noreply.github.com"
          git add -A data
          git diff --cached --quiet || (git commit -q -m "agent: update progress" && git pull -q --rebase && git push -q)
"""


def code_files() -> dict[str, bytes]:
    root = store.ROOT
    out = {}
    for g in CODE_GLOBS:
        for p in root.glob(g):
            if p.is_file() and "secrets" not in p.name:
                out[p.relative_to(root).as_posix()] = p.read_bytes()
    req = out.get("requirements.txt", b"").decode()
    if "pynacl" not in req.lower():
        out["requirements.txt"] = (req.rstrip() + "\npynacl>=1.5\n").encode()
    out[".gitignore"] = GITIGNORE.encode()
    return out


def data_files() -> dict[str, bytes]:
    out = {}
    for name in DATA_FILES:
        p = store.DATA / name
        if p.exists():
            out[f"data/{name}"] = p.read_bytes()
    return out


def setup(token: str, settings: dict, secrets: dict[str, str], progress=None, first_run: bool = True) -> str:
    """Create or update the user's private repo. Returns the repo's web address."""
    say = progress or (lambda f, m: None)
    gh = GitHub(token)
    say(0.05, "Checking your GitHub key...")
    gh.check()
    new = not gh.repo_exists()
    if new:
        say(0.15, "Creating your private project on GitHub...")
        gh.create_repo()
    say(0.35, "Copying the agent and your settings...")
    files = {**code_files(), **data_files(), WORKFLOW_PATH: workflow_yaml(settings).encode()}
    if new:
        files["README.md"] = README.encode()
    else:
        files.pop("data/tracker.csv", None)  # the cloud copy keeps its own history
    old = [f"data/{n}" for n in ("original_resume.tex", "original_resume.docx") if f"data/{n}" not in files]
    present = set(gh.list_dir("data")) if not new else set()
    gh.push(files, "Daily Job Agent: " + ("first setup" if new else "update settings"),
            delete=[p for p in old if p.split("/")[-1] in present])
    try:
        gh.enable()
    except GitHubError:
        pass
    say(0.7, "Locking your keys in GitHub's encrypted storage...")
    for name, value in secrets.items():
        if value:
            gh.set_secret(name, value)
    if first_run:
        say(0.9, "Starting the first run...")
        try:
            gh.run_now("daily")
        except GitHubError:
            pass
    say(1.0, "Done")
    return f"https://github.com/{gh.repo}"


def load_into_workspace(token: str) -> int:
    """Copy the user's settings back from their GitHub into this session's workspace."""
    gh = GitHub(token)
    gh.check()
    if not gh.repo_exists():
        raise GitHubError(f"No '{REPO_NAME}' project in your GitHub yet. Finish setup first.")
    store.DATA.mkdir(parents=True, exist_ok=True)
    n = 0
    for name in gh.list_dir("data"):
        if name in DATA_FILES:
            data = gh.read(f"data/{name}")
            if data is not None:
                (store.DATA / name).write_bytes(data)
                n += 1
    return n
