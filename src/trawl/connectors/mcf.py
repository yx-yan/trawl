"""MyCareersFuture, Singapore's national job portal: a search query instead of a company board.

The search endpoint has no job descriptions; `describe` fetches one posting's when it is needed.
"""

from __future__ import annotations

import httpx

from ..models import Posting
from .parsing import html_to_text, parse_time

API = "https://api.mycareersfuture.gov.sg/v2"
PAGE_SIZE = 100
PAGES = 2  # newest 200 per query; the daily run only needs what's new


def fetch(http: httpx.Client, query: str) -> list[Posting]:
    postings = []
    for page in range(PAGES):
        resp = http.post(f"{API}/search", params={"limit": PAGE_SIZE, "page": page},
                         json={"search": query, "sortBy": ["new_posting_date"]})
        resp.raise_for_status()
        results = resp.json()["results"]
        for job in results:
            company = (job.get("hiringCompany") or job.get("postedCompany") or {}).get("name", "")
            address = job.get("address") or {}
            meta = job.get("metadata") or {}
            postings.append(Posting(
                source="mcf",
                external_id=job["uuid"],
                company=company,
                title=job["title"].strip(),
                url=meta.get("jobDetailsUrl") or f"https://www.mycareersfuture.gov.sg/job/{job['uuid']}",
                location=address.get("overseasCountry") or "Singapore",
                posted_at=parse_time(meta.get("newPostingDate")),
            ))
        if len(results) < PAGE_SIZE:
            break
    return postings


def describe(http: httpx.Client, external_id: str) -> str:
    resp = http.get(f"{API}/jobs/{external_id}")
    resp.raise_for_status()
    return html_to_text(resp.json().get("description") or "")
