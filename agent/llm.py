"""One small client for any free, OpenAI-compatible model API (Gemini by default, Groq as backup)."""
from __future__ import annotations

import json
import re
import time

from openai import OpenAI

PROVIDERS = {
    "Google Gemini (free)": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "default_model": "gemini-2.5-flash",
        "key_url": "https://aistudio.google.com/apikey",
    },
    "Groq (free)": {
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "openai/gpt-oss-120b",
        "key_url": "https://console.groq.com/keys",
        "min_gap_s": 2.0,  # free tier allows about 30 requests a minute
    },
    "Claude by Anthropic (paid, your key)": {
        "base_url": "https://api.anthropic.com/v1/",
        "default_model": "claude-haiku-5-5",
        "key_url": "https://platform.claude.com/settings/keys",
        "min_gap_s": 0.5,
    },
    "OpenAI (paid, your key)": {
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-5.4-mini",
        "key_url": "https://platform.openai.com/api-keys",
        "min_gap_s": 0.5,
    },
    "OpenRouter (free and paid models)": {
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "openrouter/auto",
        "key_url": "https://openrouter.ai/keys",
        "min_gap_s": 3.0,
    },
    "Other (any OpenAI-compatible API)": {
        "base_url": "",
        "default_model": "",
        "key_url": "",
    },
}


def pick_fast_model(models: list[str], main: str) -> str:
    """A lighter model for reading/copying tasks. Falls back to the main model."""
    for want in ("gemini-flash-lite-latest", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-2.5-flash-lite",
                 "claude-haiku-5-5", "gpt-5.4-nano", "gpt-5.4-mini"):
        if want in models:
            return want
    lite = [m for m in models if "flash-lite" in m and "tts" not in m and "image" not in m]
    return lite[0] if lite else main


_SKIP = ("embed", "tts", "image", "audio", "veo", "imagen", "aqa", "live", "native", "vision", "guard", "whisper",
         "moderation", "computer", "robotics", "learnlm", "transcribe", "realtime", "dall", "sora", "codex")


def pick_working_model(models: list[str], current: str) -> str:
    """The provider no longer has `current` (error 404): choose the closest text model it does have."""
    ok = [m for m in models if not any(b in m.lower() for b in _SKIP)]
    if not ok:
        return ""
    for alias in ("gemini-flash-latest", "gemini-pro-latest"):
        if alias in ok:
            return alias
    fam = (current or "").split("-")[0].split("/")[-1].lower()
    same = [m for m in ok if fam and fam in m.lower()] or ok
    stable = [m for m in same if not any(w in m.lower() for w in ("preview", "exp", "lite", "nano", "mini-tts"))] or same
    mid = [m for m in stable if any(w in m.lower() for w in ("flash", "mini", "haiku", "small", "fast", "instant"))] or stable

    def ver(m):
        nums = re.findall(r"\d+(?:\.\d+)?", m)
        return [float(n) for n in nums] or [0.0]
    return sorted(mid, key=ver)[-1]


_MODEL_FIX: dict = {}  # (provider, key) -> model that works, found after a 404


from contextvars import ContextVar

SESSION_SECRETS: ContextVar[dict] = ContextVar("session_secrets", default={})


def key_name_for(provider: str) -> str:
    return {"Google": "GEMINI_API_KEY", "Groq": "GROQ_API_KEY", "Claude": "ANTHROPIC_API_KEY",
            "OpenAI": "OPENAI_API_KEY", "OpenRouter": "OPENROUTER_API_KEY"}.get(provider.split()[0], "CUSTOM_API_KEY")


def local_secret(name: str) -> str:
    """Read a value saved with 'Remember on this laptop' (.streamlit/secrets.toml) or an environment variable."""
    import os
    import tomllib
    from pathlib import Path

    if SESSION_SECRETS.get().get(name):  # hosted website: kept only in this visitor's session
        return SESSION_SECRETS.get()[name]
    if os.environ.get(name):
        return os.environ[name]
    p = Path(__file__).resolve().parent.parent / ".streamlit" / "secrets.toml"
    try:
        return str(tomllib.loads(p.read_text()).get(name, ""))
    except FileNotFoundError:
        return ""
    except Exception:
        return str(repair_secrets().get(name, ""))


def _lenient_secrets(text: str) -> dict:
    """Read KEY = "value" lines even when a value has unescaped quotes (older versions wrote backup keys that way)."""
    import json

    out = {}
    for line in text.splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if not k:
            continue
        try:
            out[k] = json.loads(v)  # a correctly escaped string
            continue
        except Exception:
            pass
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k] = v
    return out


