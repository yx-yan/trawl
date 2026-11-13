"""The daily digest: new eligible jobs as one plain-text message, sent to Telegram."""

from __future__ import annotations

import httpx

from .store import Batch, Row

TELEGRAM_LIMIT = 4096
HEADINGS = {"fde": "FDE roles", "path": "Paths into FDE"}


def render(batch: Batch) -> str:
    rows = batch.rows
    lines = [f"trawl: {len(rows)} new job{'s' if len(rows) != 1 else ''} you can apply to"
             if rows else "trawl: no new jobs you can apply to today."]
    for track, heading in HEADINGS.items():
        group = sorted((r for r in rows if r.track == track), key=lambda r: (-r.score, r.company.lower()))
        if not group:
            continue
        lines += ["", f"{heading} ({len(group)})"]
        lines += [_entry(r) for r in group]
    if batch.resurfaced:
        lines += ["", f"Better fits since your résumé changed ({len(batch.resurfaced)})"]
        lines += [_entry(r) for r in sorted(batch.resurfaced, key=lambda r: -r.score)]
    if batch.rejected:
        top = ", ".join(f"{n} {kind}" for kind, n in batch.rejected.most_common(5))
        lines += ["", f"Filtered out {sum(batch.rejected.values())}: {top}"]
    return "\n".join(lines)


def _entry(r: Row) -> str:
    score = f"{r.score} " if r.match else ""
    tags = f" [{'; '.join(r.flags)}]" if r.flags else ""
    line = f"• {score}{r.company}: {r.title}{tags}"
    if r.match:
        fit = []
        if r.match.get("strengths"):
            fit.append("+ " + ", ".join(r.match["strengths"]))
        if r.match.get("gaps"):
            fit.append("− " + ", ".join(r.match["gaps"][:3]))
        if fit:
            line += "\n  " + " · ".join(fit)
    return f"{line}\n  {r.url}"


def render_gaps(gaps: list[tuple[str, int, int]], top: int = 12) -> str:
    if not gaps:
        return "trawl: no skill gaps yet (no scored jobs)."
    lines = ["trawl: what the jobs you can apply to ask for that your résumé doesn't show", ""]
    for gap, jobs, fde in gaps[:top]:
        lines.append(f"• {gap}: {jobs} job{'s' if jobs != 1 else ''}" + (f" ({fde} FDE)" if fde else ""))
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
