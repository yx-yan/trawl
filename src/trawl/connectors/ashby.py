"""Ashby job boards: https://api.ashbyhq.com/posting-api/job-board/<board>"""

from __future__ import annotations

import httpx

from ..models import Posting
from .parsing import join_locations, parse_time


def fetch(http: httpx.Client, board: str) -> list[Posting]:
    resp = http.get(f"https://api.ashbyhq.com/posting-api/job-board/{board}")
    resp.raise_for_status()
    postings = []
    for job in resp.json()["jobs"]:
        if not job.get("isListed", True):
            continue
        secondary = [s.get("location") for s in job.get("secondaryLocations") or []]
        remote = "Remote" if job.get("isRemote") else None
        postings.append(Posting(
            source="ashby",
            external_id=job["id"],
            company=board.capitalize(),
            title=job["title"].strip(),
            url=job["jobUrl"],
            location=join_locations(job.get("location"), *secondary, remote),
            posted_at=parse_time(job.get("publishedAt")),
            description=(job.get("descriptionPlain") or "").strip(),
        ))
    return postings