def write_secrets(values: dict):
    """Write .streamlit/secrets.toml with every value safely escaped, so one value can never break the file."""
    import json
    from pathlib import Path

    p = Path(__file__).resolve().parent.parent / ".streamlit" / "secrets.toml"
    p.parent.mkdir(exist_ok=True)
    p.write_text("".join(f"{k} = {json.dumps(str(v), ensure_ascii=True)}\n" for k, v in values.items()))


def read_secrets() -> dict:
    import tomllib
    from pathlib import Path

    p = Path(__file__).resolve().parent.parent / ".streamlit" / "secrets.toml"
    if not p.exists():
        return {}
    try:
        return {k: str(v) for k, v in tomllib.loads(p.read_text()).items()}
    except Exception:
        return repair_secrets()


def repair_secrets() -> dict:
    """If the secrets file can't be read, recover every key from it and rewrite it properly."""
    import tomllib
    from pathlib import Path

    p = Path(__file__).resolve().parent.parent / ".streamlit" / "secrets.toml"
    if not p.exists():
        return {}
    text = p.read_text()
    try:
        return {k: str(v) for k, v in tomllib.loads(text).items()}
    except Exception:
        vals = _lenient_secrets(text)
        try:
            write_secrets(vals)
        except Exception:
            pass
        return vals


