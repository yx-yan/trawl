"""SmartRecruiters job boards: https://api.smartrecruiters.com/v1/companies/<company>/postings

The list is asked for Singapore only (`country=sg`) and has the employment type and experience
level, which the first rules use; descriptions come from each posting's own endpoint, fetched by
`describe` only for postings that survive those rules.
"""

from __future__ import annotations

import httpx

from ..models import Posting
from .parsing import html_to_text, join_locations, parse_time

API = "https://api.smartrecruiters.com/v1/companies"
PAGE = 100
SECTIONS = ("jobDescription", "qualifications", "additionalInformation", "companyDescription")
# SmartRecruiters' words, in the ones the rules use (MyCareersFuture's)
EMPLOYMENT = {"full-time": "full time", "part-time": "part time", "contract": "contract",
              "temporary": "temporary", "internship": "internship/attachment", "freelance": "freelance"}
LEVELS = {"director": "senior management", "executive": "senior management"}


def fetch(http: httpx.Client, company: str) -> list[Posting]:
    postings: list[Posting] = []
    offset = 0
    while True:
        resp = http.get(f"{API}/{company}/postings",
                        params={"limit": PAGE, "offset": offset, "country": "sg"})
        resp.raise_for_status()
        data = resp.json()
        content = data.get("content") or []
        for job in content:
            loc = job.get("location") or {}
            kind = ((job.get("typeOfEmployment") or {}).get("label") or "").lower()
            level = ((job.get("experienceLevel") or {}).get("id") or "").lower()
            facts = {"board": company}
            if kind:
                facts["employment_types"] = [EMPLOYMENT.get(kind, kind)]
            if level in LEVELS:
                facts["position_levels"] = [LEVELS[level]]
            postings.append(Posting(
                source="smartrecruiters",
                external_id=str(job["id"]),
                company=(job.get("company") or {}).get("name") or company,
                title=job["name"].strip(),
                url=f"https://jobs.smartrecruiters.com/{company}/{job['id']}",
                location=loc.get("fullLocation") or join_locations(loc.get("city"), loc.get("country")),
                posted_at=parse_time(job.get("releasedDate")),
                facts=facts,
            ))
        offset += len(content)
        if not content or offset >= (data.get("totalFound") or 0):
            break
    return postings


def describe(http: httpx.Client, external_id: str, facts: dict) -> tuple[str, dict]:
    resp = http.get(f"{API}/{facts['board']}/postings/{external_id}")
    resp.raise_for_status()
    sections = ((resp.json().get("jobAd") or {}).get("sections")) or {}
    parts = [html_to_text((sections.get(name) or {}).get("text") or "") for name in SECTIONS]
    return "\n\n".join(p for p in parts if p), {}
