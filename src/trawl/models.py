"""The one shape every connector produces, whatever the job board looks like."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime

# Legal suffixes and noise that differ between boards for the same employer,
# e.g. "ANTHROPIC SINGAPORE PTE. LTD." on MyCareersFuture vs "Anthropic" on Greenhouse.
_COMPANY_NOISE = re.compile(
    r"\b(pte|ltd|limited|inc|llc|corp|corporation|co|singapore|sg|holdings|group|private)\b"
)


def normalize(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def normalize_company(name: str) -> str:
    return " ".join(_COMPANY_NOISE.sub(" ", normalize(name)).split())


@dataclass(frozen=True)
class Posting:
    source: str  # greenhouse | lever | ashby | mcf
    external_id: str  # the board's own id, unique within a source
    company: str
    title: str
    url: str
    location: str
    posted_at: datetime | None
    description: str = ""  # plain text; empty when the board's list endpoint doesn't include it

    @property
    def fingerprint(self) -> str:
        """Same company + same title = probably the same job seen on two boards."""
        key = f"{normalize_company(self.company)}|{normalize(self.title)}"
        return hashlib.sha1(key.encode()).hexdigest()[:16]
