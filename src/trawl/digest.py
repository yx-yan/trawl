"""The daily digest: new title-matched jobs as one plain-text message, sent to Telegram."""

from __future__ import annotations

import httpx

from .store import Row

TELEGRAM_LIMIT = 4096
HEADINGS = {"core": "FDE-like titles", "adjacent": "Adjacent titles"}


def render(rows: list[Row]) -> str:
    if not rows:
        return "trawl: no new matching jobs today."
    lines = [f"trawl: {len(rows)} new matching job{'s' if len(rows) != 1 else ''}"]
    for tier, heading in HEADINGS.items():
        group = sorted((r for r in rows if r.title_tier == tier), key=lambda r: (r.company.lower(), r.title))
        if not group:
            continue
        lines += ["", f"{heading} ({len(group)})"]
        for r in group:
            lines.append(f"• {r.company}: {r.title} ({r.location})\n  {r.url}")
    return "\n".join(lines)


def split(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Split on line boundaries so no job entry is cut in half."""
    parts: list[str] = []
    current = ""
    for line in text.split("\n"):
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit and current:
            parts.append(current)
            current = line[:limit]
        else:
            current = candidate[:limit]
    if current:
        parts.append(current)
    return parts


def send_telegram(text: str, token: str, chat_id: str, http: httpx.Client | None = None) -> None:
    http = http or httpx.Client(timeout=30)
    for part in split(text):
        resp = http.post(f"https://api.telegram.org/bot{token}/sendMessage",
                         json={"chat_id": chat_id, "text": part, "disable_web_page_preview": True})
        resp.raise_for_status()
