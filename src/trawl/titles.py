"""Title seeds for FDE-like roles (see docs/roles.md).

Titles are only a first pass: the same work hides under many names, so from M1 on every
posting in a wanted location is classified by its description, and these tiers just decide
what the daily digest shows before that exists.
"""

from __future__ import annotations

import re
from typing import Literal

Tier = Literal["core", "adjacent"]

CORE = [
    r"forward[\s-]*deployed",
    r"\bfde\b",
    r"applied ai",
    r"ai applied",
    r"\bagent (deployment )?engineer",
    r"deployment (engineer|strategist)",
    r"ai solutions? (engineer|architect)",
    r"solutions? (engineer|architect).*\b(ai|genai|gen ai|llm|ml)\b",
    r"\b(ai|genai|llm)\b.*solutions? (engineer|architect)",
    r"\bai deployment",
]

ADJACENT = [
    r"\bapplied engineer",
    r"solutions? (engineer|architect)",
    r"\b(implementation|integration|professional services|field|customer) engineer",
    r"customer success engineer",
    r"product engineer.*customer",
    r"\b(ai|genai|gen ai|llm) (engineer|developer|consultant)",
    r"ai agent (developer|engineer)",
    r"\b(sales|pre-?sales) engineer",
]

# Not engineering roles, even when the team is called "Forward Deployed Engineering"
EXCLUDE = r"\b(manager|head of|director|vp|vice president|recruit\w*|account executive)\b"

_CORE = [re.compile(p) for p in CORE]
_ADJACENT = [re.compile(p) for p in ADJACENT]
_EXCLUDE = re.compile(EXCLUDE)


def title_tier(title: str) -> Tier | None:
    t = title.lower()
    if _EXCLUDE.search(t):
        return None
    if any(p.search(t) for p in _CORE):
        return "core"
    if any(p.search(t) for p in _ADJACENT):
        return "adjacent"
    return None


def in_locations(location: str, wanted: list[str]) -> bool:
    loc = location.lower()
    return any(w.lower() in loc for w in wanted)