class LLM:
    def __init__(self, provider: str, api_key: str, model: str | None = None, min_gap_s: float | None = None,
                 base_url: str | None = None):
        cfg = PROVIDERS[provider]
        url = (base_url or cfg["base_url"]).strip()
        if not url:
            raise ValueError("Enter the API base URL for your provider.")
        # fail fast instead of hanging: 90 s per request, and our own retry loop below handles busy servers
        self.client = OpenAI(api_key=api_key, base_url=url, timeout=90, max_retries=0)
        self.provider = provider
        self.status = None  # optional callback(str) so the app can show what's happening
        self.model = model or cfg["default_model"]
        self.model = _MODEL_FIX.get((provider, api_key), self.model)
        # spacing between calls keeps free tiers under their rate limits; paid keys can go faster
        self.min_gap_s = cfg.get("min_gap_s", 4.0) if min_gap_s is None else min_gap_s
        self._last = 0.0
        import threading as _th
        self._gap_lock = _th.Lock()
        self.backup = None  # the next key to use when this one's free limit runs out (a chain: backup.backup ...)
        self.notes: list[str] = []
        self.label = f"{provider.split(' (')[0]} key …{api_key[-4:]}" if api_key and len(api_key) >= 4 else provider
        self._said_using = False

    def list_models(self) -> list[str]:
        try:
            return sorted(m.id.replace("models/", "") for m in self.client.models.list())
        except Exception:
            return []

    fast_model: str | None = None  # lighter model for copy-style jobs (reading resumes, scoring)

    def chat(self, system: str, user: str, temperature: float = 0.3, fast: bool = False, max_tokens: int = 8000) -> str:
        """fast=True asks the model to skip long "thinking" (good for copying/extraction, which needs no reasoning)."""
        from . import quota
        svc = "ai:" + self.label
        if not quota.check(svc)[0]:  # already used up today: don't waste time retrying
            if self.backup is not None:
                return self.backup.chat(system, user, temperature, fast, max_tokens)
            raise RuntimeError(self._all_used_note())
        delay = 5
        for attempt in range(6):
            with self._gap_lock:  # several resumes at once on one key: start them min_gap_s apart, run them together
                now = time.time()
                start = max(now, self._last + self.min_gap_s)
                self._last = start
            if start > now:
                time.sleep(start - now)
            try:
                kw = dict(model=(self.fast_model if fast and self.fast_model else self.model), temperature=temperature,
                          max_tokens=max_tokens,
                          messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
                if fast and not getattr(self, "_no_effort", False):
                    kw["reasoning_effort"] = "low"
                try:
                    r = self.client.chat.completions.create(**kw)
                except Exception as e:  # provider/model doesn't support reasoning_effort: retry without it
                    if "reasoning" in str(e).lower() or "unrecognized" in str(e).lower() or "unknown" in str(e).lower():
                        self._no_effort = True
                        kw.pop("reasoning_effort", None)
                        r = self.client.chat.completions.create(**kw)
                    else:
                        raise
                if not self._said_using:
                    self._said_using = True
                    note = f"Writing resumes with {self.label}."
                    if note not in self.notes and not any(n.startswith("Writing resumes with") for n in self.notes):
                        self.notes.append(note)
                return r.choices[0].message.content or ""
            except Exception as e:
                msg = str(e).lower()
                gone = "404" in msg or "notfound" in type(e).__name__.lower() or "not found" in msg \
                    or "does not exist" in msg or "no longer available" in msg or "decommissioned" in msg
                if gone and fast and self.fast_model and self.fast_model != self.model:
                    self.fast_model = None  # the light model is gone: use the main one
                    continue
                if gone and not getattr(self, "_model_fixed", False):
                    self._model_fixed = True
                    new = pick_working_model(self.list_models(), self.model)
                    if new and new != self.model:
                        note = f"{self.label}: the model {self.model} isn't offered anymore, so it now uses {new}."
                        print(f"[AI] {note}", flush=True)
                        self.model = new
                        _MODEL_FIX[(self.provider, getattr(self.client, "api_key", ""))] = new
                        continue
                if gone:  # this key/model can't be used: let the next key do the work (not marked used up)
                    if self.backup is not None:
                        return self.backup.chat(system, user, temperature, fast, max_tokens)
                    raise RuntimeError(f"{self.label} couldn't reach a working model ({str(e)[:80]}).")
                busy = any(w in msg for w in ("429", "rate", "quota", "503", "overloaded", "unavailable", "timed out", "timeout"))
                daily = any(w in msg for w in ("per day", "perday", "per_day", "(rpd)", "(tpd)", "daily", "requests per day"))
                if busy and not daily and attempt < 5:  # a per-minute limit: wait it out, it's not used up for the day
                    note = f"{self.label} is busy (per-minute limit). Waiting {min(15 * (attempt + 1), 60)} s..."
                    print(f"[AI] {note}", flush=True)
                    if self.status:
                        self.status(note)
                    time.sleep(min(15 * (attempt + 1), 60))
                    continue
                if busy and not daily:  # still busy after waiting: let the next key take this one, but don't mark it used up
                    if self.backup is not None:
                        return self.backup.chat(system, user, temperature, fast, max_tokens)
                    raise RuntimeError(f"{self.label} stayed busy; this resume keeps your base version for now.")
                if daily and self.backup is not None:
                    quota.mark_out(svc, "day")
                    note = f"{self.label}: today's free limit is used up. Switching to {self.backup.label}."
                    if note not in self.notes:
                        self.notes.append(note)
                    if self.status:
                        self.status(note)
                    return self.backup.chat(system, user, temperature, fast, max_tokens)
                if daily and self.backup is None:
                    quota.mark_out(svc, "day")
                    raise RuntimeError(self._all_used_note())
                if attempt < 4 and busy:
                    why = "the free tier's per-minute limit" if ("429" in msg or "quota" in msg or "rate" in msg) else "a busy server"
                    note = f"Hit {why}. Retrying in {delay} s (try {attempt + 2} of 5)..."
                    print(f"[AI] {note} ({str(e)[:120]})", flush=True)
                    if self.status:
                        self.status(note)
                    time.sleep(delay)
                    delay = min(delay * 2, 30)
                    continue
                raise
        return ""

    def _all_used_note(self) -> str:
        note = (f"{self.label}: today's free limit is used up, and there's no other AI key left to switch to, so the "
                "remaining resumes use your base resume. Add another key below (it resets tomorrow either way).")
        if note not in self.notes:
            self.notes.append(note)
        return note

    def chain(self) -> list["LLM"]:
        out, cur = [], self
        while cur is not None:
            out.append(cur)
            cur = cur.backup
        return out

    def chat_json(self, system: str, user: str, temperature: float = 0.3, fast: bool = False, max_tokens: int = 8000):
        text = self.chat(system + "\nRespond with valid JSON only. No markdown fences, no commentary.", user, temperature, fast,
                         max_tokens)
        return parse_json(text)


def parse_json(text: str):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        pass
    for open_c, close_c in (("{", "}"), ("[", "]")):
        a, b = text.find(open_c), text.rfind(close_c)
        if a != -1 and b > a:
            try:
                return json.loads(text[a : b + 1])
            except Exception:
                continue
    raise ValueError("The model did not return valid JSON. Try again or switch models.")


def with_backup(llm: "LLM", provider: str) -> "LLM":
    """Chain every AI key the person added after the main one, in order. Each takes over when the one before runs out."""
    from .keys import is_ai, load

    tail = llm
    for e in load():
        if not is_ai(e["provider"]) or e["key"] == getattr(llm.client, "api_key", None):
            continue
        try:
            nxt = LLM(e["provider"], e["key"], e.get("model") or None, base_url=e.get("base_url") or None)
        except Exception:
            continue
        nxt.notes = llm.notes  # one shared message list for the whole chain
        nxt.fast_model = None
        tail.backup = nxt
        tail = nxt
    return llm
