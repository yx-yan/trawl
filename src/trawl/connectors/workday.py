"""Workday job sites (https://<tenant>.<wdN>.myworkdayjobs.com/<site>), read through the JSON API
their own pages use: POST /wday/cxs/<tenant>/<site>/jobs. A board key is "<tenant>.<wdN>/<site>",
e.g. "nvidia.wd5/NVIDIAExternalCareerSite".

Large employers list thousands of jobs, so the search asks for Singapore. The list has titles and
places but no descriptions; `describe` fetches one only for postings that survive the first rules,
as for MyCareersFuture. The list's `total` is only in the first page's answer. Applying needs a
Workday account with each employer, so trawl doesn't fill these forms.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

import httpx

from ..models import Posting
from .parsing import html_to_text

PAGE = 20  # the most Workday returns per page
MAX = 400
# Without it Workday answers some networks (a cloud server's) with 429 Too Many Requests
JSON = {"Accept": "application/json"}


def _site(key: str) -> tuple[str, str]:
    """(the site's base URL, its API base) from "<tenant>.<wdN>/<site>"."""
    host, site = key.split("/", 1)
    tenant = host.split(".")[0]
    origin = f"https://{host}.myworkdayjobs.com"
    return f"{origin}/{site}", f"{origin}/wday/cxs/{tenant}/{site}"


def posted(text: str | None, now: datetime | None = None) -> datetime | None:
    """'Posted Today', 'Posted Yesterday', 'Posted 3 Days Ago', 'Posted 30+ Days Ago'."""
    if not text:
        return None
    now = now or datetime.now().astimezone()
    if "today" in text.lower():
        return now
    if "yesterday" in text.lower():
        return now - timedelta(days=1)
    m = re.search(r"(\d+)\+? days? ago", text, re.I)
    return now - timedelta(days=int(m.group(1))) if m else None


def location(job: dict) -> str:
    """The list says "2 Locations" for several; the path then names the first."""
    text = job.get("locationsText") or ""
    if not re.fullmatch(r"\d+ Locations?", text):
        return text
    parts = (job.get("externalPath") or "").split("/")
    first = parts[2].replace("-", " ") if len(parts) > 2 else ""
    return f"{first} (+ more)" if first else text


def fetch(http: httpx.Client, key: str) -> list[Posting]:
    base, api = _site(key)
    company = key.split(".")[0].capitalize()
    postings: list[Posting] = []
    total = None
    for offset in range(0, MAX, PAGE):
        resp = http.post(f"{api}/jobs", headers=JSON, json={"appliedFacets": {}, "limit": PAGE,
                                                            "offset": offset, "searchText": "Singapore"})
        resp.raise_for_status()
        data = resp.json()
        total = data.get("total") if total is None else total
        jobs = data.get("jobPostings") or []
        for job in jobs:
            path = job.get("externalPath")
            if not path:
                continue
            postings.append(Posting(
                source="workday",
                external_id=path,
                company=company,
                title=job["title"].strip(),
                url=f"{base}{path}",
                location=location(job),
                posted_at=posted(job.get("postedOn")),
                facts={"board": key},
            ))
        if not jobs or offset + PAGE >= (total or 0):
            break
    return postings


def describe(http: httpx.Client, external_id: str, facts: dict) -> tuple[str, dict]:
    """The posting's description, and its time type (Workday's "Full time" / "Part time")."""
    _, api = _site(facts["board"])
    resp = http.get(f"{api}{external_id}", headers=JSON)
    resp.raise_for_status()
    info = resp.json().get("jobPostingInfo") or {}
    extra = {"employment_types": [info["timeType"]]} if info.get("timeType") else {}
    return html_to_text(info.get("jobDescription") or ""), extra
