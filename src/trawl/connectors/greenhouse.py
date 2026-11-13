"""Greenhouse job boards: https://boards-api.greenhouse.io/v1/boards/<board>/jobs"""

from __future__ import annotations

import httpx

from ..models import Posting
from .parsing import html_to_text, join_locations, parse_time


def fetch(http: httpx.Client, board: str) -> list[Posting]:
    resp = http.get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs", params={"content": "true"})
    resp.raise_for_status()
    postings = []
    for job in resp.json()["jobs"]:
        offices = [o.get("name") for o in job.get("offices") or []]
        postings.append(Posting(
            source="greenhouse",
            external_id=str(job["id"]),
            company=job.get("company_name") or board,
            title=job["title"].strip(),
            url=job["absolute_url"],
            location=join_locations((job.get("location") or {}).get("name"), *offices),
            posted_at=parse_time(job.get("first_published") or job.get("updated_at")),
            # `content` is HTML that has itself been HTML-escaped
            description=html_to_text(html_to_text(job.get("content") or "")),
        ))
    return postings
