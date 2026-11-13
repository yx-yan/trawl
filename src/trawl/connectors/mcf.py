"""MyCareersFuture, Singapore's national job portal: a search query instead of a company board.

The search endpoint has no job descriptions; `describe` fetches one posting's when it is needed,
along with the agency (SSIC) code and minimum years of experience, which only the detail has.
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
            salary = job.get("salary") or {}
            facts = {
                "employment_types": [e["employmentType"] for e in job.get("employmentTypes") or []],
                "position_levels": [p["position"] for p in job.get("positionLevels") or []],
            }
            if (salary.get("type") or {}).get("salaryType") == "Monthly":
                facts |= {"salary_min": salary.get("minimum"), "salary_max": salary.get("maximum")}
            postings.append(Posting(
                source="mcf",
                external_id=job["uuid"],
                company=company,
                title=job["title"].strip(),
                url=meta.get("jobDetailsUrl") or f"https://www.mycareersfuture.gov.sg/job/{job['uuid']}",
                location=address.get("overseasCountry") or "Singapore",
                posted_at=parse_time(meta.get("newPostingDate")),
                facts=facts,
            ))
        if len(results) < PAGE_SIZE:
            break
    return postings


def describe(http: httpx.Client, external_id: str) -> tuple[str, dict]:
    """The posting's description, plus the facts only the detail endpoint has."""
    resp = http.get(f"{API}/jobs/{external_id}")
    resp.raise_for_status()
    job = resp.json()
    poster = job.get("postedCompany") or {}  # an agency posting for a client is the poster
    facts = {
        "min_years": job.get("minimumYearsExperience"),
        # SSIC 78xxx: employment agencies, executive search, labour contractors
        "ssic": poster.get("ssicCode2020") or poster.get("ssicCode"),
    }
    return html_to_text(job.get("description") or ""), facts
