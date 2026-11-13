"""Lever job boards: https://api.lever.co/v0/postings/<board>?mode=json"""

from __future__ import annotations

import httpx

from ..models import Posting
from .parsing import join_locations, parse_time


def fetch(http: httpx.Client, board: str) -> list[Posting]:
    resp = http.get(f"https://api.lever.co/v0/postings/{board}", params={"mode": "json"})
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):  # unknown boards answer 200 with {"ok": false, ...}
        raise httpx.HTTPError(f"Lever board {board!r}: {data}")
    postings = []
    for job in data:
        cats = job.get("categories") or {}
        parts = [job.get("openingPlain"), job.get("descriptionBodyPlain") or job.get("descriptionPlain")]
        parts += [f"{lst['text']}\n{lst['content']}" for lst in job.get("lists") or []]
        parts.append(job.get("additionalPlain"))
        postings.append(Posting(
            source="lever",
            external_id=job["id"],
            company=board.capitalize(),
            title=job["text"].strip(),
            url=job["hostedUrl"],
            location=join_locations(cats.get("location"), *(cats.get("allLocations") or [])),
            posted_at=parse_time(job.get("createdAt")),
            description="\n\n".join(p.strip() for p in parts if p),
        ))
    return postings
