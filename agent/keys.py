"""Every key a person adds, from any provider, in one place: AI keys (for resumes) and search keys (for finding roles).

Stored only where that person's own secrets live (their laptop's secrets file, or their own GitHub on the cloud
version). Keys are used in the order they were added; when one runs out for the day, the next one takes over.
"""
from __future__ import annotations

import json

from .llm import PROVIDERS, local_secret

SEARCH_KINDS = {
    "Tavily (web search)": {"url": "https://app.tavily.com/home", "engine": "tavily"},
    "JSearch (job listings)": {"url": "https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch", "engine": "jsearch"},
}
LEGACY = {"GROQ_API_KEY": "Groq (free)", "TAVILY_API_KEY": "Tavily (web search)", "JSEARCH_API_KEY": "JSearch (job listings)"}


def kinds() -> list[str]:
    """Everything a key can be for: every AI provider the app supports, plus the search services."""
    return list(PROVIDERS) + list(SEARCH_KINDS)


def key_url(kind: str) -> str:
    return (SEARCH_KINDS.get(kind) or PROVIDERS.get(kind) or {}).get("url") or (PROVIDERS.get(kind) or {}).get("key_url", "")


def is_ai(kind: str) -> bool:
    return kind in PROVIDERS


def short(kind: str) -> str:
    return kind.split(" (")[0]


def label(entry: dict) -> str:
    k = entry.get("key", "")
    return f"{short(entry['provider'])} key …{k[-4:]}" if len(k) >= 4 else short(entry["provider"])


def load() -> list[dict]:
    """Backup and extra keys, oldest first (the main AI key from Step 1 is not in this list)."""
    try:
        items = json.loads(local_secret("BACKUP_KEYS") or "[]")
    except Exception:
        items = []
    items = [i for i in items if isinstance(i, dict) and i.get("provider") and i.get("key")]
    have = {i["key"] for i in items}
    for env, kind in LEGACY.items():  # keys saved by earlier versions
        k = local_secret(env)
        if k and k not in have:
            items.append({"provider": kind, "key": k, "legacy": env})
    return items


def dumps(items: list[dict]) -> str:
    return json.dumps([{"provider": i["provider"], "key": i["key"],
                        **{k: i[k] for k in ("base_url", "model") if i.get(k)}}
                       for i in items if not i.get("legacy")])


def search_keys(engine: str) -> list[str]:
    return [i["key"] for i in load() if SEARCH_KINDS.get(i["provider"], {}).get("engine") == engine]
