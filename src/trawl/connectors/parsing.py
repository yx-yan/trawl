"""Small parsing helpers shared by the connectors."""

from __future__ import annotations

import html
import re
from datetime import datetime

_TAG = re.compile(r"<[^>]+>")
_BLOCK = re.compile(r"</?(p|div|br|li|h[1-6]|ul|ol|tr)\b[^>]*>", re.I)


def html_to_text(markup: str) -> str:
    text = _BLOCK.sub("\n", markup)
    text = html.unescape(_TAG.sub("", text))
    lines = (" ".join(line.split()) for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def parse_time(value: str | int | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, int):  # Lever: epoch milliseconds
        return datetime.fromtimestamp(value / 1000).astimezone()
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def join_locations(*names: str | None) -> str:
    seen: list[str] = []
    for name in names:
        if name and name not in seen:
            seen.append(name)
    return " | ".join(seen)
